"""In-process UI/API acceptance check; no live providers or deployed HTTPS.
Run: python tests/browser_workflow.py --chromium /usr/bin/chromium --output ./browser-evidence
"""
import argparse,json,re,sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from pytest import MonkeyPatch
from tests.conftest import client,owner
from tests.test_workflow import request_job,approve
from tests.browser_support import mount
from server.db import db,now
from server.security import decrypt

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--chromium');parser.add_argument('--output',type=Path,default=Path('browser-evidence'));a=parser.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as temp,MonkeyPatch.context() as mp:
        generator=client.__wrapped__(Path(temp),mp);c=next(generator)
        try:
            c=owner.__wrapped__(c,mp);b=request_job(c);approve(c,b)
            with db(True) as sql:sql.execute('UPDATE bookings SET start_ts=?,end_ts=?,busy_until=? WHERE id=?',(now()-7200,now()-3600,now()-1800,b['id']))
            with sync_playwright() as p:
                args={'headless':True,'args':['--no-sandbox','--disable-dev-shm-usage']}
                if a.chromium:args['executable_path']=a.chromium
                browser=p.chromium.launch(**args);page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda x:errors.append(str(x)))
                mount(page,c,'/app/bookings');page.wait_for_selector('[data-action="booking-open"]')
                # Customer contact must reach this business, not platform support.
                with db() as sql:
                    from server import core
                    booking_row=sql.execute('SELECT * FROM bookings WHERE id=?',(b['id'],)).fetchone()
                    shop=sql.execute('SELECT * FROM shops WHERE id=?',(booking_row['shop_id'],)).fetchone()
                    from server.mail import shop_contact
                    contact=shop_contact(shop)
                    manage_token=decrypt(booking_row['manage_encrypted'])
                page.evaluate('(url)=>go(url)','/manage/'+manage_token)
                page.wait_for_selector('a:has-text("Contact the business")')
                assert page.locator('a:has-text("Contact the business")').get_attribute('href')=='mailto:'+contact
                page.evaluate("go('/app/bookings')");page.wait_for_selector('[data-action="booking-open"]')
                page.evaluate('(id)=>bookingModal(id)',b['id']);page.locator('[data-action="finish-invoice"]').click()
                page.locator('form[data-form="finish-invoice"] input[name="signer_name"]').fill('Alex Detailer')
                page.locator('form[data-form="finish-invoice"] input[name="consent"]').check();page.locator('form[data-form="finish-invoice"] button[type="submit"]').click()
                page.wait_for_selector('[data-action="invoice-resend"]')
                page.screenshot(path=str(a.output/'live-api-invoices.png'),full_page=True)
                with db() as sql:inv=sql.execute('SELECT * FROM invoices WHERE booking_id=?',(b['id'],)).fetchone();raw=decrypt(inv['token_encrypted'])
                # PDF endpoint verified separately: managed browser navigation is restricted.
                assert c.get('/api/public/invoices/'+raw+'/pdf').status_code==200
                page.evaluate('(url)=>go(url)','/invoice/'+raw);page.wait_for_selector('[data-action="invoice-code"]');page.locator('[data-action="invoice-code"]').click()
                page.wait_for_timeout(250)
                with db() as sql:mail=sql.execute("SELECT body FROM outbox WHERE subject LIKE '%Invoice signing code%' ORDER BY rowid DESC LIMIT 1").fetchone()[0]
                code=re.search(r'code is ([0-9]{6})',mail).group(1)
                page.locator('form[data-form="invoice-sign"] input[name="signer_name"]').fill('Test Customer');page.locator('form[data-form="invoice-sign"] input[name="code"]').fill(code);page.locator('form[data-form="invoice-sign"] input[name="consent"]').check();page.locator('form[data-form="invoice-sign"] button[type="submit"]').click()
                page.wait_for_selector('h1:text("Signed. Saved. Yours.")')
                with db() as sql:assert sql.execute('SELECT status FROM bookings WHERE id=?',(b['id'],)).fetchone()[0]=='completed'
                page.screenshot(path=str(a.output/'live-api-signed.png'),full_page=True);assert not errors,errors
                browser.close()
                result={'result':'passed','backend':'real TestClient handlers with temporary SQLite','flows':['customer contact link','owner finish/sign','PDF retrieval','email-code outbox','customer signature','completed job'],'browser_errors':errors,'not_tested':['live email/payment','HTTPS/cookies/CSP','actual file navigation']}
                (a.output/'workflow-browser.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
        finally:
            try:next(generator)
            except StopIteration:pass
if __name__=='__main__':main()
