"""Square only: detailer service deposits, balances and refunds. No SaaS billing."""
import json
from datetime import datetime
from fastapi import HTTPException
from . import config, branding, payments
from .db import db, now, packed, audit
from .security import encrypt, decrypt
from .core import settings, notify_booking, conflict
LOCKS=payments.LOCKS

def validate_square_url(url):
    from urllib.parse import urlsplit
    u=urlsplit(url)
    if u.scheme!='https' or u.username or u.password or not (u.hostname in ('square.link','checkout.square.site','checkout.squareupsandbox.com') or (u.hostname or '').endswith('.square.site')):
        raise HTTPException(502,'Square returned an unexpected checkout address.')
    return url


def square_token(shop_id):
    with LOCKS['square:'+shop_id]:
        with db() as c: s=c.execute('SELECT * FROM shops WHERE id=?',(shop_id,)).fetchone()
        if not s or not s['square_access']: raise HTTPException(503,'This business has not connected Square.')
        if s['square_expires'] and s['square_expires']<now()+86400:
            result=payments.call(config.SQUARE_URL,'/oauth2/token','',{'client_id':config.SQUARE_ID,'client_secret':config.SQUARE_SECRET,
                'grant_type':'refresh_token','refresh_token':decrypt(s['square_refresh'])},square=True)
            expiry=int(datetime.fromisoformat(result['expires_at'].replace('Z','+00:00')).timestamp())
            with db(True) as c:
                c.execute('UPDATE shops SET square_access=?,square_refresh=?,square_expires=? WHERE id=?',
                    (encrypt(result['access_token']),encrypt(result.get('refresh_token') or decrypt(s['square_refresh'])),expiry,shop_id))
            return result['access_token']
        return decrypt(s['square_access'])


def square(shop_id,path,body=None,method='POST'):
    return payments.call(config.SQUARE_URL,path,payments.square_token(shop_id),body,method,True)


def create_checkout(booking_id):
    with LOCKS['checkout:'+booking_id]:
        with db() as c:
            b=c.execute('SELECT * FROM bookings WHERE id=?',(booking_id,)).fetchone()
            s=c.execute('SELECT * FROM shops WHERE id=?',(b['shop_id'],)).fetchone()
        if b['checkout_url'] or b['status']!='held': return dict(b)
        if s['is_demo'] or not b['approved_at'] or b['payment_method']!='square':
            raise HTTPException(403,'Only approved real Square jobs can request a payment link.')
        if b['hold_until']<=now(): raise HTTPException(409,'This payment reservation has expired.')
        payload={'idempotency_key':b['id'],'description':s['name']+' appointment deposit',
            'order':{'location_id':s['square_location'],'reference_id':b['id'],
              'line_items':[{'name':s['name']+' - booking deposit','quantity':'1','base_price_money':{'amount':b['deposit'],'currency':'USD'}}]},
            'checkout_options':{'allow_tipping':False,'redirect_url':branding.public_origin(s)+'/manage/'+decrypt(b['manage_encrypted'])},
            'pre_populated_data':{'buyer_email':b['email']}}
        result=payments.square(s['id'],'/v2/online-checkout/payment-links',payload)['payment_link']
        payments.validate_square_url(result['url'])
        with db(True) as c:
            c.execute('UPDATE bookings SET checkout_url=?,square_order=?,square_link=? WHERE id=?',
                (result['url'],result['order_id'],result['id'],b['id']))
            return dict(c.execute('SELECT * FROM bookings WHERE id=?',(b['id'],)).fetchone())


def delete_checkout(b):
    if b['square_link']:
        payments.square(b['shop_id'],'/v2/online-checkout/payment-links/'+b['square_link'],method='DELETE')


def apply_square_payment(event_id,merchant_id,payment):
    """Called only after signature verification or authenticated provider reconciliation."""
    if payment.get('status')!='COMPLETED': return
    from .invoices import apply_payment, sync_deposit
    # Known booking deposits avoid an unnecessary provider order lookup.
    with db() as c: known=c.execute('SELECT id FROM bookings WHERE square_order=?',(payment.get('order_id'),)).fetchone()
    if not known and apply_payment(event_id,merchant_id,payment): return
    with db() as c:
        shop=c.execute('SELECT * FROM shops WHERE square_merchant=?',(merchant_id,)).fetchone()
        b=c.execute('SELECT * FROM bookings WHERE square_order=?',(payment.get('order_id'),)).fetchone()
    if not shop: return
    if b is None and payment.get('order_id'):
        order=payments.square(shop['id'],'/v2/orders/'+payment['order_id'],method='GET').get('order',{})
        with db(True) as c:
            b=c.execute('SELECT * FROM bookings WHERE id=? AND shop_id=?',(order.get('reference_id',''),shop['id'])).fetchone()
            if b: c.execute('UPDATE bookings SET square_order=? WHERE id=?',(payment['order_id'],b['id']))
    if not b: return
    if b['shop_id']!=shop['id'] or (payment.get('location_id') and payment['location_id']!=shop['square_location']): raise HTTPException(400,'Payment merchant or location mismatch.')
    if not b['approved_at'] or b['payment_method']!='square': raise HTTPException(400,'Payment was not authorized for this job.')
    money=payment.get('amount_money',{})
    if money.get('currency')!='USD' or money.get('amount')!=b['deposit']:
        raise HTTPException(400,'Payment amount does not match the reservation.')
    with db(True) as c:
        if c.execute("SELECT 1 FROM webhook_events WHERE provider='square' AND event_id=?",(event_id,)).fetchone(): return
        b=c.execute('SELECT * FROM bookings WHERE id=?',(b['id'],)).fetchone()
        if b['square_payment'] and b['square_payment']!=payment['id']:
            raise HTTPException(409,'A second payment requires manual investigation.')
        already_paid=bool(b['square_payment'])
        refunded=max(payment.get('refunded_money',{}).get('amount',0),b['refunded'])
        pstatus='refunded' if refunded>=b['deposit'] else ('partially_refunded' if refunded else 'paid')
        status=b['status']
        if not already_paid:
            safe=(bool(b['approved_at']) and status=='held' and b['hold_until']>now() and not conflict(c,shop['id'],b['start_ts'],b['busy_until'],b['id']))
            status='confirmed' if safe else 'payment_review'
        if b['payment_status']=='refund_pending' and not refunded: pstatus='refund_pending'
        c.execute('UPDATE bookings SET square_payment=?,payment_status=?,refunded=?,status=?,updated_at=? WHERE id=?',
            (payment['id'],pstatus,max(refunded,b['refunded']),status,now(),b['id']))
        updated=c.execute('SELECT * FROM bookings WHERE id=?',(b['id'],)).fetchone()
        sync_deposit(c,updated)
        if not already_paid:
            title='Booking confirmed' if status=='confirmed' else 'Payment received - appointment needs review; not confirmed'
            notify_booking(c,shop,updated,title,b['id']+':payment-confirmed')
            if status=='confirmed': notify_booking(c,shop,updated,'Your detail is tomorrow',b['id']+':reminder',True)
        c.execute('INSERT INTO webhook_events VALUES(?,?,?)',('square',event_id,now()))
        audit(c,shop['id'],'payment.'+pstatus,b['id'])


