import argparse,json
from pathlib import Path
from playwright.sync_api import sync_playwright
parser=argparse.ArgumentParser(description='Offline demo browser checks; no live services.');parser.add_argument('--chromium');parser.add_argument('--output',type=Path,default=Path('browser-evidence'));args=parser.parse_args()
root=Path(__file__).resolve().parents[1];evidence=args.output;evidence.mkdir(parents=True,exist_ok=True)
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=args.chromium,headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
    page=browser.new_page(viewport={'width':1440,'height':1000})
    errors=[];requests=[];page.on('pageerror',lambda e:errors.append(str(e)));page.on('request',lambda r:requests.append(r.url))
    page.set_content((root/'Kontracts-Preview.html').read_text(),wait_until='load')
    page.wait_for_timeout(600)
    page.screenshot(path=str(evidence/'landing-final.png'),full_page=True)
    page.evaluate("go('/app/bookings')");page.wait_for_timeout(400)
    identifier=page.evaluate("S.bookings.find(b=>b.status==='confirmed'&&b.start_ts<Date.now()/1000).id")
    page.evaluate('(id)=>bookingModal(id)',identifier)
    page.locator('[data-action="finish-invoice"]').click()
    page.locator('form[data-form="finish-invoice"] input[name="signer_name"]').fill('Demo Detailer')
    page.locator('form[data-form="finish-invoice"] input[name="consent"]').check()
    page.locator('form[data-form="finish-invoice"] button[type="submit"]').click()
    page.wait_for_timeout(400)
    page.screenshot(path=str(evidence/'invoices-desktop.png'),full_page=True)
    page.locator('[data-action="invoice-resend"]').first.click()
    page.wait_for_timeout(650)
    assert page.locator('text=SIMULATION ONLY.').count()==1
    assert page.locator('text=PDF disabled in demo').count()==1
    page.locator('[data-action="invoice-code"]').click()
    page.locator('form[data-form="invoice-sign"] input[name="signer_name"]').fill('Demo Customer')
    page.locator('form[data-form="invoice-sign"] input[name="code"]').fill('123456')
    page.locator('form[data-form="invoice-sign"] input[name="consent"]').check()
    page.locator('form[data-form="invoice-sign"] button[type="submit"]').click()
    page.wait_for_timeout(400)
    assert 'Signed. Saved. Yours.' in page.locator('h1').inner_text()
    page.screenshot(path=str(evidence/'signed-demo-desktop.png'),full_page=True)
    routes=['/','/how-payments-work','/app','/app/bookings','/app/invoices','/app/billing','/app/settings','/book/northline']
    results=[]
    for width in [1440,390]:
        page.set_viewport_size({'width':width,'height':900})
        for index,route in enumerate(routes):
            page.evaluate('(route)=>go(route)',route);page.wait_for_timeout(180)
            overflow=page.evaluate('document.documentElement.scrollWidth>innerWidth+2')
            results.append({'width':width,'route':route,'horizontal_overflow':overflow,'title':page.title()})
            if width==390 and route in ['/','/app','/how-payments-work','/book/northline']:
                page.screenshot(path=str(evidence/('mobile-'+str(index)+'.png')),full_page=True)
    out={'mode':'set_content offline sandbox; not deployed HTTPS or actual file navigation','signature_simulation':'passed','page_errors':errors,'external_requests':[x for x in requests if x.startswith(('http:','https:'))],'routes':results}
    assert not errors,errors
    assert not out['external_requests'],out['external_requests']
    assert all(not item['horizontal_overflow'] for item in results),results
    (evidence/'browser-final.json').write_text(json.dumps(out,indent=2))
    print(json.dumps(out,indent=2))
    browser.close()
