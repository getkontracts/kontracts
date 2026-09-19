"""Retained, dual e-signed invoices. No external signing service or card handling.
Electronic signatures evidence intent and email access, not verified civil identity.
PDFs are not certificate/PAdES signatures. HMAC seals detect application-record tampering.
"""
import base64
import hashlib
import hmac
import io
import json
import re
import secrets
from datetime import datetime, timezone
from html import escape
from typing import Literal
from fastapi import Depends, HTTPException, Request, Response
from pydantic import Field
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_RIGHT
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from . import config, core, branding, payments
from .db import db, now, uid, packed, audit, queue_mail
from .mail import shop_contact
from .models import Model
from .security import token, digest, encrypt, decrypt, constant_equal, rate_limit

OWNER_CONSENT='I certify that the listed service was performed and I intend my typed name to electronically sign this invoice. Payment is recorded separately.'
CUSTOMER_CONSENT=('I have reviewed and can open, save and print this PDF invoice. I consent to receive this invoice electronically and intend my typed name to sign my acknowledgement of the listed completed service. Signing does not mean payment was made and does not waive statutory rights. I may request a free paper copy, update my email, or withdraw consent for future records by contacting the mailbox shown on the invoice. A current web browser, email access and a PDF reader are required. Withdrawal does not undo this signed record.')

class Completion(Model):
    signer_name: str = Field(min_length=2,max_length=80)
    consent: Literal[True]
    received_in_person: bool = False
class Signature(Model):
    signer_name: str = Field(min_length=2,max_length=80)
    consent: Literal[True]
    code: str = Field(pattern=r'^[0-9]{6}$')
    document_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
class Received(Model):
    confirmation: Literal['PAYMENT RECEIVED']


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def evidence(request,identity,consent,name):
    return {'name':name,'identity':identity,'at':now(),'consent':consent,
            'ip_fingerprint':hmac.new(config.SECRET.encode(),('invoice-ip:'+(request.client.host if request.client else 'unknown')).encode(),hashlib.sha256).hexdigest(),
            'browser_fingerprint':digest(request.headers.get('user-agent','')[:512])}
def seal(payload,pdf_hash):
    key=hmac.new(config.SECRET.encode(),b'kontracts-invoice-seal-v1',hashlib.sha256).digest()
    return hmac.new(key,(payload+'\n'+pdf_hash).encode(),hashlib.sha256).hexdigest()
