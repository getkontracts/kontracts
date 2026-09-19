"""Synthetic demo data; this seed is unavailable in production."""
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from .db import db,uid,now,packed
from .models import default_settings
from .security import PASSWORDS,token,digest,encrypt

def seed():
    with db(True) as c:
        if c.execute('SELECT 1 FROM shops WHERE is_demo=1').fetchone(): return
        owner=uid();shop=uid();email='alex@northlinedemo.com'
        c.execute('INSERT INTO users VALUES(?,?,?,?,?,?)',(owner,email,PASSWORDS.hash(token()),'Alex Morgan',1,now()))
        s=default_settings('Northline Auto Care',email,'America/Chicago')
        s.update(city='Austin, Texas',zip_codes=['78701','78702','78703','78704','78705','78745','78748'],phone='(512) 555-0148',policy_reviewed=True)
        c.execute('''INSERT INTO shops(id,owner_id,slug,name,settings,published,created_at,trial_end,billing_status,is_demo)
            VALUES(?,?,?,?,?,1,?,?,'trial',1)''',(shop,owner,'northline',s['name'],packed(s),now(),now()+14*86400))
        local=datetime.fromtimestamp(now(),ZoneInfo('America/Chicago')).replace(hour=9,minute=0,second=0,microsecond=0)
        names=[('Jordan Lee','suv','Tesla Model Y'),('Olivia Chen','sedan','BMW 330i'),('Marcus Davis','truck','Ford F-150'),('Sofia Williams','suv','Volvo XC60'),('Ethan Brooks','sedan','Honda Accord'),('Maya Patel','suv','Toyota RAV4')]
        cursor=local-timedelta(days=2)
        for i,(name,vehicle,car) in enumerate(names):
            while cursor.weekday()==6: cursor+=timedelta(days=1)
            day=cursor
            cursor+=timedelta(days=1)
            if i==5: day=day.replace(hour=14)
            start=int(day.timestamp());past=start<now();total=21400 if vehicle=='suv' else 18900 if vehicle=='sedan' else 23400
            p={'subtotal':total,'tax':0,'total':total,'deposit':total//5,'minutes':180,'buffer_minutes':30,
               'service':'The full detail','vehicle':vehicle.upper(),'currency':'USD','lines':[{'name':'The full detail','amount':18900}]+([{'name':'Vehicle size','amount':total-18900}] if total>18900 else [])}
            raw=token();bid=uid()
            b={'id':bid,'shop_id':shop,'manage_hash':digest(raw),'manage_encrypted':encrypt(raw),'idempotency_key':uid(),'request_hash':'demo',
                'customer_name':name,'email':name.split()[0].lower()+'@example.com','phone':'(512) 555-0100','address':str(120+i*20)+' Sample Avenue, Austin, TX 78701',
                'zip':'78701','vehicle':vehicle,'vehicle_notes':car,'notes':'Synthetic demo appointment. Not a real customer.','snapshot':packed(p),
                'start_ts':start,'end_ts':start+10800,'busy_until':start+12600,'subtotal':total,'tax':0,'total':total,'deposit':total//5,
                'status':'completed' if past else 'confirmed','payment_status':'paid','square_payment':'demo-'+bid,
                'accepted_policy':s['cancellation_policy'],'created_at':now()-86400*4,'updated_at':now()}
            c.execute(f"INSERT INTO bookings ({','.join(b)}) VALUES({','.join('?' for _ in b)})",tuple(b.values()))
        raw=token()
        c.execute('''INSERT INTO quotes(id,shop_id,token_hash,token_encrypted,customer_name,email,phone,address,zip,vehicle,vehicle_notes,notes,selections,status,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'new',?)''',(uid(),shop,digest(raw),encrypt(raw),'Jamie Parker','jamie@example.com','(512) 555-0165',
                '310 Sample Lane, Austin, TX 78704','78704','suv','Subaru Outback','Two dogs and a road trip. Could you quote a deeper interior clean? This is a demo request.',
                packed({'package_id':'interior','vehicle_id':'suv','extra_ids':['pet-hair'],'zip':'78704','condition':'heavy'}),now()-7200))
