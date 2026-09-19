"""Bound incoming bytes, including chunked requests without Content-Length."""
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse


class BodySizeLimit:
    def __init__(self, app, limit=26*1024*1024):
        self.app=app
        self.limit=limit

    async def __call__(self, scope, receive, send):
        if scope['type']!='http':
            return await self.app(scope,receive,send)
        content_type=next((v for k,v in scope.get('headers',[]) if k.lower()==b'content-type'),b'')
        limit=self.limit if content_type.lower().startswith(b'multipart/form-data') else 256*1024
        used=0
        exceeded=False
        emitted=False
        async def bounded_receive():
            nonlocal used,exceeded
            message=await receive()
            if message['type']=='http.request':
                used+=len(message.get('body',b''))
                if used>limit:
                    exceeded=True
                    raise HTTPException(status_code=413,detail='Request body exceeds the permitted size.')
            return message
        async def limit_response():
            nonlocal emitted
            if not emitted:
                emitted=True
                await JSONResponse({'detail':'Request body exceeds the permitted size.'},status_code=413,
                                   headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})(scope,receive,send)
        async def bounded_send(message):
            # Body-parser middleware may translate its wrapped exception into 400.
            # Preserve a deterministic 413 and do not send the parser's body.
            if exceeded:
                await limit_response()
            else:
                await send(message)
        try:
            await self.app(scope,bounded_receive,bounded_send)
        except Exception:
            if not exceeded:raise
            await limit_response()