def money(cents): return f'${cents/100:,.2f}'
def utc(ts): return datetime.fromtimestamp(ts,timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')


def make_pdf(value):
    """Only escaped text enters ReportLab: never untrusted markup, URLs or files."""
    output=io.BytesIO()
    styles=getSampleStyleSheet()
    styles.add(ParagraphStyle('SmallText',fontName='Helvetica',fontSize=8.5,leading=12,spaceAfter=6))
    styles.add(ParagraphStyle('InvoiceTitle',fontName='Helvetica-Bold',fontSize=25,leading=30,spaceAfter=12))
    styles.add(ParagraphStyle('Amount',fontName='Helvetica-Bold',fontSize=11,leading=15,alignment=TA_RIGHT))
    def p(s,style='BodyText'): return Paragraph(escape(str(s)).replace('\n','<br/>'),styles[style])
    doc=SimpleDocTemplate(output,pagesize=(612,792),rightMargin=45,leftMargin=45,topMargin=44,bottomMargin=48,
                          title=value['number']+' - '+value['business']['name'],author=value['business']['name'],pageCompression=1)
    business=value['business']; customer=value['customer']
    parts=[p(business['name'],'Title'),p('SERVICE INVOICE','InvoiceTitle'),
           p(value['number']+' | Issued '+utc(value['issued_at']),'SmallText'),
           p('Customer signed' if value.get('customer_signature') else 'Detailer signed - awaiting customer acknowledgement','Heading3'),Spacer(1,12)]
    columns=[[p('BILLED TO','Heading4'),p('DETAILER','Heading4')],
             [p(customer['name']+'\n'+customer['email']+'\n'+customer['address']+'\n'+customer['state']+' '+customer['zip']+', United States'),
              p(business['name']+'\n'+business['city']+'\n'+business['phone']+'\nAll correspondence: '+business['correspondence_email'])]]
    table=Table(columns,colWidths=[270,252]);table.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),9)]));parts.extend([table,Spacer(1,14)])
    parts.extend([p('Job '+value['booking_id'][:8].upper()+' | '+value['appointment'],'SmallText'),p('Vehicle: '+value['vehicle'],'SmallText')])
    rows=[[p('SERVICE','Heading4'),p('AMOUNT','Heading4')]]
    rows.extend([[p(line['name']),p(money(line['amount']),'Amount')] for line in value['lines']])
    rows.extend([[p('Subtotal'),p(money(value['subtotal']),'Amount')],[p('Tax'),p(money(value['tax']),'Amount')],
                 [p('Total','Heading3'),p(money(value['total']),'Amount')],
                 [p('Payments recorded at issue'),p(money(value['paid_at_issue']),'Amount')],
                 [p('Balance at issue','Heading3'),p(money(max(0,value['total']-value['paid_at_issue'])),'Amount')]])
    table=Table(rows,colWidths=[397,125],repeatRows=1,hAlign='LEFT');table.setStyle(TableStyle([
        ('VALIGN',(0,0),(-1,-1),'TOP'),('BACKGROUND',(0,0),(-1,0),colors.HexColor('#edf3ef')),
        ('LINEBELOW',(0,0),(-1,0),0.6,colors.HexColor('#d5dfd9')),('BOTTOMPADDING',(0,0),(-1,-1),9),('TOPPADDING',(0,0),(-1,-1),8)]))
    parts.extend([table,Spacer(1,12),p('Payment method: '+('Pay the detailer through Square.' if value['payment_method']=='square' else 'Pay the detailer in person. No online payment link.'),'SmallText'),
                  p('Payment status above is a snapshot, not a guarantee of settlement. Later payments are recorded separately. Kontracts does not receive the service payment or charge a booking commission.','SmallText')])
    for label,key in [('Detailer electronic signature','detailer_signature'),('Customer electronic signature','customer_signature')]:
        sig=value.get(key);parts.extend([Spacer(1,12),p(label,'Heading3')])
        if sig:
            parts.extend([p(sig['name'],'Heading2'),p(utc(sig['at'])+' | '+sig['identity'],'SmallText'),p(sig['consent'],'SmallText')])
        else: parts.append(p('Pending. The customer must independently review this document and verify access to their email.','SmallText'))
    parts.extend([Spacer(1,12),p('Electronic signatures are recorded acknowledgements, not certificate-based digital signatures. A sealed copy and audit evidence are retained in the application.','SmallText'),
                  p('Document content SHA-256: '+digest(canonical(value)),'SmallText')])
    def footer(canvas,document):
        canvas.saveState();canvas.setFont('Helvetica',8);canvas.setFillColor(colors.HexColor('#55645d'))
        canvas.drawString(45,27,'Kontracts | '+value['number']);canvas.drawRightString(567,27,'Page '+str(document.page));canvas.restoreState()
    doc.build(parts,onFirstPage=footer,onLaterPages=footer)
    return output.getvalue()


def store_version(c,invoice_id,version,value):
    payload=canonical(value);pdf=make_pdf(value);pdf_hash=hashlib.sha256(pdf).hexdigest()
    c.execute('INSERT INTO invoice_versions VALUES(?,?,?,?,?,?,?,?)',
              (uid(),invoice_id,version,encrypt(payload),encrypt(base64.b64encode(pdf).decode()),pdf_hash,seal(payload,pdf_hash),now()))

