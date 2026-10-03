"""Square account connection management. Nothing here reads or changes Paddle billing."""
import json
from datetime import datetime
from fastapi import Depends, HTTPException
from pydantic import Field
from . import config, payments
from .db import db, now, packed, audit
from .models import Model
from .security import encrypt, decrypt


class LocationChoice(Model):
    location_id: str = Field(min_length=1,max_length=100,pattern=r'^[A-Za-z0-9_-]+$')


def eligible_locations(locations):
    return [x for x in locations if x.get('id') and x.get('country')=='US'
            and x.get('currency')=='USD' and x.get('status')=='ACTIVE'
            and 'CREDIT_CARD_PROCESSING' in x.get('capabilities',[])]


def save_connection(c,shop_id,result,location):
    shop=c.execute('SELECT * FROM shops WHERE id=?',(shop_id,)).fetchone()
    other=c.execute('SELECT id FROM shops WHERE square_merchant=? AND id<>?',(result['merchant_id'],shop_id)).fetchone()
    if other:raise HTTPException(409,'This Square account is connected to another workspace. Disconnect it there or contact support. No connection was replaced.')
    changed=shop['square_merchant'] and (shop['square_merchant']!=result['merchant_id'] or shop['square_location']!=location['id'])
    if changed:
        history=c.execute("SELECT 1 FROM bookings WHERE shop_id=? AND payment_method='square' LIMIT 1",(shop_id,)).fetchone()
        if history:raise HTTPException(409,'Existing Square records use a different account or location. Reconnect the original location, or contact support for a migration. Your current connection is unchanged.')
    try:
        expiry=int(datetime.fromisoformat(result['expires_at'].replace('Z','+00:00')).timestamp())
        access=encrypt(result['access_token']);refresh=encrypt(result['refresh_token'])
    except (KeyError,TypeError,ValueError):
        raise HTTPException(502,'Square returned an incomplete connection. Start Connect Square again; no credentials were replaced.')
    c.execute('UPDATE shops SET square_merchant=?,square_location=?,square_access=?,square_refresh=?,square_expires=? WHERE id=?',
              (result['merchant_id'],location['id'],access,refresh,expiry,shop_id))
    c.execute('DELETE FROM square_pending_connections WHERE shop_id=?',(shop_id,))
    audit(c,shop_id,'square.connected',location['id'])


def complete_oauth(shop,result,locations):
    eligible=eligible_locations(locations)
    if not eligible:raise HTTPException(422,'Square has no active US location configured for USD card processing. Finish the location setup in Square, then reconnect.')
    if not all(result.get(k) for k in ('merchant_id','access_token','refresh_token','expires_at')):
        raise HTTPException(502,'Square returned an incomplete connection. Try Connect Square again.')
    with payments.LOCKS['square:'+shop['id']]:
        with db(True) as c:
            if len(eligible)==1:
                save_connection(c,shop['id'],result,eligible[0]);return 'square-connected'
            expiry=int(datetime.fromisoformat(result['expires_at'].replace('Z','+00:00')).timestamp())
            safe=[{'id':x['id'],'name':x.get('name') or x['id']} for x in eligible]
            c.execute('INSERT OR REPLACE INTO square_pending_connections VALUES(?,?,?,?,?,?,?)',
                      (shop['id'],result['merchant_id'],encrypt(result['access_token']),encrypt(result['refresh_token']),expiry,packed(safe),now()+600))
            audit(c,shop['id'],'square.location_selection_requested')
    return 'square-select-location'


def install(app,owner):
    @app.get('/api/integrations/square/status')
    def status(shop=Depends(owner)):
        with db() as c:
            pending=c.execute('SELECT locations,expires_at FROM square_pending_connections WHERE shop_id=? AND expires_at>?',(shop['id'],now())).fetchone()
        return {'connected':bool(shop['square_access']),'location_id':shop['square_location'],
                'pending_locations':json.loads(pending['locations']) if pending else [],
                'selection_expires_at':pending['expires_at'] if pending else None}

    @app.post('/api/integrations/square/location')
    def choose(body:LocationChoice,shop=Depends(owner)):
        with payments.LOCKS['square:'+shop['id']]:
            with db() as c:
                pending=c.execute('SELECT * FROM square_pending_connections WHERE shop_id=? AND expires_at>?',(shop['id'],now())).fetchone()
            if not pending:raise HTTPException(409,'This location selection expired. Start Connect Square again. Your existing connection is unchanged.')
            if not any(x['id']==body.location_id for x in json.loads(pending['locations'])):
                raise HTTPException(422,'Choose one of the Square locations shown for this account.')
            access=decrypt(pending['access_encrypted'])
            locations=payments.call(config.SQUARE_URL,'/v2/locations',access,method='GET',square=True).get('locations',[])
            location=next((x for x in eligible_locations(locations) if x['id']==body.location_id),None)
            if not location:raise HTTPException(409,'This location is no longer eligible. Check US, USD and card processing settings in Square, then reconnect.')
            result={'merchant_id':pending['merchant_id'],'access_token':access,'refresh_token':decrypt(pending['refresh_encrypted']),
                    'expires_at':datetime.fromtimestamp(pending['token_expires']).astimezone().isoformat()}
            with db(True) as c:
                if pending['expires_at']<=now():raise HTTPException(409,'Location selection expired. Connect Square again.')
                save_connection(c,shop['id'],result,location)
        return {'message':'Square connected to the selected location. Your Kontracts subscription is unchanged.'}
