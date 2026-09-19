"""Pricing and scheduling. Every reservation is checked in an IMMEDIATE transaction."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from . import config
from .mail import shop_contact
from .db import db, now, uid, packed, queue_mail, audit
from .security import digest, token, encrypt, decrypt

ACTIVE = ('confirmed','completed','no_show')

def settings(shop):
    result = json.loads(shop['settings'])
    result.setdefault('payment_method','square' if shop['square_access'] else 'in_person')
    for package in result.get('packages', []):
        package.setdefault('vehicle_rates', {})
    if 'settings_revision' in shop.keys(): result['revision'] = shop['settings_revision']
    return result

def shop_by_slug(c,slug,require_public=True):
    row=c.execute('SELECT s.* FROM shops s WHERE s.slug=? OR s.id=(SELECT shop_id FROM booking_slugs WHERE slug=?)',(slug,slug)).fetchone()
    if not row: raise HTTPException(404,'This booking page could not be found.')
    if require_public and (not row['published'] or not entitled(row)):
        raise HTTPException(404,'This business is not accepting online bookings right now.')
    return row

def entitled(shop):
    return bool(shop['is_demo']) or shop['billing_status'] in ('active','trialing') or (shop['billing_status']=='trial' and shop['trial_end']>now())

def public_shop(shop):
    s=settings(shop)
    s['packages']=[x for x in s['packages'] if x['active']]
    s['extras']=[x for x in s['extras'] if x['active']]
    for key in ['policy_reviewed','revision']:
        s.pop(key,None)
    from .branding import live_brand, links
    return {'slug':shop['slug'],'name':shop['name'],'settings':s,'branding':live_brand(shop),**links(shop),'demo':bool(shop['is_demo']),
            'payments_ready':s['payment_method']=='in_person' or bool(shop['square_access'] and config.SQUARE_WEBHOOK), 'correspondence_email':shop_contact(shop)}

def quote_price(c,shop,selection):
    s=settings(shop)
    if selection.zip not in s['zip_codes']:
        raise HTTPException(422,'That ZIP code is outside the service area. Contact the business for a custom arrangement.')
    if selection.quote_token:
        q=c.execute('SELECT * FROM quotes WHERE token_hash=? AND shop_id=?',(digest(selection.quote_token),shop['id'])).fetchone()
        if not q or q['status']!='offered' or q['expires_at']<now(): raise HTTPException(410,'This custom quote is no longer available.')
        if selection.zip!=q['zip'] or selection.vehicle_id!=q['vehicle']: raise HTTPException(422,'The custom quote applies only to the original vehicle and ZIP code.')
        sub=q['amount']; minutes=q['minutes']; lines=[{'name':'Custom detail quote','amount':sub}]
        vehicle=q['vehicle']; name='Custom detail'
    else:
        if selection.condition=='heavy':
            raise HTTPException(422,'Heavy-condition vehicles need a reviewed quote. Please request a custom quote.')
        package=next((p for p in s['packages'] if p['id']==selection.package_id and p['active']),None)
        vehicle=next((v for v in s['vehicles'] if v['id']==selection.vehicle_id),None)
        if not package or not vehicle: raise HTTPException(422,'Please choose a current service and vehicle size.')
        extras=[]
        for eid in selection.extra_ids:
            item=next((e for e in s['extras'] if e['id']==eid and e['active']),None)
            if not item: raise HTTPException(422,'One of those add-ons is no longer available.')
            extras.append(item)
        rate = package.get('vehicle_rates', {}).get(vehicle['id'])
        base = rate['price'] if rate else package['price'] + vehicle['price']
        base_minutes = rate['minutes'] if rate else package['minutes'] + vehicle['minutes']
        sub=base+sum(e['price'] for e in extras)
        minutes=base_minutes+sum(e['minutes'] for e in extras)
        if minutes+s['buffer_minutes']>720: raise HTTPException(422,'This combination needs a custom quote.')
        lines=[{'name':package['name'] + ' - ' + vehicle['name'],'amount':base}]
        lines += [{'name':e['name'],'amount':e['price']} for e in extras]
        name=package['name']; vehicle=vehicle['name']
    tax=(sub*s['tax_basis_points']+5000)//10000
    total=sub+tax
    deposit=(total*s['deposit_percent']+50)//100 if s['payment_method']=='square' else 0
    return {'subtotal':sub,'tax':tax,'total':total,'deposit':deposit,'minutes':minutes,
            'buffer_minutes':s['buffer_minutes'],'lines':lines,'service':name,'vehicle':vehicle,'currency':'USD'}

def conflict(c,shop_id,start,end,exclude=None):
    row=c.execute('''SELECT id FROM bookings WHERE shop_id=? AND id!=? AND start_ts<? AND busy_until>?
      AND (status IN ('confirmed','completed','awaiting_signature','no_show') OR (status='held' AND hold_until>?)) LIMIT 1''',
      (shop_id,exclude or '',end,start,now())).fetchone()
    if row: return True
    return bool(c.execute('SELECT id FROM blocks WHERE shop_id=? AND start_ts<? AND end_ts>? LIMIT 1',(shop_id,end,start)).fetchone())

def day_slots(c,shop,date,minutes,exclude=None):
    s=settings(shop); zone=ZoneInfo(s['timezone'])
    try: day=datetime.strptime(date,'%Y-%m-%d').date()
    except ValueError: raise HTTPException(422,'Choose a valid calendar date.')
    today=datetime.fromtimestamp(now(),zone).date()
    if day<today or day>today+timedelta(days=s['horizon_days']): return []
    hours=s['hours'][day.weekday()]
    if not hours['enabled']: return []
    opening=datetime.fromisoformat(f"{date}T{hours['start']}")
    closing=datetime.fromisoformat(f"{date}T{hours['end']}")
    result=[]; t=opening
    while t+timedelta(minutes=minutes+s['buffer_minutes'])<=closing:
        local=t.replace(tzinfo=zone)
        ts=int(local.timestamp())
        # Skip nonexistent and ambiguous DST local times rather than silently shift them.
        valid=datetime.fromtimestamp(ts,zone).replace(tzinfo=None)==t and local.utcoffset()==t.replace(tzinfo=zone,fold=1).utcoffset()
        end=ts+(minutes+s['buffer_minutes'])*60
        if valid and ts>=now()+s['lead_hours']*3600 and not conflict(c,shop['id'],ts,end,exclude):
            result.append({'start_ts':ts,'label':local.strftime('%I:%M %p').lstrip('0')})
        t+=timedelta(minutes=30)
    return result

def require_slot(c,shop,start,minutes,exclude=None):
    date=datetime.fromtimestamp(start,ZoneInfo(settings(shop)['timezone'])).strftime('%Y-%m-%d')
    if start not in {x['start_ts'] for x in day_slots(c,shop,date,minutes,exclude)}:
        raise HTTPException(409,'That time is no longer available. Please choose another appointment.')

def booking_message(shop,b,title):
    s=settings(shop)
    when=datetime.fromtimestamp(b['start_ts'],ZoneInfo(s['timezone'])).strftime('%A, %B %d at %I:%M %p %Z')
    from .branding import public_origin
    manage=public_origin(shop)+'/manage/'+decrypt(b['manage_encrypted'])
    body=f"{title}\n\n{shop['name']}\n{json.loads(b['snapshot'])['service']}\n{when}\n{b['address']}\n\nService total: ${b['total']/100:.2f}\nDeposit: ${b['deposit']/100:.2f}\nPayment status: {b['payment_status']}\n\nView and manage your booking: {manage}\n\nQuestions: {shop_contact(shop)}\n{s['cancellation_policy']}"
    method=b['payment_method'] if 'payment_method' in b.keys() else s['payment_method']
    body+='\n\n'+('Payment: pay the detailer in person. No online payment link.' if method=='in_person' else 'Payment: Square checkout is available only after detailer approval. Funds go to the detailer.')
    body+='\nBooking and invoice questions: '+shop_contact(shop)
    return body

def notify_booking(c,shop,b,title,key,reminder=False):
    body=booking_message(shop,b,title)
    if reminder:
        due=b['start_ts']-86400
        if due>now(): queue_mail(c,key,b['email'],shop['name']+' | '+title+' | '+b['id'][:8].upper(),body,shop['id'],due,b['id'],b['start_ts'],expires_at=b['start_ts'])
    else:
        queue_mail(c,key+':customer',b['email'],shop['name']+' | '+title+' | '+b['id'][:8].upper(),body,shop['id'])
        queue_mail(c,key+':owner',settings(shop)['contact_email'],shop['name']+' | '+title+' | '+b['customer_name']+' | '+b['id'][:8].upper(),body,shop['id'],reply_to=b['email'])

def reserve(shop_id,body,owner=False):
    request_hash=hashlib.sha256(packed(body.model_dump()).encode()).hexdigest()
    with db(True) as c:
        shop=c.execute('SELECT * FROM shops WHERE id=?',(shop_id,)).fetchone()
        if shop and shop['is_demo']: raise HTTPException(403,'The demo cannot create real bookings. Use the isolated /demo simulation.')
        if not shop or (not owner and (not shop['published'] or not entitled(shop))): raise HTTPException(404,'Booking is unavailable.')
        prior=c.execute('SELECT * FROM bookings WHERE shop_id=? AND idempotency_key=?',(shop_id,body.idempotency_key)).fetchone()
        if prior:
            if prior['request_hash']!=request_hash: raise HTTPException(409,'This booking attempt changed. Please start a new submission.')
            return dict(prior),False
        if body.website: raise HTTPException(422,'Unable to submit this request.')
        s=settings(shop)
        if s['requires_water'] and not body.has_water: raise HTTPException(422,'Water access is required for this service.')
        if s['requires_power'] and not body.has_power: raise HTTPException(422,'Power access is required for this service.')
        p=quote_price(c,shop,body)
        if body.quote_token:
            q=c.execute('SELECT * FROM quotes WHERE token_hash=?',(digest(body.quote_token),)).fetchone()
            if body.email.lower()!=q['email'].lower(): raise HTTPException(422,'Use the email address associated with this custom quote.')
            qid=q['id']
        else: qid=None
        require_slot(c,shop,body.start_ts,p['minutes'])
        if p['deposit'] and not owner and not shop['is_demo'] and not (shop['square_access'] and config.SQUARE_WEBHOOK):
            raise HTTPException(503,'Online deposits are not connected. Please contact the business to book.')
        identifier=uid(); manage=token(); timestamp=now()
        pending=c.execute("SELECT count(*) FROM bookings WHERE shop_id=? AND email=? AND status='pending_approval'",(shop_id,str(body.email).lower())).fetchone()[0]
        if pending>=3: raise HTTPException(429,'You already have pending requests with this detailer. Contact them before sending another.')
        needs_payment=False
        b={'id':identifier,'shop_id':shop_id,'manage_hash':digest(manage),'manage_encrypted':encrypt(manage),
           'idempotency_key':body.idempotency_key,'request_hash':request_hash,
           'customer_name':body.customer_name,'email':str(body.email).lower(),'phone':body.phone,
           'address':body.address,'zip':body.zip,'vehicle':body.vehicle_id,'vehicle_notes':body.vehicle_notes,'notes':body.notes,
           'snapshot':packed(p),'start_ts':body.start_ts,'end_ts':body.start_ts+p['minutes']*60,
           'busy_until':body.start_ts+(p['minutes']+s['buffer_minutes'])*60,
           'subtotal':p['subtotal'],'tax':p['tax'],'total':p['total'],'deposit':p['deposit'],
           'status':'pending_approval','payment_status':'unpaid',
           'payment_method':s['payment_method'],'country':'US','state':body.state,
           'hold_until':timestamp+900 if needs_payment else None,'quote_id':qid,
           'accepted_policy':s['cancellation_policy'],'created_at':timestamp,'updated_at':timestamp}
        c.execute(f"INSERT INTO bookings ({','.join(b)}) VALUES({','.join('?' for _ in b)})",tuple(b.values()))
        if qid: c.execute("UPDATE quotes SET status='reserved' WHERE id=?",(qid,))
        audit(c,shop_id,'booking.reserved',identifier)
        if not needs_payment:
            notify_booking(c,shop,b,'Booking request received - awaiting detailer approval',identifier+':requested')
        return dict(c.execute('SELECT * FROM bookings WHERE id=?',(identifier,)).fetchone()),True

def booking_result(b):
    return {'id':b['id'],'status':b['status'],'payment_status':b['payment_status'],
            'manage_url':'/manage/'+decrypt(b['manage_encrypted']),'checkout_url':b['checkout_url'],
            'hold_until':b['hold_until']}

def reschedule(c,shop,b,start):
    if b['status'] not in ('confirmed','payment_review'): raise HTTPException(409,'Only confirmed bookings or paid review bookings can be rescheduled.')
    p=json.loads(b['snapshot']); s=settings(shop)
    require_slot(c,shop,start,p['minutes'],b['id'])
    c.execute("UPDATE bookings SET start_ts=?,end_ts=?,busy_until=?,status='confirmed',updated_at=? WHERE id=?",
        (start,start+p['minutes']*60,start+(p['minutes']+s['buffer_minutes'])*60,now(),b['id']))
    updated=c.execute('SELECT * FROM bookings WHERE id=?',(b['id'],)).fetchone()
    notify_booking(c,shop,updated,'Booking rescheduled',b['id']+':rescheduled:'+str(start))
    notify_booking(c,shop,updated,'Your detail is tomorrow',b['id']+':reminder:'+str(start),True)
    audit(c,shop['id'],'booking.rescheduled',b['id'])

def cancel(c,shop,b):
    if b['status'] not in ('pending_approval','confirmed','held','payment_review'): raise HTTPException(409,'This booking cannot be cancelled in its current state.')
    c.execute("UPDATE bookings SET status='cancelled',updated_at=? WHERE id=?",(now(),b['id']))
    if b['quote_id']:
        c.execute("UPDATE quotes SET status='offered' WHERE id=? AND expires_at>?",(b['quote_id'],now()))
    updated=c.execute('SELECT * FROM bookings WHERE id=?',(b['id'],)).fetchone()
    notify_booking(c,shop,updated,'Booking cancelled - refunds are reviewed separately',b['id']+':cancelled')
    audit(c,shop['id'],'booking.cancelled',b['id'])
