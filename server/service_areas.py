"""Offline, city-based US service areas. Postal names are not municipal boundaries.
The install/update script loads nationwide GeoNames data; never guess a ZIP range.
"""
import hashlib
from functools import lru_cache
from fastapi import Depends, HTTPException, Query, Request
from . import core, postal
from .db import db
from .security import rate_limit


def city_id(name, state):
    return hashlib.sha256((state+'|'+name.strip().casefold()).encode()).hexdigest()[:20]


def cities():
    value = postal.registry().get('cities', {})
    if not value:
        raise HTTPException(503, 'City data has not been installed. The operator must run scripts/update_zipcodes.py and restart the app.')
    return value


def resolve_areas(areas):
    registry = cities()
    names, covered, seen = [], set(), set()
    for area in areas:
        key = area['city_id']
        city = registry.get(key)
        if not city:
            raise ValueError('Choose a city from the search results. The selected city is not in the installed US registry.')
        if key in seen:
            raise ValueError('This city is already selected. Edit its coverage instead of adding it again.')
        seen.add(key)
        all_zips = set(city['zip_codes'])
        if area['coverage'] == 'city':
            selected = all_zips
        else:
            selected = set(area.get('zip_codes') or [])
            if not selected:
                raise ValueError('Select at least one ZIP code for '+city['name']+', '+city['state']+'.')
            invalid = selected - all_zips
            if invalid:
                raise ValueError('ZIP '+', '.join(sorted(invalid))+' is not assigned to '+city['name']+', '+city['state']+' in the registry. Add its own city to offer service there.')
        covered.update(selected)
        names.append(city['name']+', '+city['state'])
    return names, sorted(covered)


def coverage(settings, zip_code, business='This business'):
    try:
        postal.validate_zip(zip_code)
    except ValueError:
        return {'valid': False, 'available': False, 'zip': zip_code,
                'message': 'Enter a recognized five digit US ZIP code. Check the ZIP and try again.'}
    if settings.get('service_areas'):
        try:
            _, allowed = resolve_areas(settings['service_areas'])
        except ValueError as exc:
            raise HTTPException(503, 'This business needs to update its service area. Contact the detailer before booking.') from exc
    else:
        # Do not silently expand existing businesses when upgrading an installation.
        allowed = settings.get('zip_codes', [])
    available = zip_code in allowed
    return {'valid': True, 'available': available, 'zip': zip_code,
            'states': sorted(postal.zip_states(zip_code)),
            'message': (business+' serves ZIP '+zip_code+'. Choose your service to see available appointments.' if available else
                        business+' does not currently serve ZIP '+zip_code+'. Try another service address or contact the detailer. No booking or payment has been created.')}


def require_coverage(settings, zip_code, business):
    result = coverage(settings, zip_code, business)
    if not result['available']:
        raise HTTPException(422, result['message'])
    return result


def install(app, owner, ip):
    @app.get('/api/locations/cities')
    def search_cities(request: Request, q: str = Query('', max_length=80), state: str = Query('', max_length=2)):
        rate_limit('cities:'+ip(request), 240, 600)
        state = state.upper()
        if state and state not in postal.STATES:
            raise HTTPException(422, 'Choose a US state or Washington, DC.')
        term = q.strip().casefold()
        if len(term) < 2 and not state:
            return {'cities': [], 'more': False, 'fixture': bool(postal.registry().get('fixture'))}
        matching = [x for x in cities().values() if (not state or x['state']==state) and
                    (not term or term in x['name'].casefold() or term in x['zip_codes'])]
        matching.sort(key=lambda x: (not x['name'].casefold().startswith(term), x['name'], x['state']))
        return {'cities': [{k: x[k] for k in ('id','name','state')} for x in matching[:40]],
                'more': len(matching)>40, 'fixture': bool(postal.registry().get('fixture')),
                'source': 'GeoNames', 'retrieved_at': postal.registry().get('retrieved_at')}

    @app.get('/api/locations/cities/{identifier}')
    def city_detail(identifier: str):
        city = cities().get(identifier)
        if not city:
            raise HTTPException(404, 'City not found. Search again and select a result.')
        return city

    @app.get('/api/public/shops/{slug}/coverage')
    def public_coverage(slug: str, request: Request, zip: str = Query('', max_length=12)):
        rate_limit('coverage:'+ip(request), 120, 600)
        with db() as c:
            shop = core.shop_by_slug(c, slug)
            return coverage(core.settings(shop), zip, shop['name'])

    @app.get('/api/preview/coverage')
    def preview_coverage(zip: str = Query('', max_length=12), shop=Depends(owner)):
        return coverage(core.settings(shop), zip, shop['name'])
