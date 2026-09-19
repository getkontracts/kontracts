"""Offline ZIP membership validation, not street-address/geolocation verification.
Run scripts/update_zipcodes.py during installation. No API key or per-booking lookup.
"""
import json
import re
from functools import lru_cache
from pathlib import Path
from . import config
STATES=frozenset('AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY'.split())
@lru_cache(maxsize=1)
def registry():
    path=Path(__file__).parent/'resources'/'us_zipcodes.json'
    value=json.loads(path.read_text())
    codes=value.get('codes',{})
    if config.PRODUCTION and (value.get('fixture') or len(codes)<30000):
        raise RuntimeError('A full US ZIP registry is required. Run python scripts/update_zipcodes.py before production.')
    if not codes or any(not re.fullmatch(r'[0-9]{5}',k) or not set(v).issubset(STATES) for k,v in codes.items()):
        raise RuntimeError('The local ZIP registry is invalid.')
    return value

def zip_states(value):
    validate_zip(value)
    return frozenset(registry()['codes'][value])
def validate_zip(value):
    if not isinstance(value,str) or not re.fullmatch(r'[0-9]{5}',value) or value not in registry()['codes']:
        raise ValueError('Enter a recognized five-digit US ZIP code in the 50 states or Washington, DC.')
    return value