def read_version(c,invoice_id,version=None):
    row=c.execute('SELECT * FROM invoice_versions WHERE invoice_id=? '+('AND version=? ' if version is not None else '')+'ORDER BY version DESC LIMIT 1',
                  (invoice_id,version) if version is not None else (invoice_id,)).fetchone()
    if not row: raise HTTPException(404,'Invoice document not found.')
    try:
        payload=decrypt(row['payload']);pdf=base64.b64decode(decrypt(row['pdf']),validate=True)
        valid=constant_equal(hashlib.sha256(pdf).hexdigest(),row['pdf_sha256']) and constant_equal(seal(payload,row['pdf_sha256']),row['seal'])
        if not valid: raise ValueError('Invalid seal')
        return row,json.loads(payload),pdf
    except Exception as exc:
        raise HTTPException(409,'Invoice integrity verification failed. Contact support; this record has not been replaced.') from exc

def public_url(inv): return config.BASE_URL+'/invoice/'+decrypt(inv['token_encrypted'])
def owned(c,identifier,shop):
    row=c.execute('SELECT * FROM invoices WHERE id=? AND shop_id=?',(identifier,shop['id'])).fetchone()
    if not row: raise HTTPException(404,'Invoice not found.')
    return row

def accessed(c,raw):
    if not re.fullmatch(r'[A-Za-z0-9_-]{40,100}',raw): raise HTTPException(404,'Invoice link is invalid or expired.')
    inv=c.execute('SELECT * FROM invoices WHERE token_hash=? AND token_expires>?',(digest(raw),now())).fetchone()
    if not inv: raise HTTPException(404,'Invoice link is invalid or expired. Ask the detailer for a fresh link.')
    return inv

def summary(inv,include_token=False):
    result={k:inv[k] for k in ('id','booking_id','number','status','created_at','signed_at','payment_method','total','deposit_received','collected','balance_refunded','payment_review')}
    result['net_received']=max(0,inv['deposit_received']+inv['collected']-inv['balance_refunded'])
    result['balance']=max(0,inv['total']-result['net_received'])
    if include_token: result['url']=public_url(inv)
    return result

def send_invoice(c,inv,shop,kind):
    _,v,_=read_version(c,inv['id'])
    label='Signed invoice saved' if kind=='signed' else 'Invoice ready - review and sign'
    body=(f"{shop['name']}\n{label}: {inv['number']}\n\nService total: {money(inv['total'])}\n"
          f"Review and download your invoice: {public_url(inv)}\n\n")
    if inv['payment_method']=='square' and summary(inv)['balance']:
        body+='After signing, use the invoice page to pay the remaining balance securely to the detailer through Square.\n'
    elif inv['payment_method']=='in_person': body+='Payment is collected by the detailer in person. There is no online payment link.\n'
    body+='\nQuestions, paper copies or electronic-consent changes: '+shop_contact(shop)
    queue_mail(c,inv['id']+':'+kind+':'+inv['token_hash'],v['customer']['email'],shop['name']+' | '+label+' | '+inv['number'],body,shop['id'],
               expires_at=inv['token_expires'],guard_kind='invoice_link',guard_id=inv['id'],guard_value=inv['token_hash'])

