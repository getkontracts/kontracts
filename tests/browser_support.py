"""Local in-process browser harness. Does not bypass or modify browser network policy.

The managed Chromium environment blocks localhost navigation. TestClient instead
executes API code in process. This tests UI/HTTP handlers, not deployed HTTPS,
real browser cookies, CSP enforcement, external checkout or physical cameras.
"""
import base64
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]


def mount(page,client,start='/'):
    def bridge(source,path,options):
        options=options or {};headers=options.get('headers',{})
        kwargs={}
        form=options.get('__form')
        if form is not None:
            data={};files=[]
            for item in form:
                if item.get('file'):
                    files.append((item['key'],(item['name'],base64.b64decode(item['bytes']),item['type'])))
                else:data[item['key']]=item['value']
            kwargs={'data':data,'files':files}
        else:kwargs={'content':options.get('body')}
        r=client.request(options.get('method','GET'),path,headers=headers,**kwargs)
        return {'status':r.status_code,'body64':base64.b64encode(r.content).decode(),'type':r.headers.get('content-type','application/json')}
    page.expose_binding('__apiBridge',bridge)
    js=(ROOT/'web/assets/release.js').read_text()+'\n'+(ROOT/'web/assets/branding.js').read_text()+'\n'+(ROOT/'web/assets/photos.js').read_text()+'\n'+(ROOT/'web/assets/workflow.js').read_text()+'\n'+(ROOT/'web/assets/app.js').read_text()
    js=js.replace('location.pathname','window.__testURL.pathname').replace('location.search','window.__testURL.search')
    js=js.replace('location.origin',"'http://kontracts.local'").replace('location.host',"'kontracts.local'")
    js=js.replace("history.replaceState({},'',url)","window.__testURL=new URL(url,'http://kontracts.local')")
    js=js.replace("history.pushState({},'',url)","window.__testURL=new URL(url,'http://kontracts.local')")
    js=js.replace('window.location.assign','window.__testAssign')
    setup='''window.__testURL=new URL(START,'http://kontracts.local');
window.__testAssign=url=>{window.__testURL=new URL(url,'http://kontracts.local');render();};
const encode64=bytes=>{let out='';for(let i=0;i<bytes.length;i+=8192)out+=String.fromCharCode(...bytes.subarray(i,i+8192));return btoa(out);};
window.fetch=async(path,options={})=>{
 const copy={...options};
 if(options.body instanceof FormData){copy.__form=[];for(const [key,value] of options.body.entries()){
  if(value instanceof Blob)copy.__form.push({key,file:true,name:value.name,type:value.type,bytes:encode64(new Uint8Array(await value.arrayBuffer()))});
  else copy.__form.push({key,value});
 }delete copy.body;}
 const r=await window.__apiBridge(path,copy);const bytes=Uint8Array.from(atob(r.body64),c=>c.charCodeAt(0));
 return new Response(bytes,{status:r.status,headers:{'Content-Type':r.type}});
};
const imageObserver=new MutationObserver(()=>{document.querySelectorAll('img').forEach(async image=>{
 const source=image.getAttribute('src')||'';
 if(!source.startsWith('/api/')||image.dataset.bridgeLoading)return;
 image.dataset.bridgeLoading='true';
 try{const result=await fetch(source);if(result.ok)image.src=URL.createObjectURL(await result.blob());}catch(_){}
});});
imageObserver.observe(document.documentElement,{subtree:true,childList:true});
if(!crypto.randomUUID)crypto.randomUUID=()=>Array.from(crypto.getRandomValues(new Uint8Array(20)),x=>x.toString(16).padStart(2,'0')).join('');
'''.replace('START',json.dumps(start))
    html=(ROOT/'web/index.html').read_text().replace('<script defer src="/assets/release.js"></script>','').replace('<script defer src="/assets/branding.js"></script>','')
    html=html.replace('<link rel="stylesheet" href="/assets/app.css">','<style>'+(ROOT/'web/assets/app.css').read_text()+'</style>')
    html=html.replace('<script defer src="/assets/workflow.js"></script>','').replace('<script defer src="/assets/app.js"></script>','').replace('<script defer src="/assets/photos.js"></script>','').replace('<link rel="icon" href="/assets/favicon.svg" type="image/svg+xml">','')
    page.set_content(html)
    page.add_script_tag(content=setup+'\n'+js)
    page.wait_for_function("document.querySelector('#main') !== null")
