"""Fetch GeoNames US postal data at install/update time, then validate locally.
Usage: python scripts/update_zipcodes.py [--input /path/to/US.zip]
Data license: Creative Commons Attribution 4.0; attribution in THIRD-PARTY-NOTICES.md.
"""
import argparse, hashlib, io, json, os, re, tempfile, urllib.request, zipfile
from datetime import datetime, timezone
from pathlib import Path
STATES=set('AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY'.split())
URL='https://download.geonames.org/export/zip/US.zip'
def build(raw):
    if len(raw)>10_000_000: raise ValueError('ZIP registry download is too large.')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        info=archive.getinfo('US.txt')
        if info.file_size>30_000_000: raise ValueError('Unexpected postal data size.')
        text=archive.read(info).decode('utf-8')
    codes={}
    for line in text.splitlines():
        r=line.split('\t')
        if len(r)>=5 and r[0]=='US' and re.fullmatch(r'[0-9]{5}',r[1]) and r[4] in STATES:
            codes.setdefault(r[1],set()).add(r[4])
    if not 30000<=len(codes)<=50000 or set.union(*codes.values())!=STATES:
        raise ValueError('Postal data failed nationwide coverage checks; existing file left unchanged.')
    return {'fixture':False,'source':URL,'retrieved_at':datetime.now(timezone.utc).isoformat(),
            'source_sha256':hashlib.sha256(raw).hexdigest(),'license':'CC BY 4.0',
            'codes':{k:sorted(v) for k,v in sorted(codes.items())}}
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--input',type=Path);args=parser.parse_args()
    if args.input: raw=args.input.read_bytes()
    else:
        with urllib.request.urlopen(URL,timeout=40) as response: raw=response.read(10_000_001)
    value=build(raw);path=Path(__file__).resolve().parents[1]/'server/resources/us_zipcodes.json'
    fd,name=tempfile.mkstemp(dir=path.parent,prefix='.zip-update-')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump(value,f,separators=(',',':'));f.flush();os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        Path(name).unlink(missing_ok=True)
    print('Installed',len(value['codes']),'US ZIP codes; no runtime ZIP connection required.')
if __name__=='__main__': main()
