"""Private customer conversations, customer history and completed-job reviews.
All owner reads are tenant scoped. Customer access requires an unguessable existing
booking or quote capability. Message bodies never grant money or approval state.
"""
import json
from typing import Literal
from fastapi import Depends, HTTPException, Query, Request
from pydantic import EmailStr, Field
from . import branding, core, models
from .db import db, uid, now, packed, audit, queue_mail
from .security import decrypt, digest, rate_limit


class Message(models.Model):
    body: str = Field(min_length=1, max_length=4000)
    idempotency_key: str = Field(pattern=r'^[a-zA-Z0-9-]{20,64}$')


class Review(models.Model):
    rating: int = Field(ge=1, le=5, strict=True)
    comment: str = Field(default='', max_length=2000)


class Notes(models.Model):
    email: EmailStr
    notes: str = Field(default='', max_length=4000)
    revision: int = Field(default=0, ge=0, strict=True)


def quote_access(c, raw):
    if len(raw)>128:
        raise HTTPException(404, 'This private quote link is invalid.')
    q=c.execute('SELECT * FROM quotes WHERE token_hash=?',(digest(raw),)).fetchone()
    if not q:
        raise HTTPException(404, 'This private quote link is invalid. Ask the detailer for your link.')
    shop=c.execute('SELECT * FROM shops WHERE id=?',(q['shop_id'],)).fetchone()
    if not shop or shop['is_demo']:
        raise HTTPException(404, 'Conversation not found.')
    return q, shop


def thread(c, kind, identifier, shop_id):
    table='bookings' if kind=='booking' else 'quotes'
    row=c.execute('SELECT * FROM '+table+' WHERE id=? AND shop_id=?',(identifier, shop_id)).fetchone()
    if not row:
        raise HTTPException(404, 'This customer conversation was not found in your workspace.')
    return row


def messages(c, kind, identifier, shop_id, before=0):
    column='booking_id' if kind=='booking' else 'quote_id'
    rows=c.execute('SELECT id,sender,body,created_at,read_at FROM customer_messages WHERE '+column+
                   '=? AND shop_id=?'+(' AND rowid<?' if before else '')+' ORDER BY rowid DESC LIMIT 101',
                   (identifier,shop_id,before) if before else (identifier,shop_id)).fetchall()
    has_more=len(rows)>100
    result=[dict(x) for x in reversed(rows[:100])]
    cursor=None
    if has_more and result:
        cursor=c.execute('SELECT rowid FROM customer_messages WHERE id=?',(result[0]['id'],)).fetchone()[0]
    return {'messages':result,'next_before':cursor}


def send(c, kind, row, shop, sender, body):
    column='booking_id' if kind=='booking' else 'quote_id'
    prior=c.execute('SELECT * FROM customer_messages WHERE shop_id=? AND sender=? AND idempotency_key=?',
                    (shop['id'],sender,body.idempotency_key)).fetchone()
    if prior:
        if prior[column]!=row['id'] or prior['body']!=body.body:
            raise HTTPException(409, 'This message attempt changed. Refresh the conversation before sending again.')
        return {'id':prior['id'],'message':'Message already sent.'}
    identifier=uid()
    c.execute('INSERT INTO customer_messages(id,shop_id,booking_id,quote_id,sender,body,idempotency_key,created_at) VALUES(?,?,?,?,?,?,?,?)',
              (identifier,shop['id'],row['id'] if kind=='booking' else None,row['id'] if kind=='quote' else None,
               sender,body.body,body.idempotency_key,now()))
    if sender=='owner':
        raw=decrypt(row['manage_encrypted'] if kind=='booking' else row['token_encrypted'])
        url=branding.public_origin(shop)+('/manage/' if kind=='booking' else '/request/')+raw
        to=row['email'];reply_to=None
        subject='New message about your '+kind+' | '+row['id'][:8].upper()
    else:
        from . import config
        url=config.BASE_URL+'/app/messages'
        to=core.settings(shop)['contact_email'];reply_to=row['email']
        subject='Customer replied | '+row['customer_name']+' | '+row['id'][:8].upper()
    queue_mail(c,'chat:'+identifier,to,subject,
               'You have a new private message about your '+kind+'. Read and reply inside Kontracts:\n'+url+
               '\n\nEmail replies are not added to the app conversation. Open the link to respond.',
               shop['id'],reply_to=reply_to)
    audit(c,shop['id'],'message.sent.'+sender,row['id'])
    return {'id':identifier,'message':'Message sent. An email notification has been queued.'}