def complete(c,b,shop,user,body,request):
    prior=c.execute('SELECT * FROM invoices WHERE booking_id=?',(b['id'],)).fetchone()
    if prior: return summary(prior)
    if shop['is_demo']: raise HTTPException(403,'The demo cannot issue real invoices.')
    if not user['verified']: raise HTTPException(403,'Verify your owner email before issuing invoices.')
    if b['status'] not in ('confirmed','completed') or not b['approved_at']:
        raise HTTPException(409,'Approve and confirm this job before completing it.')
    if b['start_ts']>now(): raise HTTPException(409,'The appointment has not started.')
    if body.received_in_person and b['payment_method']!='in_person':
        raise HTTPException(422,'A Square job cannot be marked paid in person. Wait for verified Square payment.')
    timestamp=now();identifier=uid();raw=token();st=core.settings(shop)
    number='K-'+datetime.fromtimestamp(timestamp,timezone.utc).strftime('%Y%m%d')+'-'+identifier[:8].upper()
    deposited=max(0,b['deposit']-b['refunded']) if b['square_payment'] else 0
    collected=b['total']-deposited if body.received_in_person else 0
    snapshot=json.loads(b['snapshot'])
    value={'schema':1,'number':number,'booking_id':b['id'],'issued_at':timestamp,
        'business':{'name':shop['name'],'city':st['city'],'phone':st['phone'],'correspondence_email':shop_contact(shop)},
        'customer':{'name':b['customer_name'],'email':b['email'],'address':b['address'],'zip':b['zip'],'state':b['state'],'country':'US'},
        'appointment':datetime.fromtimestamp(b['start_ts'],timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
        'vehicle':b['vehicle_notes'] or snapshot['vehicle'],'lines':snapshot['lines'],'subtotal':b['subtotal'],'tax':b['tax'],'total':b['total'],
        'payment_method':b['payment_method'],'paid_at_issue':deposited+collected,
        'detailer_signature':evidence(request,'Authenticated detailer account: '+user['email'],OWNER_CONSENT,body.signer_name),'customer_signature':None}
    c.execute('''INSERT INTO invoices(id,shop_id,booking_id,number,token_hash,token_encrypted,token_expires,status,created_at,payment_method,total,deposit_received,collected)
        VALUES(?,?,?,?,?,?,?,'awaiting_signature',?,?,?,?,?)''',
        (identifier,shop['id'],b['id'],number,digest(raw),encrypt(raw),timestamp+30*86400,timestamp,b['payment_method'],b['total'],deposited,collected))
    store_version(c,identifier,0,value)
    if collected: c.execute('INSERT INTO invoice_receipts VALUES(?,?,?,?,?,?,?)',(uid(),identifier,collected,'in_person','completion:'+identifier,timestamp,user['user_id']))
    c.execute("UPDATE bookings SET status='awaiting_signature',updated_at=? WHERE id=?",(timestamp,b['id']))
    inv=c.execute('SELECT * FROM invoices WHERE id=?',(identifier,)).fetchone()
    send_invoice(c,inv,shop,'issued');audit(c,shop['id'],'invoice.detailer_signed',identifier)
    return summary(inv)


def create_payment(inv):
    """Charge only the balance, into the detailer's own OAuth-connected account."""
    with payments.LOCKS['invoice:'+inv['id']]:
        with db() as c:
            inv=c.execute('SELECT * FROM invoices WHERE id=?',(inv['id'],)).fetchone()
            shop=c.execute('SELECT * FROM shops WHERE id=?',(inv['shop_id'],)).fetchone()
        if inv['payment_method']!='square': raise HTTPException(409,'Pay this detailer in person; online payments are disabled.')
        if inv['status']!='signed': raise HTTPException(409,'Review and sign the invoice before paying the balance.')
        if not summary(inv)['balance']: raise HTTPException(409,'No balance is due.')
        if inv['payment_review']: raise HTTPException(409,'A refund or adjustment requires review with the detailer. Online collection is paused.')
        if not shop['square_access'] or not shop['square_location'] or not config.SQUARE_WEBHOOK: raise HTTPException(503,'Square is not ready for this business. Contact the detailer.')
        if inv['checkout_url']: return inv['checkout_url']
        # Freeze the provider intent before the network call. Later refunds must
        # never change the amount expected for this idempotent checkout.
        with db(True) as c:
            c.execute('UPDATE invoices SET checkout_amount=? WHERE id=? AND checkout_amount=0',(summary(inv)['balance'],inv['id']))
            amount=c.execute('SELECT checkout_amount FROM invoices WHERE id=?',(inv['id'],)).fetchone()[0]
        payload={'idempotency_key':'invoice-'+inv['id'],'order':{'location_id':shop['square_location'],'reference_id':'invoice:'+inv['id'],
                  'line_items':[{'name':shop['name']+' - '+inv['number']+' balance','quantity':'1','base_price_money':{'amount':amount,'currency':'USD'}}]},
                  'checkout_options':{'allow_tipping':False,'redirect_url':public_url(inv)}}
        result=payments.square(shop['id'],'/v2/online-checkout/payment-links',payload)['payment_link']
        payments.validate_square_url(result['url'])
        with db(True) as c:
            c.execute('UPDATE invoices SET square_order=?,square_link=?,checkout_url=? WHERE id=?',(result['order_id'],result['id'],result['url'],inv['id']))
        return result['url']


def apply_payment(event_id,merchant_id,payment):
    """Returns True only for invoice balance payments; invoked after webhook verification."""
    order_id=payment.get('order_id')
    if not order_id: return False
    with db() as c:
        inv=c.execute('SELECT * FROM invoices WHERE square_order=?',(order_id,)).fetchone()
        shop=c.execute('SELECT * FROM shops WHERE square_merchant=?',(merchant_id,)).fetchone()
    if not shop: return False
    if not inv:
        # Recover a provider response lost after successful payment-link creation.
        order=payments.square(shop['id'],'/v2/orders/'+order_id,method='GET').get('order',{})
        ref=order.get('reference_id','')
        if not ref.startswith('invoice:'): return False
        with db() as c: inv=c.execute('SELECT * FROM invoices WHERE id=? AND shop_id=?',(ref[8:],shop['id'])).fetchone()
        if not inv: return False
    if inv['shop_id']!=shop['id'] or inv['payment_method']!='square' or inv['status']!='signed': raise HTTPException(400,'Invoice payment association mismatch.')
    amount=inv['checkout_amount'];m=payment.get('amount_money',{})
    if payment.get('status')!='COMPLETED': return True
    refunded=payment.get('refunded_money',{}).get('amount',0)
    if not isinstance(refunded,int) or isinstance(refunded,bool) or not 0<=refunded<=amount or (refunded and payment.get('refunded_money',{}).get('currency')!='USD'): raise HTTPException(400,'Invalid aggregate refund amount.')
    if amount<=0 or m.get('amount')!=amount or m.get('currency')!='USD' or (payment.get('location_id') and payment['location_id']!=shop['square_location']): raise HTTPException(400,'Invoice payment amount, currency or location mismatch.')
    with db(True) as c:
        if c.execute("SELECT 1 FROM webhook_events WHERE provider='square' AND event_id=?",(event_id,)).fetchone(): return True
        current=c.execute('SELECT * FROM invoices WHERE id=?',(inv['id'],)).fetchone()
        if current['square_payment'] and current['square_payment']!=payment['id']: raise HTTPException(409,'Additional payment requires manual review.')
        c.execute('INSERT OR IGNORE INTO invoice_receipts VALUES(?,?,?,?,?,?,?)',(uid(),inv['id'],amount,'square',payment['id'],now(),'square-webhook'))
        c.execute('UPDATE invoices SET collected=?,square_order=?,square_payment=? WHERE id=?',(amount,order_id,payment['id'],inv['id']))
        if refunded>current['balance_refunded']:
            c.execute('INSERT INTO invoice_adjustments VALUES(?,?,?,?,?,?)',(uid(),inv['id'],refunded-current['balance_refunded'],'Square balance refund','balance-refund:'+payment['id']+':'+str(refunded),now()))
            c.execute('UPDATE invoices SET balance_refunded=?,payment_review=1 WHERE id=?',(refunded,inv['id']))
            audit(c,shop['id'],'invoice.refund_review_required',inv['id'])
        c.execute('INSERT INTO webhook_events VALUES(?,?,?)',('square',event_id,now()))
        _,value,_=read_version(c,inv['id'])
        queue_mail(c,inv['id']+':paid',value['customer']['email'],shop['name']+' | Payment received | '+inv['number'],
                   'Square confirmed payment to '+shop['name']+'.\nInvoice and receipt: '+public_url(inv),shop['id'])
        audit(c,shop['id'],'invoice.square_payment_received',inv['id'])
    return True


def sync_deposit(c,booking):
    """Reflect verified deposit refunds without editing either signed PDF."""
    inv=c.execute('SELECT * FROM invoices WHERE booking_id=?',(booking['id'],)).fetchone()
    if not inv or not booking['square_payment']: return
    net=max(0,booking['deposit']-booking['refunded'])
    if net<inv['deposit_received']:
        c.execute('INSERT INTO invoice_adjustments VALUES(?,?,?,?,?,?)',(uid(),inv['id'],inv['deposit_received']-net,'Square deposit refund','deposit-refund:'+booking['square_payment']+':'+str(net),now()))
        c.execute('UPDATE invoices SET deposit_received=?,payment_review=1 WHERE id=?',(net,inv['id']))
        audit(c,inv['shop_id'],'invoice.deposit_refund_review_required',inv['id'])

def apply_refund(event_id,merchant_id,refund):
    with db() as c:
        inv=c.execute("SELECT i.* FROM invoices i JOIN shops s ON s.id=i.shop_id WHERE i.square_payment=? AND s.square_merchant=?",(refund.get('payment_id'),merchant_id)).fetchone()
    if not inv: return False
    payment=payments.square(inv['shop_id'],'/v2/payments/'+inv['square_payment'],method='GET')['payment']
    return apply_payment(event_id,merchant_id,payment)

def reconcile(limit=15):
    """Bounded reconciliation complements signed webhooks; never trusts browser returns."""
    import logging
    with db() as c:
        rows=c.execute("""SELECT i.*,s.square_merchant FROM invoices i JOIN shops s ON s.id=i.shop_id
            WHERE i.square_order IS NOT NULL AND s.square_access IS NOT NULL
            AND i.created_at>? AND i.last_checked_at<? ORDER BY i.last_checked_at LIMIT ?""",(now()-90*86400,now()-300,limit)).fetchall()
    for inv in rows:
        try:
            ids=[inv['square_payment']] if inv['square_payment'] else []
            if not ids:
                order=payments.square(inv['shop_id'],'/v2/orders/'+inv['square_order'],method='GET')['order']
                ids=[t['payment_id'] for t in order.get('tenders',[]) if t.get('payment_id')]
            for payment_id in ids:
                p=payments.square(inv['shop_id'],'/v2/payments/'+payment_id,method='GET')['payment']
                event='invoice-reconcile:'+payment_id+':'+p.get('status','')+':'+str(p.get('refunded_money',{}).get('amount',0))
                apply_payment(event,inv['square_merchant'],p)
        except Exception:
            logging.getLogger('kontracts.worker').warning('Invoice payment reconciliation needs retry.')
        finally:
            with db(True) as c: c.execute('UPDATE invoices SET last_checked_at=? WHERE id=?',(now(),inv['id']))


def install(app,owner,session,owned_booking,ip):
    @app.post('/api/bookings/{identifier}/complete')
    def finish(identifier:str,body:Completion,request:Request,shop=Depends(owner),user=Depends(session)):
        with db(True) as c: return complete(c,owned_booking(c,identifier,shop['id']),shop,user,body,request)

    @app.get('/api/invoices')
    def list_invoices(shop=Depends(owner)):
        with db() as c: return {'invoices':[summary(r) for r in c.execute('SELECT * FROM invoices WHERE shop_id=? ORDER BY created_at DESC LIMIT 1000',(shop['id'],))]}

    @app.get('/api/invoices/{identifier}')
    def view_owner(identifier:str,shop=Depends(owner)):
        with db() as c:
            inv=owned(c,identifier,shop);row,value,_=read_version(c,identifier)
            return {**summary(inv),'document':value,'pdf_sha256':row['pdf_sha256'],'seal':row['seal']}

    @app.get('/api/invoices/{identifier}/pdf')
    def pdf_owner(identifier:str,shop=Depends(owner)):
        with db() as c:
            inv=owned(c,identifier,shop);_,_,pdf=read_version(c,identifier)
        return Response(pdf,media_type='application/pdf',headers={'Content-Disposition':'attachment; filename="'+inv['number']+'.pdf"','Cache-Control':'no-store'})

    @app.post('/api/invoices/{identifier}/send-link')
    def renew(identifier:str,shop=Depends(owner)):
        rate_limit('invoice-resend:'+shop['id'],10,3600)
        with db(True) as c:
            inv=owned(c,identifier,shop);raw=token()
            c.execute('UPDATE invoices SET token_hash=?,token_encrypted=?,token_expires=?,otp_hash=NULL,otp_expires=NULL,otp_attempts=0,pdf_viewed_at=NULL WHERE id=?',
                      (digest(raw),encrypt(raw),now()+30*86400,identifier))
            inv=owned(c,identifier,shop);send_invoice(c,inv,shop,'signed' if inv['status']=='signed' else 'renewed');audit(c,shop['id'],'invoice.link_rotated',identifier)
        return {'message':'A fresh private link was queued to the original customer email. The previous link is invalid.'}

    @app.post('/api/invoices/{identifier}/received-in-person')
    def record_cash(identifier:str,body:Received,shop=Depends(owner),user=Depends(session)):
        with db(True) as c:
            inv=owned(c,identifier,shop)
            if inv['payment_method']!='in_person': raise HTTPException(409,'Square payments must be verified by Square, not marked manually.')
            amount=summary(inv)['balance']
            if amount:
                c.execute('INSERT INTO invoice_receipts VALUES(?,?,?,?,?,?,?)',(uid(),identifier,amount,'in_person','cash:'+identifier,now(),user['user_id']))
                c.execute('UPDATE invoices SET collected=collected+? WHERE id=?',(amount,identifier))
                audit(c,shop['id'],'invoice.in_person_payment_recorded',identifier)
                _,v,_=read_version(c,identifier)
                queue_mail(c,identifier+':cash-paid',v['customer']['email'],shop['name']+' | In-person payment recorded | '+inv['number'],
                           shop['name']+' recorded your in-person payment.\nRetained invoice: '+public_url(inv),shop['id'])
            return {'message':'In-person payment recorded.'}

    @app.get('/api/public/invoices/{raw}')
    def view_public(raw:str,request:Request):
        rate_limit('invoice-view:'+ip(request),90,60)
        with db() as c:
            inv=accessed(c,raw);row,value,_=read_version(c,inv['id']);original,original_value,_=read_version(c,inv['id'],0)
            adjustments=[dict(r) for r in c.execute('SELECT amount,reason,created_at FROM invoice_adjustments WHERE invoice_id=? ORDER BY created_at',(inv['id'],))]
            receipts=[dict(r) for r in c.execute('SELECT amount,method,recorded_at FROM invoice_receipts WHERE invoice_id=? ORDER BY recorded_at',(inv['id'],))]
        return {**summary(inv),'document':value,'document_hash':digest(canonical(original_value)),
                'pdf_sha256':row['pdf_sha256'],'consent_text':CUSTOMER_CONSENT,'correspondence_email':value['business'].get('correspondence_email',config.SUPPORT_EMAIL),'receipts':receipts,'adjustments':adjustments}

    @app.get('/api/public/invoices/{raw}/pdf')
    def pdf_public(raw:str,request:Request):
        rate_limit('invoice-pdf:'+ip(request),60,60)
        with db(True) as c:
            inv=accessed(c,raw);_,_,pdf=read_version(c,inv['id'])
            c.execute('UPDATE invoices SET pdf_viewed_at=? WHERE id=?',(now(),inv['id']))
        return Response(pdf,media_type='application/pdf',headers={'Content-Disposition':'attachment; filename="'+inv['number']+'.pdf"','Cache-Control':'no-store','X-Robots-Tag':'noindex, nofollow'})

    @app.post('/api/public/invoices/{raw}/code')
    def request_code(raw:str,request:Request):
        rate_limit('invoice-code-ip:'+ip(request),10,3600);rate_limit('invoice-code-token:'+digest(raw),5,3600)
        with db(True) as c:
            inv=accessed(c,raw)
            if inv['status']=='signed': return {'message':'This invoice is already signed.'}
            if not inv['pdf_viewed_at']: raise HTTPException(409,'Open and save the PDF before requesting a signing code.')
            _,value,_=read_version(c,inv['id']);code=f'{secrets.randbelow(1000000):06d}'
            code_hash=hmac.new(config.SECRET.encode(),(inv['id']+':'+code).encode(),hashlib.sha256).hexdigest()
            c.execute('UPDATE invoices SET otp_hash=?,otp_expires=?,otp_attempts=0 WHERE id=?',(code_hash,now()+600,inv['id']))
            queue_mail(c,inv['id']+':code:'+uid(),value['customer']['email'],value['business']['name']+' | Invoice signing code | '+inv['number'],
                       'Your invoice signing code is '+code+'. It expires in 10 minutes. Do not share it with anyone, including the detailer.\n\nInvoice: '+public_url(inv),inv['shop_id'],
                       reply_to=config.SUPPORT_EMAIL,expires_at=now()+600,guard_kind='invoice_code',guard_id=inv['id'],guard_value=code_hash)
        return {'message':'A six-digit code was queued to the customer email already on this invoice. It expires in 10 minutes.'}

    @app.post('/api/public/invoices/{raw}/sign')
    def sign_public(raw:str,body:Signature,request:Request):
        rate_limit('invoice-sign:'+ip(request),30,600)
        error=None
        with db(True) as c:
            inv=accessed(c,raw)
            if inv['status']=='signed': return {**summary(inv),'message':'This invoice is already signed and saved.'}
            _,value,_=read_version(c,inv['id'],0)
            expected=hmac.new(config.SECRET.encode(),(inv['id']+':'+body.code).encode(),hashlib.sha256).hexdigest()
            if not inv['pdf_viewed_at'] or not inv['otp_hash'] or (inv['otp_expires'] or 0)<=now() or inv['otp_attempts']>=5:
                error='The signing code is unavailable, expired or locked. Open the PDF and request a new code.'
            elif not constant_equal(body.document_hash,digest(canonical(value))):
                error='The document changed. Reload and review it again.'
            elif not constant_equal(expected,inv['otp_hash']):
                # Commit the failed attempt before returning an error; never roll this counter back.
                c.execute('UPDATE invoices SET otp_attempts=otp_attempts+1 WHERE id=?',(inv['id'],));error='The signing code is incorrect.'
            else:
                value['customer_signature']=evidence(request,'Verified invoice email: '+value['customer']['email'],CUSTOMER_CONSENT,body.signer_name)
                store_version(c,inv['id'],1,value)
                c.execute("UPDATE invoices SET status='signed',signed_at=?,otp_hash=NULL,otp_expires=NULL WHERE id=?",(now(),inv['id']))
                c.execute("UPDATE bookings SET status='completed',updated_at=? WHERE id=?",(now(),inv['booking_id']))
                shop=c.execute('SELECT * FROM shops WHERE id=?',(inv['shop_id'],)).fetchone();inv=c.execute('SELECT * FROM invoices WHERE id=?',(inv['id'],)).fetchone()
                send_invoice(c,inv,shop,'signed');audit(c,shop['id'],'invoice.customer_signed',inv['id'])
                queue_mail(c,inv['id']+':owner-signed',core.settings(shop)['contact_email'],shop['name']+' | Job completed and invoice signed | '+inv['number'],
                           'The customer signed '+inv['number']+'. The sealed PDF is saved in your workspace.\n'+config.BASE_URL+'/app/invoices',shop['id'],reply_to=value['customer']['email'])
        if error: raise HTTPException(422,error)
        return {**summary(inv),'message':'Your signed invoice has been saved. Download and retain your copy.'}

    @app.post('/api/public/invoices/{raw}/pay')
    def pay_public(raw:str,request:Request):
        rate_limit('invoice-pay:'+ip(request),10,600)
        with db() as c: inv=accessed(c,raw)
        return {'url':create_payment(inv)}
