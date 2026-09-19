"""Local readiness report; no API keys, provider requests or migrations.
python scripts/doctor.py --strict [--square-required]
"""
from pathlib import Path
import argparse,importlib.metadata,json,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--strict',action='store_true');parser.add_argument('--square-required',action='store_true');args=parser.parse_args();checks=[]
    def add(name,ready,note):checks.append({'check':name,'ready':bool(ready),'note':note})
    try:
        from server import config,plans,postal
        add('Production mode',config.PRODUCTION,'Production requires HTTPS, real mail and reviewed policies.')
        add('HTTPS',config.BASE_URL.startswith('https://'),'Canonical public origin.')
        add('Server demo disabled',not config.DEMO,'The isolated /demo remains available.')
        add('Patched framework',tuple(map(int,importlib.metadata.version('starlette').split('.')[:3]))>=(1,6,0),'Install requirements.txt; run the release tests and dependency audit.')
        z=postal.registry();add('Nationwide US ZIP registry',not z.get('fixture') and len(z['codes'])>=30000,'Run python scripts/update_zipcodes.py; no runtime ZIP API.')
        add('Email provider and channel senders',bool(config.MAIL_ENABLED and (config.RESEND_API_KEY or config.SMTP_HOST)),'Verify MAIL_SENDER_DOMAIN; use scripts/email_admin.py check and configure support forwarding separately.')
        add('Paddle subscription',plans.configured() and config.PADDLE_ENV=='production','One active USD49 monthly price; validate checkout and signed webhook events.')
        if args.square_required:add('Square online payments',bool(config.SQUARE_ID and config.SQUARE_SECRET and config.SQUARE_WEBHOOK) and config.SQUARE_ENV=='production','Authorize a US Square merchant; test signed payments and refunds.')
        add('Reviewed policies',bool(config.TERMS_TEXT and config.PRIVACY_TEXT and config.LEGAL_NAME and config.LEGAL_ADDRESS),'Publish reviewed legal text and an actual retention policy.')
        add('Demo artifact',(ROOT/'Kontracts-Preview.html').is_file(),'The safe static demo must be shipped.')
    except Exception as exc:add('Configuration',False,str(exc))
    print(json.dumps({'checks':checks,'note':'Configured is not live-verified. Encrypted storage, tested backups, monitoring and independent review are still operator responsibilities.'},indent=2))
    return int(args.strict and any(not x['ready'] for x in checks))
if __name__=='__main__':sys.exit(main())
