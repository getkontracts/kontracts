"""Single-plan billing. Signed subscription events, never the browser, grant access."""
import hashlib
import hmac
import json
from fastapi import HTTPException
from urllib.parse import urlencode
from . import config, plans
from .db import db, now, packed, audit
from .security import token, digest


def binding(shop_id, customer_id):
    return hmac.new(config.SECRET.encode(),f'v2:{shop_id}:{customer_id}'.encode(),hashlib.sha256).hexdigest()


def checked_price(plan_id):
    from . import payments
    identifier=plans.price_id(plan_id)
    if not identifier or not config.PADDLE_CLIENT or not config.PADDLE_WEBHOOK:
        raise HTTPException(503,'This plan is not connected to Paddle yet. No charge was made. Contact the platform operator.')
    if plans.for_price(identifier)!=plan_id:
        raise HTTPException(503,'The configured Paddle price must match the one public plan.')
    p=payments.paddle('/prices/'+identifier,method='GET')
    cycle=p.get('billing_cycle') or {};money=p.get('unit_price') or {}
    if (p.get('status')!='active' or str(money.get('amount'))!=str(plans.get(plan_id)['amount'])
            or money.get('currency_code')!='USD' or cycle.get('interval')!='month' or cycle.get('frequency')!=1
            or p.get('trial_period') is not None or p.get('unit_price_overrides')):
        raise HTTPException(503,'The configured Paddle price does not match the displayed monthly USD plan. No charge was made.')
    return identifier


def checkout(shop_id, plan_id=None):
    from . import payments
    with payments.LOCKS['billing:'+shop_id]:
        with db() as c:
            s=c.execute('SELECT s.*,u.email FROM shops s JOIN users u ON u.id=s.owner_id WHERE s.id=?',(shop_id,)).fetchone()
            intent=c.execute('SELECT * FROM billing_intents WHERE shop_id=?',(shop_id,)).fetchone()
            pi=c.execute('SELECT * FROM billing_plan_intents WHERE shop_id=?',(shop_id,)).fetchone()
        if not s: raise HTTPException(404,'Account not found.')
        if s['is_demo']: raise HTTPException(403,'The demo cannot create subscriptions.')
        plan_id=plan_id or plans.selected(s)
        if plan_id not in plans.CATALOG or (plan_id=='legacy' and plans.selected(s)!='legacy'):
            raise HTTPException(422,'Choose a current plan.')
        if s['paddle_subscription'] and s['billing_status'] in ('active','trialing','past_due','paused'):
            raise HTTPException(409,'You already have a subscription. Use the billing portal instead of creating another.')
        if intent:
            same=(pi['plan_id'] if pi else 'legacy')==plan_id
            if intent['created_at']>now()-3600:
                if same:return {'url':intent['checkout_url']}
                raise HTTPException(409,'Another checkout is already open. Complete it or ask support to cancel it before choosing another plan.')
            # A locally old URL may still be payable: never assume it expired.
            previous=payments.paddle('/transactions/'+intent['transaction_id'],method='GET')
            if previous.get('status')!='canceled':
                if same and previous.get('status') in ('draft','ready'):
                    return {'url':intent['checkout_url']}
                raise HTTPException(409,'An earlier checkout is still open or awaiting confirmation. No duplicate checkout was created. Contact support before starting again.')
            with db(True) as c:
                c.execute('DELETE FROM billing_intents WHERE shop_id=?',(shop_id,))
                c.execute('DELETE FROM billing_plan_intents WHERE shop_id=?',(shop_id,))
        price=checked_price(plan_id)
        customer = s['paddle_customer']
        if not customer:
            existing = payments.paddle(
                '/customers?' + urlencode({'email': s['email']}),
                method='GET'
            )
            if existing:
                customer = existing[0]['id']
            else:
                customer = payments.paddle(
                    '/customers',
                    {
                        'email': s['email'],
                        'name': s['name']
                    }
                )['id']

            with db(True) as c:
                c.execute(
                    'UPDATE shops SET paddle_customer=? WHERE id=?',
                    (customer, shop_id)
                )
        result=payments.paddle('/transactions',{'items':[{'price_id':price,'quantity':1}],
            'customer_id':customer,'collection_mode':'automatic',
            'custom_data':{'shop_id':shop_id,'binding':binding(shop_id,customer)},
            'checkout':{'url':config.BASE_URL+'/checkout'}})
        url=result['checkout']['url']
        with db(True) as c:
            c.execute('INSERT OR REPLACE INTO billing_intents VALUES(?,?,?,?)',(shop_id,result['id'],url,now()))
            c.execute('INSERT OR REPLACE INTO billing_plan_intents VALUES(?,?,?,?)',(shop_id,plan_id,price,now()))
        return {'url':url}


def preview_totals(data):
    def totals(tx):
        tx=tx or {}
        return (tx.get('details') or {}).get('totals') or tx.get('totals') or {}
    return {'due_now':totals(data.get('immediate_transaction')).get('grand_total','0'),
            'next_total':totals(data.get('next_transaction')).get('grand_total'),
            'currency':data.get('currency_code','USD')}


def preview_change(shop,plan_id):
    raise HTTPException(409,'Kontracts has one plan. Manage or cancel your subscription in the billing portal.')

def confirm_change(shop,raw):
    raise HTTPException(409,'Plan changes are disabled; there is one Kontracts plan.')
