"""End-to-end customer UI with real temporary API handlers; no live service calls.
python tests/browser_experience.py --chromium /usr/bin/chromium --output browser-evidence
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from pytest import MonkeyPatch
from tests.conftest import client, owner
from tests.browser_support import mount
from tests.test_app import available, shop
from tests.test_media import image_bytes
from tests.test_workflow import request_job, issue, sign
from tests.test_experience import quote
from server import config
from server.db import db, now
from server.security import decrypt


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--chromium',default='/usr/bin/chromium')
    ap.add_argument('--output',type=Path,default=Path('browser-evidence'))
    args=ap.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    checks=[];layouts=[];errors=[];dialogs=[]
    with tempfile.TemporaryDirectory() as temp,MonkeyPatch.context() as mp:
        gen=client.__wrapped__(Path(temp),mp);c=next(gen)
        try:
            c=owner.__wrapped__(c,mp)
            mp.setattr(config,'SQUARE_REFERRAL_URL','https://squareup.com/r/EXAMPLE-TEST-ONLY')
            pending=request_job(c,email='pending-ui@example.com',customer_name='Pending UI Customer')
            inv,invoice_token=issue(c);assert sign(c,invoice_token).status_code==200
            with db() as sql:
                complete=sql.execute('SELECT * FROM bookings WHERE id=?',(inv['booking_id'],)).fetchone()
                completed_manage=decrypt(complete['manage_encrypted'])
            requested=quote(c,email='quote-ui@example.com',customer_name='Quote UI Customer').json()
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path=args.chromium,headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
                page=browser.new_page(viewport={'width':1440,'height':1000})
                page.set_default_timeout(12000)
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.on('dialog',lambda d:(dialogs.append(d.message),d.dismiss()))
                mount(page,c,'/app/settings')
                page.wait_for_selector('#city-search')
                page.locator('#city-search').fill('Austin')
                page.wait_for_selector('.city-result')
                assert 'Austin, TX' in page.locator('.city-result').first.inner_text()
                page.wait_for_selector('[data-area-mode]')
                page.locator('[data-area-mode][value="city"]').check()
                page.locator('[data-action="areas-save"]').click()
                page.wait_for_function("document.querySelector('#area-status').textContent.includes('Saved')")
                assert c.get('/api/auth/me').json()['shop']['settings']['service_areas'][0]['coverage']=='city'
                checks.append('City search and saved citywide coverage')
                page.locator('[data-area-mode][value="zip_codes"]').check()
                # Saving a profile while an area is being edited must retain that area.
                page.locator('[data-form="settings-business"] input[name="tagline"]').fill('Private mobile test profile')
                page.locator('[data-form="settings-business"] button[type="submit"]').click()
                page.wait_for_function("document.querySelector('[data-form=\"settings-business\"]').dataset.unsaved==='false'")
                assert page.locator('[data-area-mode][value="zip_codes"]').is_checked()
                for box in page.locator('[data-area-zip]').all():
                    if box.get_attribute('value')=='78701':box.check()
                    else:box.uncheck()
                page.locator('[data-action="areas-save"]').click()
                page.wait_for_function("Experience.areaDirty===false")
                assert c.get('/api/auth/me').json()['shop']['settings']['zip_codes']==['78701']
                checks.append('Specific ZIP coverage and independent business profile drafts')

                page.evaluate("go('/book/northline')");page.wait_for_selector('#booking-zip')
                page.locator('#booking-zip').fill('90210')
                page.wait_for_function("document.querySelector('#zip-coverage-status').textContent.includes('does not currently serve')")
                assert page.locator('[data-action="wizard-next"]').is_disabled()
                page.locator('[data-wcondition]').check()
                page.wait_for_selector('#booking-zip');page.wait_for_timeout(400)
                assert page.locator('[data-action="wizard-next"]').is_disabled()
                page.locator('[data-wcondition]').uncheck()
                page.locator('#booking-zip').fill('78701')
                page.wait_for_function("S.wizard.price!==null")
                page.locator('[data-action="wizard-next"]').click()
                page.wait_for_selector('#booking-date')
                page.locator('#booking-date').fill(available()[0]);page.locator('#booking-date').dispatch_event('change')
                page.wait_for_selector('[data-action="wizard-slot"]')
                page.locator('[data-action="wizard-slot"]').first.click()
                page.locator('[data-action="wizard-next"]').click()
                page.wait_for_selector('[data-form="wizard-details"]')
                for name,value in {'customer_name':'Photo Booking Customer','email':'photo-ui@example.com','phone':'5125550156','vehicle_notes':'2024 Test Sedan','address':'120 Example Avenue, Austin, TX','state':'TX'}.items():
                    page.locator('[data-form="wizard-details"] [name="'+name+'"]').fill(value)
                page.locator('[name="accepted_policy"]').check()
                page.locator('#quote-gallery').set_input_files({'name':'car.jpg','mimeType':'image/jpeg','buffer':image_bytes((1200,800))})
                page.wait_for_function("S.wizard.photos.length===1&&!PhotoState.busy")
                # Simulate a lost photo response after the backend committed. Same request
                # must retry safely, without a duplicate booking or duplicate photo.
                page.evaluate("""() => {const original=window.fetch;window.__dropPhotoResponse=true;window.fetch=async(path,opts={})=>{const result=await original(path,opts);if(window.__dropPhotoResponse&&String(path).includes('/photos')&&opts.method==='POST'){window.__dropPhotoResponse=false;throw Error('Simulated connection interruption');}return result;};}""")
                page.locator('[data-form="wizard-details"] button[type="submit"]').click()
                page.wait_for_selector('#continue-saved-request')
                assert 'already saved' in page.locator('.form-error').inner_text()
                with db() as sql:
                    count=sql.execute("SELECT count(*) FROM bookings WHERE email='photo-ui@example.com'").fetchone()[0]
                    bid=sql.execute("SELECT id FROM bookings WHERE email='photo-ui@example.com'").fetchone()[0]
                    assert count==1
                page.locator('[data-form="wizard-details"] button[type="submit"]').click()
                page.wait_for_selector('#customer-experience')
                with db() as sql:
                    assert sql.execute("SELECT count(*) FROM bookings WHERE email='photo-ui@example.com'").fetchone()[0]==1
                    assert sql.execute('SELECT count(*) FROM job_photos WHERE booking_id=?',(bid,)).fetchone()[0]==1
                checks.append('Normal and heavy quote ZIP rejection; optional photo booking; lost upload response is idempotent')

                page.evaluate("go('/app/bookings')");page.wait_for_selector('[data-action="booking-open"]')
                page.evaluate('(id)=>bookingModal(id)',pending['id'])
                page.locator('[data-action="thread-open"]').click()
                page.wait_for_selector('#chat-message')
                page.locator('#chat-message').fill('Please explain the stain <img src=x onerror=alert(1)>')
                page.locator('[data-form="chat-send"] button[type="submit"]').click()
                page.wait_for_function("document.querySelector('#chat-log').textContent.includes('Please explain')")
                assert page.locator('#chat-log img').count()==0
                page.locator('#chat-message').fill('Unsent private draft')
                page.locator('[data-action="modal-close"]').click()
                page.wait_for_selector('dialog.app-confirm')
                page.locator('dialog.app-confirm [data-answer="no"]').click()
                assert page.locator('#chat-message').input_value()=='Unsent private draft'
                page.locator('#chat-message').fill('')
                page.evaluate('closeModal()')
                page.evaluate('(url)=>go(url)',pending['manage_url'])
                page.wait_for_selector('#customer-experience [data-action="thread-open"]')
                page.locator('#customer-experience [data-action="thread-open"]').click()
                page.wait_for_function("document.querySelector('#chat-log').textContent.includes('Please explain')")
                page.locator('#chat-message').fill('The stain is on the back seat.')
                page.locator('[data-form="chat-send"] button[type="submit"]').click()
                page.wait_for_function("document.querySelector('#chat-log').textContent.includes('back seat')")
                page.screenshot(path=str(args.output/'customer-chat-desktop.png'),full_page=True)
                page.evaluate('closeModal()')
                checks.append('Private owner and customer chat, text escaping, in-app unsent-draft protection')

                page.evaluate("go('/app/customers')");page.wait_for_selector('#customer-search')
                page.locator('#customer-search').fill('pending-ui@example.com')
                assert page.locator('#customer-cards [data-action="customer-open"]').count()==1
                page.locator('#customer-cards [data-action="customer-open"]').click()
                page.wait_for_selector('[data-form="customer-notes"]')
                page.locator('[data-form="customer-notes"] textarea').fill('Prefers the rear parking bay.')
                page.locator('[data-form="customer-notes"] button[type="submit"]').click()
                page.wait_for_function("document.querySelector('[data-form=\"customer-notes\"]').dataset.unsaved==='false'")
                assert c.get('/api/customers/detail?email=pending-ui@example.com').json()['notes']=='Prefers the rear parking bay.'
                page.evaluate('closeModal()')
                checks.append('Customer search, combined history and saved private notes')

                page.evaluate("go('/app/bookings')");page.wait_for_selector('[data-action="booking-open"]')
                page.evaluate('(id)=>bookingModal(id)',inv['booking_id'])
                page.locator('[data-action="request-review"]').click()
                page.wait_for_timeout(300);page.evaluate('closeModal()')
                page.evaluate('(url)=>go(url)','/manage/'+completed_manage)
                page.wait_for_selector('[data-form="customer-review"]')
                page.locator('#review-rating').select_option('4')
                page.locator('#review-comment').fill('Careful work and clear communication.')
                page.locator('[data-form="customer-review"] button[type="submit"]').click()
                page.wait_for_selector('h3:text("Thank you for your review")')
                checks.append('Completed job review request and customer submission')

                page.evaluate("go('/app/invoices')");page.wait_for_selector('[data-action="invoice-cash"]')
                page.locator('[data-action="invoice-cash"][data-id="'+inv['id']+'"]').click()
                page.wait_for_selector('[data-form="record-payment"]')
                page.set_viewport_size({'width':390,'height':844})
                page.screenshot(path=str(args.output/'record-payment-mobile.png'),full_page=True)
                assert not page.evaluate('document.documentElement.scrollWidth>innerWidth+2')
                page.locator('#receipt-method').select_option('venmo')
                page.locator('[data-form="record-payment"] [name="reference"]').fill('Venmo reference 123')
                page.locator('[data-form="record-payment"] [name="confirmed"]').check()
                page.locator('[data-form="record-payment"] button[type="submit"]').click()
                page.wait_for_function("!document.querySelector('#modal').open")
                with db() as sql:assert sql.execute('SELECT method FROM invoice_receipts WHERE invoice_id=?',(inv['id'],)).fetchone()[0]=='venmo'
                checks.append('Branded mobile record-payment dialog and explicitly confirmed Venmo receipt')

                page.evaluate("go('/app/bookings')");page.wait_for_selector('[data-action="booking-open"]')
                page.evaluate('(id)=>bookingModal(id)',pending['id'])
                page.locator('[data-action="proof-open"]').click()
                page.wait_for_selector('#photo-category');assert page.locator('#photo-category').input_value()=='payment_proof'
                page.locator('#job-gallery').set_input_files({'name':'proof.jpg','mimeType':'image/jpeg','buffer':image_bytes((600,500))})
                page.wait_for_function('PhotoState.pending.length===1&&!PhotoState.busy')
                page.locator('[data-action="photos-save"]').click()
                page.wait_for_function('PhotoState.pending.length===0&&!PhotoState.uploadBusy')
                photos=c.get('/api/bookings/'+pending['id']+'/photos').json()['photos'];assert photos[0]['category']=='payment_proof'
                with db() as sql:assert sql.execute('SELECT payment_status FROM bookings WHERE id=?',(pending['id'],)).fetchone()[0]=='unpaid'
                page.evaluate('closeModal()')
                checks.append('Payment proof photo does not mark a job paid')

                page.evaluate('(url)=>go(url)',requested['conversation_url'])
                page.wait_for_selector('[data-action="thread-open"]')
                page.locator('[data-action="thread-open"]').click()
                page.wait_for_selector('#chat-message');page.locator('#chat-message').fill('There is heavy pet hair.')
                page.locator('[data-form="chat-send"] button[type="submit"]').click()
                page.wait_for_function("document.querySelector('#chat-log').textContent.includes('pet hair')")
                page.evaluate('closeModal()')
                checks.append('Quote request conversation before any offer or payment')

                for width in (320,390,1440):
                    page.set_viewport_size({'width':width,'height':900})
                    for route,selector in [('/app/customers','#customer-search'),('/app/messages','.conversation-row'),('/app/customer-payments','#square-location-choice'),('/app/settings','#city-search'),('/app/services','h1'),('/app/billing','h1'),('/book/northline','#booking-zip')]:
                        page.evaluate('(url)=>go(url)',route);page.wait_for_selector(selector);page.wait_for_timeout(120)
                        overflow=page.evaluate('document.documentElement.scrollWidth>innerWidth+2')
                        layouts.append({'width':width,'route':route,'horizontal_overflow':overflow})
                        if overflow:page.screenshot(path=str(args.output/('overflow-'+str(width)+'-'+route.split('/')[-1]+'.png')),full_page=True)
                        if width==390 and route in ['/app/customers','/app/customer-payments','/app/settings']:
                            page.screenshot(path=str(args.output/('mobile-'+route.split('/')[-1]+'.png')),full_page=True)
                        if route=='/app/customer-payments':
                            assert page.locator('a:text("Get Square")').get_attribute('href')=='https://squareup.com/r/EXAMPLE-TEST-ONLY'
                            assert page.locator('a:text("Get Square")').get_attribute('target')=='_blank'
                checks.append('Referral is separate, and 21 route/viewport layout checks')
                assert not errors,errors
                assert not dialogs,dialogs
                assert all(not x['horizontal_overflow'] for x in layouts),layouts
                browser.close()
        finally:
            try:next(gen)
            except StopIteration:pass
    result={'result':'passed','backend':'real temporary TestClient API and SQLite','checks':checks,'layouts':layouts,'page_errors':errors,'native_dialogs':dialogs,'limitations':['No deployed HTTPS, cookie or CSP browser verification','No real Square/Paddle/email','Chromium viewport emulation, not a physical camera or iOS Safari']}
    (args.output/'experience-browser.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))

if __name__=='__main__':main()
