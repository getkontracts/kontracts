"""Offline demo additions, display-copy audit and explicit exit navigation.
No external connection is made; the explicit exit click is observed and then canceled by the test.
"""
import argparse,json,re
from pathlib import Path
from playwright.sync_api import sync_playwright
ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--chromium',default='/usr/bin/chromium');ap.add_argument('--output',type=Path,default=Path('browser-evidence'));args=ap.parse_args();args.output.mkdir(parents=True,exist_ok=True)
root=Path(__file__).resolve().parents[1]
def wait(page,expression):
    # Poll through automation without requiring unsafe-eval in the demo CSP.
    for _ in range(80):
        if page.evaluate(expression):return
        page.wait_for_timeout(100)
    raise AssertionError('Timed out: '+expression)
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=args.chromium,headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
    page=browser.new_page(viewport={'width':390,'height':844});page.set_default_timeout(10000)
    errors=[];requests=[];layouts=[];copy={}
    page.on('pageerror',lambda e:errors.append(str(e)));page.on('request',lambda r:requests.append(r.url))
    page.set_content((root/'Kontracts-Preview.html').read_text(),wait_until='load');page.wait_for_selector('#main')
    page.evaluate("go('/app/settings')");page.wait_for_selector('#city-search');page.locator('#city-search').fill('Austin');page.wait_for_selector('.city-result')
    page.locator('[data-area-mode][value="city"]').check();page.locator('[data-action="areas-save"]').click();wait(page,"Experience.areaDirty===false")
    page.evaluate("go('/app/bookings')");page.wait_for_selector('[data-action="booking-open"]')
    bid=page.evaluate('S.bookings[0].id');page.evaluate('(id)=>bookingModal(id)',bid)
    page.locator('[data-action="thread-open"]').click();page.wait_for_selector('#chat-message');page.locator('#chat-message').fill('Do you have water access?')
    page.locator('[data-form="chat-send"] button[type="submit"]').click();wait(page,"document.querySelector('#chat-log').textContent.includes('water access')")
    page.evaluate('closeModal()')
    routes=['/app/customers','/app/messages','/app/customer-payments','/app/billing','/app/services','/app/setup','/app/help','/app/settings','/terms','/privacy','/register']
    for width in (320,390,1440):
        page.set_viewport_size({'width':width,'height':900})
        for route in routes:
            page.evaluate('(route)=>go(route)',route);page.wait_for_timeout(170)
            assert page.locator('#main').count(),route
            overflow=page.evaluate('document.documentElement.scrollWidth>innerWidth+2')
            layouts.append({'route':route,'width':width,'horizontal_overflow':overflow})
            if width==390:
                words=sorted(set(re.findall(r'\b[A-Za-z]+(?:[-\u2010-\u2015][A-Za-z]+)+\b',page.locator('body').inner_text())))
                if words:copy[route]=words
    page.wait_for_selector('h1:text("Ready for your own workspace?")')
    assert page.locator('form[data-form="register"]').count()==0
    exit=page.locator('a[data-demo-exit]').last
    assert exit.get_attribute('href')=='https://getkontracts.app/register'
    page.screenshot(path=str(args.output/'demo-exit-mobile.png'),full_page=True)
    assert not [r for r in requests if r.startswith(('http:','https:'))],requests
    assert not errors,errors
    assert all(not l['horizontal_overflow'] for l in layouts),layouts
    # Observe the event after app listeners. Browser navigation itself is restricted
    # in this environment, so test the destination and that no app guard cancels it.
    page.evaluate("""() => {window.addEventListener('click',event=>{const a=event.target.closest('[data-demo-exit]');if(a){window.__exitCheck={href:a.href,preventedByApp:event.defaultPrevented};event.preventDefault();}},{once:true});}""")
    exit.click()
    assert page.evaluate("window.__exitCheck.href==='https://getkontracts.app/register'&&!window.__exitCheck.preventedByApp")
    result={'result':'passed','city_edit':'passed','simulated_chat':'passed','demo_signup_has_no_account_form':True,'exit_link':'correct real destination; app allows click; test canceled actual navigation','layouts':layouts,'page_errors':errors,'hyphenated_display_words':copy,'limitations':'In-memory demo, not live payment/account creation; Chromium viewport emulation only'}
    (args.output/'preview-experience.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2));browser.close()
