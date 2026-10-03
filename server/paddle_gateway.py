"""Paddle only: the Kontracts software subscription. Never customer service revenue."""
import json
from datetime import datetime
from fastapi import HTTPException
from . import config, payments
from .db import db, now, audit
from .security import billing_binding, constant_equal
LOCKS=payments.LOCKS

def paddle(path,body=None,method='POST'):
    if not config.PADDLE_KEY: raise HTTPException(503,'Subscription billing is not configured yet.')
    return payments.call(config.PADDLE_URL,path,config.PADDLE_KEY,body,method)['data']


def billing_checkout(shop_id,plan_id=None):
    from .billing import checkout
    return checkout(shop_id,plan_id)


def billing_portal(shop):
    if not shop['paddle_customer']: raise HTTPException(409,'This account does not have a subscription yet.')
    result=payments.paddle('/customers/'+shop['paddle_customer']+'/portal-sessions',{})
    return result['urls']['general']['overview']


def apply_paddle(event):
    eid=event.get('event_id'); kind=event.get('event_type',''); d=event.get('data',{})
    if not eid: raise HTTPException(400,'Missing event ID.')
    if not kind.startswith('subscription.'): return
    custom=d.get('custom_data') or {}; customer=d.get('customer_id'); sid=custom.get('shop_id')
    from .billing import binding as current_binding
    from . import plans
    if not sid or not customer or not (constant_equal(custom.get('binding',''),billing_binding(sid,customer)) or constant_equal(custom.get('binding',''),current_binding(sid,customer))):
        raise HTTPException(400,'Subscription binding is invalid.')
    prices=[i.get('price',{}).get('id') for i in d.get('items',[])]
    plan=plans.for_price(prices[0]) if len(prices)==1 else None
    if not plan or any(i.get('quantity',1)!=1 for i in d.get('items',[])): raise HTTPException(400,'Unexpected subscription price or quantity.')
    occurred=event.get('occurred_at','')
    try: datetime.fromisoformat(occurred.replace('Z','+00:00'))
    except ValueError: raise HTTPException(400,'Invalid event timestamp.')
    with db(True) as c:
        if c.execute("SELECT 1 FROM webhook_events WHERE provider='paddle' AND event_id=?",(eid,)).fetchone(): return
        shop=c.execute('SELECT * FROM shops WHERE id=? AND paddle_customer=?',(sid,customer)).fetchone()
        if not shop: raise HTTPException(400,'Unknown billing customer.')
        if shop['paddle_subscription'] and shop['paddle_subscription']!=d['id'] and shop['billing_status']!='canceled':
            raise HTTPException(409,'A different subscription is already associated with this account.')
        if not shop['billing_updated_at'] or datetime.fromisoformat(occurred.replace('Z','+00:00'))>datetime.fromisoformat(shop['billing_updated_at'].replace('Z','+00:00')):
            if d.get('status') not in ('active','trialing','past_due','paused','canceled'): raise HTTPException(400,'Unknown subscription state.')
            c.execute('UPDATE shops SET paddle_subscription=?,billing_status=?,billing_updated_at=?,plan_id=? WHERE id=?',(d['id'],d['status'],occurred,plan,sid))
            c.execute('DELETE FROM billing_intents WHERE shop_id=?',(sid,))
            c.execute('DELETE FROM billing_plan_intents WHERE shop_id=?',(sid,))
            audit(c,sid,'subscription.'+d['status'],d['id'])
            from .mail import queue_subscription_notice
            queue_subscription_notice(c,shop,d['status'],eid)
        c.execute('INSERT INTO webhook_events VALUES(?,?,?)',('paddle',eid,now()))


