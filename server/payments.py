"""Compatibility facade and neutral HTTP transport.
Square implementation is in square_gateway.py; SaaS subscription implementation is
in paddle_gateway.py. Sharing a transport is not sharing customers, balances, money,
credentials, webhook verification, checkout URLs or provider state.
"""
import threading
from collections import defaultdict
import httpx
from fastapi import HTTPException
from . import config
LOCKS=defaultdict(threading.RLock)

def call(base,path,token,body=None,method='POST',square=False,auth_scheme='Bearer'):
    headers={'Content-Type':'application/json'}
    if token: headers['Authorization']=auth_scheme+' '+token
    if square: headers['Square-Version']=config.SQUARE_VERSION
    try:
        with httpx.Client(timeout=20) as client:
            r=client.request(method,base+path,headers=headers,json=body)
    except httpx.HTTPError:
        raise HTTPException(502,'The payment provider could not be reached. Your payment status has not been assumed; please retry safely.')
    provider='Square' if square else 'Subscription billing'
    if not 200<=r.status_code<300:
        if r.status_code in (401,403):
            message=('Square authorization was rejected. Reconnect the original account in Customer payments, or ask the operator to check Square permissions.' if square else 'Paddle authorization was rejected. Ask the operator to check subscription billing credentials.')
        elif r.status_code==429:message=provider+' is temporarily limiting requests. Retry later; do not create another checkout.'
        elif r.status_code>=500:message=provider+' is temporarily unavailable. Retry the same action to avoid duplicates.'
        else:message=provider+' could not accept this operation. Check the account connection and details, or contact support.'
        raise HTTPException(502,message+' Existing payment status has not been assumed or changed.')
    try:return r.json() if r.content else {}
    except ValueError:raise HTTPException(502,provider+' returned an invalid response. Retry the same action; payment status has not been assumed.')


def validate_square_url(*args, **kwargs):
    from .square_gateway import validate_square_url as operation
    return operation(*args, **kwargs)

def square_token(*args, **kwargs):
    from .square_gateway import square_token as operation
    return operation(*args, **kwargs)

def square(*args, **kwargs):
    from .square_gateway import square as operation
    return operation(*args, **kwargs)

def create_checkout(*args, **kwargs):
    from .square_gateway import create_checkout as operation
    return operation(*args, **kwargs)

def delete_checkout(*args, **kwargs):
    from .square_gateway import delete_checkout as operation
    return operation(*args, **kwargs)

def apply_square_payment(*args, **kwargs):
    from .square_gateway import apply_square_payment as operation
    return operation(*args, **kwargs)

def refund_deposit(*args, **kwargs):
    from .square_gateway import refund_deposit as operation
    return operation(*args, **kwargs)

def apply_refund(*args, **kwargs):
    from .square_gateway import apply_refund as operation
    return operation(*args, **kwargs)

def paddle(*args, **kwargs):
    from .paddle_gateway import paddle as operation
    return operation(*args, **kwargs)

def billing_checkout(*args, **kwargs):
    from .paddle_gateway import billing_checkout as operation
    return operation(*args, **kwargs)

def billing_portal(*args, **kwargs):
    from .paddle_gateway import billing_portal as operation
    return operation(*args, **kwargs)

def apply_paddle(*args, **kwargs):
    from .paddle_gateway import apply_paddle as operation
    return operation(*args, **kwargs)

