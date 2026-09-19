"""ASGI boundary checks run before Starlette constructs a URL.
These are defense-in-depth; dependency advisory scans remain a release gate.
"""
import re
from starlette.responses import JSONResponse
class RequestBoundary:
    def __init__(self,app): self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']!='http': return await self.app(scope,receive,send)
        headers=scope.get('headers',[]);hosts=[v for k,v in headers if k.lower()==b'host']
        path=scope.get('path','');method=scope.get('method','')
        error=None;status=400
        if len(hosts)!=1 or not re.fullmatch(rb'[A-Za-z0-9.-]+(?::[0-9]{1,5})?',hosts[0]): error='Invalid Host header.'
        elif not path.startswith('/') or path.startswith('//') or any(ord(x)<32 for x in path) or '\\' in path: error='Invalid request path.'
        elif sum(len(k)+len(v) for k,v in headers)>16384 or len(scope.get('query_string',b''))>8192: error='Request headers or query too large.';status=431
        elif method not in ('GET','HEAD','POST','PUT','PATCH','DELETE','OPTIONS'): error='Method not allowed.';status=405
        elif any(x.startswith('.') and x not in ('.well-known',) for x in path.split('/')) or path.startswith(('/data/','/server/','/scripts/')): error='Not found.';status=404
        for k,v in headers:
            if k.lower()==b'content-type' and v.lower().startswith(b'application/x-www-form-urlencoded'):
                error='Use JSON or the documented multipart upload.';status=415
            if k.lower()==b'range' and not re.fullmatch(rb'bytes=[0-9]*-[0-9]*',v): error='Unsupported Range header.';status=416
        if error:
            response=JSONResponse({'detail':error},status_code=status,headers={
                'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
                'X-Frame-Options':'DENY','Content-Security-Policy':"default-src 'none'; frame-ancestors 'none'",'Referrer-Policy':'no-referrer'})
            return await response(scope,receive,send)
        await self.app(scope,receive,send)