def customer_summary(c, shop_id):
    # Latest contact wins; grouping is case insensitive and includes quote-only leads.
    rows=c.execute('''SELECT customer_name,email,phone,created_at,start_ts,status,total,'booking' AS kind,id FROM bookings WHERE shop_id=?
       UNION ALL SELECT customer_name,email,phone,created_at,NULL,status,0,'quote',id FROM quotes WHERE shop_id=?
       ORDER BY created_at DESC''',(shop_id,shop_id)).fetchall()
    groups={}
    for r in rows:
        key=r['email'].lower()
        item=groups.setdefault(key,{'email':key,'name':r['customer_name'],'phone':r['phone'],'bookings':0,
                         'quotes':0,'completed_value':0,'last_booking':None,'last_activity':r['created_at'],
                         'needs_approval':0,'unread_messages':0})
        if r['kind']=='booking':
            item['bookings']+=1
            item['last_booking']=max(item['last_booking'] or 0,r['start_ts'])
            item['completed_value']+=r['total'] if r['status']=='completed' else 0
            item['needs_approval']+=int(r['status']=='pending_approval')
        else:
            item['quotes']+=1
    unread=c.execute('''SELECT lower(COALESCE(b.email,q.email)) AS email,count(*) AS n FROM customer_messages m
      LEFT JOIN bookings b ON b.id=m.booking_id LEFT JOIN quotes q ON q.id=m.quote_id
      WHERE m.shop_id=? AND m.sender='customer' AND m.read_at IS NULL GROUP BY lower(COALESCE(b.email,q.email))''',(shop_id,)).fetchall()
    for r in unread:
        if r['email'] in groups:groups[r['email']]['unread_messages']=r['n']
    return list(groups.values())


