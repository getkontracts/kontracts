"""One public plan. Legacy provider contracts are never repriced by a migration."""
from typing import Literal
from fastapi import HTTPException
from . import config
from .db import now
from .models import Model

CATALOG = {'solo': {'id':'solo','name':'Kontracts','amount':4900,'currency':'USD','interval':'month',
    'description':'A focused workspace for one detailing business, one operator and one calendar.',
    'items':['Your own booking link and approval-first scheduling',
             'Logo, brand colors, button colors and cover image',
             'Individual service and vehicle pricing',
             'Branded email updates from one platform mailbox',
             'Saved PDF invoices with detailer and customer e-signatures',
             'Square payments or in-person collection; no booking commission',
             'Private car photos (30 MiB per workspace)',
             'Customer records and data exports'],
    'exclusions':['No SMS, multi-staff calendars, routing or native mobile app',
                  'No custom email domains, automatic domain hosting or accounting integration',
                  'No unlimited photo storage, tax advice or payment processing included']}}
class PlanSelection(Model):
    plan_id: Literal['solo'] | None = None

def get(key='solo'): return CATALOG['solo']
def selected(shop): return 'solo'
def price_id(key='solo'): return config.PADDLE_PRICE if key=='solo' else ''
def configured(key='solo'):
    return bool(config.PADDLE_PRICE and config.PADDLE_KEY and config.PADDLE_CLIENT and config.PADDLE_WEBHOOK)
def catalog(): return [{**get(),'checkout_ready':configured()}]
def features(shop):
    trial=shop['billing_status']=='trial' and shop['trial_end']>now()
    active=bool(shop['is_demo']) or trial or shop['billing_status'] in ('active','trialing')
    # Existing paid custom domains are retained; new accounts are not sold this feature.
    grandfathered=bool(shop['paddle_subscription']) and (shop['plan_id'] in ('legacy','business') or bool(shop['legacy_custom_domain']))
    return {'logo':True,'brand_colors':active,'hide_attribution':active,
            'custom_domain':active and grandfathered,'custom_sender':False,
            'trial_all_features':trial and not bool(shop['is_demo'])}
def require(shop,feature):
    if not features(shop).get(feature):
        raise HTTPException(402,'This feature is unavailable. Branding is included in the active Kontracts plan; custom email senders are not supported.')
def for_price(identifier):
    if not identifier: return None
    # Read-only compatibility for existing subscriptions, not additional public plans.
    known=[config.PADDLE_PRICE,config.PADDLE_STARTER_PRICE_ID,config.PADDLE_PRO_PRICE_ID,config.PADDLE_BUSINESS_PRICE_ID]
    return 'solo' if identifier in known else None
