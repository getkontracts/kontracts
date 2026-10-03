"""Operator-only customer domain activation. Run with the app's DATA_DIR and env.

Verify ownership in the UI, provision the hostname and its certificate at the
hosting edge, then inspect its HTTPS /.well-known/kontracts-domain response.
No client-facing endpoint may activate a domain or bypass this operator gate.
"""
import argparse
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import config,brand_domains
from server.db import db,initialize,now,audit


def activate(hostname,confirmed=False):
    hostname=brand_domains.normalize_domain(hostname)
    if not confirmed:raise ValueError('Test the exact HTTPS hostname first, then pass --confirm-https-tested.')
    with db(True) as c:
        row=c.execute('SELECT * FROM shop_domains WHERE hostname=?',(hostname,)).fetchone()
        if not row or not row['verified_at']:raise ValueError('The business must verify its DNS TXT ownership record first.')
        if row['verified_at']<now()-7*86400:raise ValueError('DNS ownership must be checked again; the last verification is over seven days old.')
        c.execute('UPDATE shop_domains SET active=1 WHERE hostname=?',(hostname,))
        audit(c,row['shop_id'],'domain.activated')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['list','activate','deactivate','caddyfile'])
    parser.add_argument('hostname',nargs='?')
    parser.add_argument('--confirm-https-tested',action='store_true')
    args=parser.parse_args();initialize()
    if args.action=='list':
        with db() as c:
            for row in c.execute('SELECT d.*,s.name FROM shop_domains d JOIN shops s ON s.id=d.shop_id ORDER BY hostname'):
                print(row['hostname'], '|', 'active' if row['active'] else 'verified' if row['verified_at'] else 'pending', '|', row['name'])
    elif args.action=='caddyfile':
        primary=urlsplit(config.BASE_URL).hostname
        if not primary or primary in ('localhost','127.0.0.1'):parser.error('Set BASE_URL to the real main HTTPS address first.')
        with db() as c:hosts=[primary]+[row[0] for row in c.execute('SELECT hostname FROM shop_domains WHERE verified_at IS NOT NULL ORDER BY hostname')]
        print('{\n    email {$ACME_EMAIL}\n}\n'+', '.join(dict.fromkeys(hosts))+' {\n    encode zstd gzip\n    request_body {\n        max_size 26MB\n    }\n    reverse_proxy app:8000\n    # Do not enable access logs containing private booking tokens.\n}')
    elif not args.hostname:parser.error('Specify a hostname.')
    elif args.action=='activate':
        try:activate(args.hostname,args.confirm_https_tested)
        except ValueError as exc:parser.error(str(exc))
        print('Activated:',args.hostname)
    else:
        with db(True) as c:
            row=c.execute('SELECT * FROM shop_domains WHERE hostname=?',(args.hostname,)).fetchone()
            if not row:parser.error('Unknown hostname.')
            c.execute('UPDATE shop_domains SET active=0 WHERE hostname=?',(args.hostname,));audit(c,row['shop_id'],'domain.deactivated')
        print('Deactivated. Remove the hostname from hosting and DNS when appropriate.')

if __name__=='__main__':main()