def install(app, owner, session, owned_booking, managed, ip):
    def public_row(c,kind,raw):
        return managed(c,raw) if kind=='booking' else quote_access(c,raw)

    # Explicit routes prevent a caller from choosing an arbitrary table or role.
    def owner_get(kind):
        def handler(identifier: str, before: int = Query(0,ge=0), shop=Depends(owner)):
            with db() as c:
                row=thread(c,kind,identifier,shop['id'])
                return {**messages(c,kind,identifier,shop['id'],before),'customer_name':row['customer_name'],
                        'kind':kind,'id':identifier,'status':row['status']}
        return handler
    def owner_post(kind):
        def handler(identifier: str, body: Message, shop=Depends(owner)):
            rate_limit('owner-chat:'+shop['id'],120,3600)
            with db(True) as c:return send(c,kind,thread(c,kind,identifier,shop['id']),shop,'owner',body)
        return handler
    def public_get(kind):
        def handler(raw: str, request: Request, before: int = Query(0,ge=0)):
            rate_limit('read-chat:'+ip(request),240,600)
            with db() as c:
                row,shop=public_row(c,kind,raw)
                return messages(c,kind,row['id'],shop['id'],before)
        return handler
    def public_post(kind):
        def handler(raw: str, body: Message, request: Request):
            rate_limit('send-chat-ip:'+ip(request),60,600)
            with db(True) as c:
                row,shop=public_row(c,kind,raw)
                # Apply a per-capability limit outside the message transaction.
            rate_limit('send-chat-link:'+digest(raw),30,600)
            with db(True) as c:
                row,shop=public_row(c,kind,raw)
                return send(c,kind,row,shop,'customer',body)
        return handler
    def mark_read(kind,public=False):
        if public:
            def handler(raw: str, request: Request):
                rate_limit('read-marker:'+ip(request),240,600)
                with db(True) as c:
                    row,shop=public_row(c,kind,raw)
                    col='booking_id' if kind=='booking' else 'quote_id'
                    c.execute('UPDATE customer_messages SET read_at=? WHERE '+col+"=? AND shop_id=? AND sender='owner' AND read_at IS NULL",(now(),row['id'],shop['id']))
                return {'ok':True}
        else:
            def handler(identifier: str, shop=Depends(owner)):
                with db(True) as c:
                    row=thread(c,kind,identifier,shop['id']);col='booking_id' if kind=='booking' else 'quote_id'
                    c.execute('UPDATE customer_messages SET read_at=? WHERE '+col+"=? AND shop_id=? AND sender='customer' AND read_at IS NULL",(now(),row['id'],shop['id']))
                return {'ok':True}
        return handler
    for kind,plural,public in [('booking','bookings','manage'),('quote','quotes','quote-requests')]:
        base='/api/'+plural+'/{identifier}/messages'
        app.add_api_route(base,owner_get(kind),methods=['GET'],name='owner_'+kind+'_messages')
        app.add_api_route(base,owner_post(kind),methods=['POST'],status_code=201,name='owner_send_'+kind)
        app.add_api_route(base+'/read',mark_read(kind),methods=['POST'],name='owner_read_'+kind)
        base='/api/public/'+public+'/{raw}/messages'
        app.add_api_route(base,public_get(kind),methods=['GET'],name='customer_'+kind+'_messages')
        app.add_api_route(base,public_post(kind),methods=['POST'],status_code=201,name='customer_send_'+kind)
        app.add_api_route(base+'/read',mark_read(kind,True),methods=['POST'],name='customer_read_'+kind)

    @app.get('/api/public/quote-requests/{raw}')
    def public_request(raw: str, request: Request):
        rate_limit('quote-view:'+ip(request),120,600)
        with db() as c:
            q,shop=quote_access(c,raw)
            return {'business':shop['name'],'status':q['status'],'customer_name':q['customer_name'],
                    'notes':q['notes'],'vehicle_notes':q['vehicle_notes'],'zip':q['zip'],
                    'offer_available':q['status']=='offered' and q['expires_at']>now(),
                    'booking_url':'/quote/'+raw if q['status']=='offered' and q['expires_at']>now() else None}

    @app.get('/api/conversations')
    def inbox(shop=Depends(owner)):
        with db() as c:
            rows=c.execute('''SELECT m.*,COALESCE(b.customer_name,q.customer_name) AS customer_name,
               COALESCE(b.status,q.status) AS status FROM customer_messages m
               LEFT JOIN bookings b ON b.id=m.booking_id LEFT JOIN quotes q ON q.id=m.quote_id
               WHERE m.shop_id=? AND m.rowid IN (SELECT MAX(rowid) FROM customer_messages WHERE shop_id=? GROUP BY booking_id,quote_id)
               ORDER BY m.rowid DESC LIMIT 200''',(shop['id'],shop['id'])).fetchall()
            result=[]
            for r in rows:
                kind='booking' if r['booking_id'] else 'quote';identifier=r['booking_id'] or r['quote_id'];col=kind+'_id'
                unread=c.execute('SELECT count(*) FROM customer_messages WHERE shop_id=? AND '+col+"=? AND sender='customer' AND read_at IS NULL",(shop['id'],identifier)).fetchone()[0]
                result.append({'id':identifier,'kind':kind,'customer_name':r['customer_name'],'status':r['status'],
                               'last_message':r['body'][:180],'updated_at':r['created_at'],'unread':unread})
            return {'conversations':result,'limit':200}

    @app.get('/api/customers/detail')
    def customer_detail(email: EmailStr, shop=Depends(owner)):
        with db() as c:
            bookings=c.execute('SELECT * FROM bookings WHERE shop_id=? AND lower(email)=? ORDER BY start_ts DESC',(shop['id'],str(email).lower())).fetchall()
            quotes=c.execute('SELECT id,customer_name,phone,created_at,status,vehicle_notes,notes FROM quotes WHERE shop_id=? AND lower(email)=? ORDER BY created_at DESC',(shop['id'],str(email).lower())).fetchall()
            if not bookings and not quotes:raise HTTPException(404,'Customer not found in your workspace.')
            from .app import serialize_booking
            note=c.execute('SELECT notes,revision FROM customer_notes WHERE shop_id=? AND email=?',(shop['id'],str(email).lower())).fetchone()
            reviews=c.execute('SELECT r.* FROM job_reviews r JOIN bookings b ON b.id=r.booking_id WHERE r.shop_id=? AND lower(b.email)=?',(shop['id'],str(email).lower())).fetchall()
            return {'email':str(email).lower(),'bookings':[serialize_booking(b) for b in bookings],
                    'quotes':[dict(q) for q in quotes],'notes':note['notes'] if note else '',
                    'revision':note['revision'] if note else 0,'reviews':[dict(r) for r in reviews]}

    @app.put('/api/customers/notes')
    def update_notes(body: Notes, shop=Depends(owner)):
        with db(True) as c:
            email=str(body.email).lower()
            exists=c.execute('SELECT 1 FROM bookings WHERE shop_id=? AND lower(email)=? UNION ALL SELECT 1 FROM quotes WHERE shop_id=? AND lower(email)=? LIMIT 1',(shop['id'],email,shop['id'],email)).fetchone()
            if not exists:raise HTTPException(404,'Customer not found in your workspace.')
            prior=c.execute('SELECT revision FROM customer_notes WHERE shop_id=? AND email=?',(shop['id'],email)).fetchone()
            revision=prior['revision'] if prior else 0
            if body.revision!=revision:raise HTTPException(409,'These notes changed in another tab. Reopen the customer before saving.')
            c.execute('INSERT INTO customer_notes VALUES(?,?,?,?,?) ON CONFLICT(shop_id,email) DO UPDATE SET notes=excluded.notes,revision=excluded.revision,updated_at=excluded.updated_at',
                      (shop['id'],email,body.notes,revision+1,now()))
        return {'message':'Private customer notes saved.','revision':revision+1}

    @app.post('/api/bookings/{identifier}/request-review')
    def request_review(identifier: str, shop=Depends(owner)):
        rate_limit('request-review:'+shop['id'],60,3600)
        with db(True) as c:
            b=owned_booking(c,identifier,shop['id'])
            if b['status']!='completed':raise HTTPException(409,'Request a review after the completed job and customer signature are recorded.')
            if c.execute('SELECT 1 FROM job_reviews WHERE booking_id=?',(identifier,)).fetchone():
                return {'message':'A review was already requested. The customer can use their private booking link.'}
            c.execute('INSERT INTO job_reviews(booking_id,shop_id,requested_at) VALUES(?,?,?)',(identifier,shop['id'],now()))
            link=branding.public_origin(shop)+'/manage/'+decrypt(b['manage_encrypted'])+'#review'
            queue_mail(c,'review:'+identifier,b['email'],'How was your detail? | '+identifier[:8].upper(),
                       'Thank you for choosing '+shop['name']+'. Please share an honest review of your completed service:\n'+link+
                       '\nAll ratings are welcome. Your review is shared privately with the detailer.',shop['id'])
            audit(c,shop['id'],'review.requested',identifier)
        return {'message':'Review request queued to the customer. All ratings are welcome.'}

    @app.get('/api/public/manage/{raw}/review')
    def view_review(raw: str):
        with db() as c:
            b,shop=managed(c,raw)
            r=c.execute('SELECT requested_at,rating,comment,submitted_at FROM job_reviews WHERE booking_id=? AND shop_id=?',(b['id'],shop['id'])).fetchone()
            return {'review':dict(r) if r else None,'can_review':bool(r and not r['submitted_at'] and b['status']=='completed')}

    @app.post('/api/public/manage/{raw}/review')
    def submit_review(raw: str, body: Review, request: Request):
        rate_limit('review:'+ip(request),20,3600)
        with db(True) as c:
            b,shop=managed(c,raw)
            r=c.execute('SELECT * FROM job_reviews WHERE booking_id=? AND shop_id=?',(b['id'],shop['id'])).fetchone()
            if not r or b['status']!='completed':raise HTTPException(409,'Reviews are available only after this completed job has a review request.')
            if r['submitted_at']:
                if r['rating']==body.rating and r['comment']==body.comment:return {'message':'Your review is already saved. Thank you.'}
                raise HTTPException(409,'A review has already been submitted for this job.')
            c.execute('UPDATE job_reviews SET rating=?,comment=?,submitted_at=? WHERE booking_id=?',(body.rating,body.comment,now(),b['id']))
            audit(c,shop['id'],'review.submitted',b['id'])
            from . import config
            queue_mail(c,'review-received:'+b['id'],core.settings(shop)['contact_email'],'Customer review received | '+b['id'][:8].upper(),
                       'A customer reviewed their completed job. Open their customer history to read it:\n'+config.BASE_URL+'/app/customers',shop['id'],reply_to=b['email'])
        return {'message':'Thank you. Your review has been shared with the detailer.'}