def refund_deposit(b):
    with LOCKS['refund:'+b['id']]:
        with db(True) as c:
            b=c.execute('SELECT * FROM bookings WHERE id=?',(b['id'],)).fetchone()
            shop=c.execute('SELECT * FROM shops WHERE id=?',(b['shop_id'],)).fetchone()
            if b['payment_status']=='refunded': return {'status':'refunded'}
            if b['payment_status'] not in ('paid','refund_pending','refund_failed','partially_refunded'):
                raise HTTPException(409,'There is no verified deposit to refund.')
            if not b['square_payment'] and not shop['is_demo']: raise HTTPException(409,'A verified provider payment is required.')
            if b['refunded']>0 and not shop['is_demo']:
                raise HTTPException(409,'A partial refund exists. Complete the remaining refund in Square and reconcile.')
            c.execute("UPDATE bookings SET payment_status='refund_pending' WHERE id=?",(b['id'],))
        if shop['is_demo']:
            result={'refund':{'id':'demo-'+b['id'],'status':'COMPLETED'}}
        else:
            # Stable idempotency key makes network retries safe. Partial external refunds require provider review.
            if b['refunded']>0: raise HTTPException(409,'A partial refund exists. Complete the remaining refund in Square and reconcile.')
            result=payments.square(b['shop_id'],'/v2/refunds',{'idempotency_key':'refund-'+b['id'],'payment_id':b['square_payment'],
                'amount_money':{'amount':b['deposit'],'currency':'USD'},'reason':'Deposit refund requested by the detailing business'})
        r=result['refund']; complete=r['status']=='COMPLETED'
        with db(True) as c:
            c.execute('UPDATE bookings SET refund_id=?,payment_status=?,refunded=?,updated_at=? WHERE id=?',
                (r['id'],'refunded' if complete else ('refund_failed' if r['status'] in ('REJECTED','FAILED') else 'refund_pending'),
                 b['deposit'] if complete else b['refunded'],now(),b['id']))
            from .invoices import sync_deposit
            sync_deposit(c,c.execute('SELECT * FROM bookings WHERE id=?',(b['id'],)).fetchone())
            audit(c,b['shop_id'],'refund.requested',b['id'])
        return {'status':'refunded' if complete else 'refund_pending'}


def apply_refund(event_id,merchant_id,r):
    from .invoices import apply_refund as invoice_refund, sync_deposit
    if invoice_refund(event_id,merchant_id,r): return
    with db(True) as c:
        if c.execute("SELECT 1 FROM webhook_events WHERE provider='square' AND event_id=?",(event_id,)).fetchone(): return
        b=c.execute('''SELECT b.* FROM bookings b JOIN shops s ON s.id=b.shop_id
          WHERE b.square_payment=? AND s.square_merchant=?''',(r.get('payment_id'),merchant_id)).fetchone()
        if not b: return
        # Refund events can arrive out of order. Never regress a completed refund to pending.
        if r.get('status')=='COMPLETED':
            amount=r.get('amount_money',{}).get('amount',0)
            if r.get('amount_money',{}).get('currency')!='USD' or amount<0: raise HTTPException(400,'Invalid refund currency or amount.')
            # Aggregate from provider payment in worker; this event handles the app's single full refund.
            if amount==b['deposit']:
                c.execute("UPDATE bookings SET payment_status='refunded',refunded=?,refund_id=?,updated_at=? WHERE id=?",(amount,r['id'],now(),b['id']))
            elif not b['refunded']:
                c.execute("UPDATE bookings SET payment_status='partially_refunded',refunded=?,updated_at=? WHERE id=?",(amount,now(),b['id']))
        elif r.get('status') in ('REJECTED','FAILED') and b['payment_status']!='refunded':
            c.execute("UPDATE bookings SET payment_status='refund_failed',updated_at=? WHERE id=?",(now(),b['id']))
        sync_deposit(c,c.execute('SELECT * FROM bookings WHERE id=?',(b['id'],)).fetchone())
        c.execute('INSERT INTO webhook_events VALUES(?,?,?)',('square',event_id,now()))


