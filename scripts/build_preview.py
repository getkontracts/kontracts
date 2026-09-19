"""Build a self-contained, clearly labeled offline design preview from synthetic data."""
import os,sys,json,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
os.environ['TESTING']='true';os.environ['DEMO_MODE']='true';os.environ['APP_ENV']='development'
for key, value in {
    'RESEND_API_KEY':'', 'SMTP_HOST':'', 'SMTP_USER':'', 'SMTP_PASSWORD':'',
    'PADDLE_API_KEY':'', 'PADDLE_CLIENT_TOKEN':'', 'PADDLE_WEBHOOK_SECRET':'', 'PADDLE_PRICE_ID':'',
    'SQUARE_APPLICATION_ID':'', 'SQUARE_APPLICATION_SECRET':'', 'SQUARE_WEBHOOK_SIGNATURE_KEY':'',
    'PADDLE_ENV':'sandbox', 'SQUARE_ENV':'sandbox', 'GOOGLE_CLIENT_ID':'', 'GOOGLE_CLIENT_SECRET':'',
    'APP_SECRET':'offline-preview-only-not-for-production-' * 2, 'BASE_URL':'http://localhost:8000',
    'MAIL_ENABLED':'false', 'MAIL_FROM':'', 'SMTP_FROM':'', 'MAIL_SENDER_DOMAIN':'getkontracts.app',
    'MAIL_ACCOUNTS_FROM':'Kontracts <accounts@getkontracts.app>',
    'MAIL_NOTIFICATIONS_FROM':'Kontracts <notifications@getkontracts.app>',
    'MAIL_BILLING_FROM':'Kontracts Billing <billing@getkontracts.app>',
    'SUPPORT_EMAIL':'demo@example.com', 'MAIL_REPLY_TO':'demo@example.com',
    'LEGAL_NAME':'', 'LEGAL_ADDRESS':'', 'LEGAL_TERMS_PATH':'', 'LEGAL_PRIVACY_PATH':'',
}.items():
    os.environ[key] = value
from fastapi.testclient import TestClient
from server import config
from server.app import app

def main():
    with tempfile.TemporaryDirectory() as folder:
        config.DATA=Path(folder);config.DB=str(Path(folder)/'preview.sqlite3')
        with TestClient(app,headers={'X-Requested-With':'Kontracts'}) as c:
            csrf=c.post('/api/auth/demo').json()['csrf'];c.headers['X-CSRF-Token']=csrf
            seed={'config':c.get('/api/config').json(),'me':c.get('/api/auth/me').json(),'dashboard':c.get('/api/dashboard').json(),
                'bookings':c.get('/api/bookings').json()['bookings'],'quotes':c.get('/api/quotes').json()['quotes'],'blocks':[], 'branding':c.get('/api/branding').json()}
    seed['config']['platform_url']='https://preview.invalid'
    seed['me']['shop']['booking_url']='https://preview.invalid/book/'+seed['me']['shop']['slug']
    seed['me']['shop']['canonical_booking_url']=seed['me']['shop']['booking_url']
    seed['branding']['booking_url']=seed['me']['shop']['booking_url']
    seed['branding']['canonical_booking_url']=seed['me']['shop']['booking_url']
    seed['config'].update(paddle_client_token='',support_email='demo@example.com',legal_name='',legal_address='',terms_text='',privacy_text='')
    seed['me']['csrf']='offline-only';seed['me']['shop']['billing_configured']=False;seed['me']['shop']['square_configured']=False
    js=(ROOT/'web/assets/release.js').read_text()+'\n'+(ROOT/'web/assets/branding.js').read_text()+'\n'+(ROOT/'web/assets/photos.js').read_text()+'\n'+(ROOT/'web/assets/workflow.js').read_text()+'\n'+(ROOT/'web/assets/app.js').read_text()
    js=js.replace('location.pathname','window.__previewURL.pathname').replace('location.search','window.__previewURL.search').replace('location.origin',"'https://preview.invalid'").replace('location.host',"'kontracts.preview'")
    js=js.replace("history.replaceState({},'',url)",'window.__previewGo(url)').replace("history.pushState({},'',url)",'window.__previewGo(url)').replace('window.location.assign','window.__previewAssign')
    js=js.replace("return '/api/photos/'+encodeURIComponent(id)+(thumbnail?'?thumbnail=true':'');","return window.__previewPhotoUrls[id]||('/api/photos/'+encodeURIComponent(id)+(thumbnail?'?thumbnail=true':''));")
    html=(ROOT/'web/index.html').read_text().replace('<script defer src="/assets/release.js"></script>','').replace('<script defer src="/assets/branding.js"></script>','').replace('<link rel="icon" href="/assets/favicon.svg" type="image/svg+xml">','')
    css=(ROOT/'web/assets/app.css').read_text()+'.preview-notice{padding:10px 16px;background:#f9e8ba;color:#56461c;font:12px system-ui;text-align:center;position:relative;z-index:40}.preview-notice a{font-weight:650;text-decoration:underline;margin-left:14px}.sidebar{top:38px}.preview-notice~#app{min-height:calc(100vh - 38px)}'
    html=html.replace('<link rel="stylesheet" href="/assets/app.css">','<style>'+css+'</style>').replace('<script defer src="/assets/app.js"></script>','')
    html=html.replace('<script defer src="/assets/photos.js"></script>','').replace('<script defer src="/assets/workflow.js"></script>','')
    html=html.replace('<body>','<body><div class="preview-notice">DEMO ONLY. Synthetic data, reset on reload. No orders, payments or email. Do not enter personal information. <a href="#/">Home</a><a href="#/app">Dashboard</a><a href="#/book/northline">Try booking</a></div>')
    script='const PREVIEW_SEED='+json.dumps(seed).replace('</','<\\/')+';\n'+(ROOT/'preview/adapter.js').read_text()+'\n'+js
    import base64
    favicon='data:image/svg+xml;base64,'+base64.b64encode((ROOT/'web/assets/favicon.svg').read_bytes()).decode()
    html=html.replace('</head>','<link rel="icon" href="'+favicon+'"><meta http-equiv="Content-Security-Policy" content="default-src &apos;none&apos;; script-src &apos;unsafe-inline&apos;; style-src &apos;unsafe-inline&apos;; img-src data: blob:; connect-src &apos;none&apos;; form-action &apos;none&apos;; base-uri &apos;none&apos;"></head>')
    script=script.replace('/assets/favicon.svg',favicon).replace('/assets/booking-icon.svg',favicon)
    html=html.replace('</body>','<script>'+script.replace('</script','<\\/script')+'</script></body>')
    dest=ROOT/'Kontracts-Preview.html';dest.write_text(html,encoding='utf-8');print(dest)

if __name__=='__main__':main()
