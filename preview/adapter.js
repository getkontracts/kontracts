/* OFFLINE DESIGN PREVIEW ONLY. This adapter is not the production server. */
window.__OFFLINE_DEMO__=true;
const preview=structuredClone(PREVIEW_SEED);
let previewSignedIn=true;preview.photos=[];preview.invoices=[];preview.me.shop.settings.payment_method='in_person';window.__previewPhotoUrls={};
const demonstrationJob=preview.bookings.find(x=>x.start_ts<Math.floor(Date.now()/1000));
if(demonstrationJob){demonstrationJob.status='confirmed';demonstrationJob.payment_method='in_person';demonstrationJob.deposit=0;demonstrationJob.payment_status='unpaid';demonstrationJob.square_payment=null;demonstrationJob.approved_at=demonstrationJob.start_ts-86400;}

function pstorage(){const used=preview.photos.reduce((n,p)=>n+p.size_bytes,0);return {count:preview.photos.length,used_bytes:used,limit_bytes:30*1024*1024,job_bytes:used,quote_bytes:0,reported_original_bytes:preview.photos.reduce((n,p)=>n+p.original_bytes,0)};}

const pnow=()=>Math.floor(Date.now()/1000);
const presult=(data,status=200)=>new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json'}});
const pshop=()=>({...preview.me.shop,branding:preview.branding.live,settings:preview.me.shop.settings,name:preview.me.shop.settings.name,payments_ready:true});
function pprice(b){const s=pshop().settings;if(!s.zip_codes.includes(b.zip))throw Error('That ZIP code is outside the sample service area. Try 78701.');if(b.condition==='heavy')throw Error('Request a reviewed quote for heavy-condition vehicles.');const p=s.packages.find(x=>x.id===b.package_id),v=s.vehicles.find(x=>x.id===b.vehicle_id);if(!p||!v)throw Error('Select a service and vehicle.');const extras=s.extras.filter(x=>(b.extra_ids||[]).includes(x.id));const q=b.quote_token?preview.quotes.find(x=>x.id===b.quote_token):null;const rate=p.vehicle_rates?.[v.id]||{price:p.price+v.price,minutes:p.minutes+v.minutes};let subtotal=q?q.amount:rate.price+extras.reduce((a,x)=>a+x.price,0);const tax=Math.round(subtotal*s.tax_basis_points/10000),total=subtotal+tax;return {subtotal,tax,total,deposit:s.payment_method==='square'?Math.round(total*s.deposit_percent/100):0,minutes:q?q.minutes:rate.minutes+extras.reduce((a,x)=>a+x.minutes,0),buffer_minutes:s.buffer_minutes,service:q?'Custom detail':p.name,vehicle:v.name,currency:'USD',lines:q?[{name:'Custom detail',amount:subtotal}]:[{name:p.name+' - '+v.name,amount:rate.price},...extras.map(x=>({name:x.name,amount:x.price}))]};}
function pslots(date,minutes,exclude){const s=pshop().settings,d=new Date(date+'T12:00:00Z'),day=(d.getUTCDay()+6)%7,h=s.hours[day];if(!h?.enabled)return [];const [oh,om]=h.start.split(':').map(Number),[eh,em]=h.end.split(':').map(Number),out=[];for(let t=oh*60+om;t+minutes+s.buffer_minutes<=eh*60+em;t+=30){const time=String(Math.floor(t/60)).padStart(2,'0')+':'+String(t%60).padStart(2,'0'),start=localTimestamp(date,time,s.timezone),end=start+(minutes+s.buffer_minutes)*60;const blocked=preview.bookings.some(b=>b.id!==exclude&&['confirmed','completed','held'].includes(b.status)&&b.start_ts<end&&b.busy_until>start)||preview.blocks.some(b=>b.start_ts<end&&b.end_ts>start);if(!blocked&&start>pnow()+s.lead_hours*3600)out.push({start_ts:start,label:new Intl.DateTimeFormat('en-US',{timeZone:s.timezone,hour:'numeric',minute:'2-digit'}).format(new Date(start*1000))});}return out;}
function pmanage(id){const b=preview.bookings.find(x=>x.id===id);if(!b)throw Error('Sample booking not found.');return {booking:b,shop:pshop(),can_change:b.status==='confirmed'&&b.start_ts>pnow()+pshop().settings.cancellation_hours*3600};}
function pdashboard(){const rows=preview.bookings.filter(x=>['confirmed','completed'].includes(x.status));return {...preview.dashboard,bookings:preview.bookings.filter(x=>x.status==='confirmed'&&x.start_ts>pnow()-86400).sort((a,b)=>a.start_ts-b.start_ts),stats:{scheduled_value:rows.reduce((a,b)=>a+b.total,0),completed_value:rows.filter(x=>x.status==='completed').reduce((a,b)=>a+b.total,0),deposits_received:rows.filter(x=>['paid','refunded'].includes(x.payment_status)).reduce((a,b)=>a+b.deposit-(b.refunded||0),0),upcoming:rows.filter(x=>x.status==='confirmed'&&x.start_ts>pnow()).length,quote_requests:preview.quotes.filter(x=>x.status==='new').length,email_failures:0,payment_reviews:0,pending_approvals:preview.bookings.filter(x=>x.status==='pending_approval').length}};}

// Offline-only brand editor: changes live in this tab, never a real account.
const pbrandAssets={};
function pbrandDecorate(b){return {...b,logo_url:pbrandAssets[b.logo_id]?.url||b.logo_url||'',cover_url:pbrandAssets[b.cover_id]?.url||b.cover_url||'',favicon_url:pbrandAssets[b.logo_id]?.url||b.favicon_url||''};}
function pbrandResult(){preview.branding.has_unpublished_changes=JSON.stringify(preview.branding.draft)!==JSON.stringify(preview.branding.live);return presult(preview.branding);}

window.fetch=async(raw,opts={})=>{await new Promise(r=>setTimeout(r,90));try{const url=new URL(raw,'https://preview.invalid'),path=url.pathname,method=opts.method||'GET';let b=opts.body instanceof FormData?JSON.parse(opts.body.get('data')||'{}'):JSON.parse(opts.body||'{}');

if(path==='/api/branding'&&method==='GET')return pbrandResult();
if(path==='/api/branding'&&method==='PUT'){if(b.revision!==preview.branding.revision)throw Error('Reload the branding editor.');preview.branding.draft=pbrandDecorate(b.branding);preview.branding.revision++;return pbrandResult();}
if(path==='/api/branding/publish'){preview.branding.live=structuredClone(preview.branding.draft);preview.me.shop.branding=preview.branding.live;preview.branding.revision++;preview.branding.published_revision=preview.branding.revision;preview.branding.message='Preview branding published in this browser tab only.';return pbrandResult();}
if(path==='/api/branding/reset'){preview.branding.draft=structuredClone(preview.branding.live);preview.branding.revision++;return pbrandResult();}
if(path==='/api/branding/preview')return presult({...pshop(),branding:preview.branding.draft,branding_preview:true});
if(path.startsWith('/api/branding/assets/')){
 const kind=path.split('/').pop();
 if(method==='POST'){const file=opts.body.get('file'),id=crypto.randomUUID();pbrandAssets[id]={url:URL.createObjectURL(file)};preview.branding.draft[kind+'_id']=id;preview.branding.draft=pbrandDecorate(preview.branding.draft);preview.branding.revision++;preview.branding.upload={bytes:file.size};return pbrandResult();}
 if(method==='DELETE'){preview.branding.draft[kind+'_id']='';preview.branding.draft[kind+'_url']='';if(kind==='logo')preview.branding.draft.favicon_url='';preview.branding.revision++;return pbrandResult();}
}
if(path==='/api/booking-page/availability')return presult({available:/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(url.searchParams.get('slug')||''),reason:'Preview only: the server checks availability in the real app.'});
if(path==='/api/booking-page/slug'){if(!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(b.slug))throw Error('Use lowercase letters, numbers and hyphens.');preview.me.shop.slug=b.slug;preview.branding.slug=b.slug;if(!preview.branding.aliases.includes(b.slug))preview.branding.aliases.push(b.slug);preview.branding.booking_url='https://preview.invalid/book/'+b.slug;preview.branding.canonical_booking_url=preview.branding.booking_url;preview.me.shop.booking_url=preview.branding.booking_url;return pbrandResult();}
if(path==='/api/branding/domain'&&method==='POST'){preview.branding.domain={hostname:b.hostname,status:'pending',txt_name:'_kontracts.'+b.hostname,txt_value:'kontracts-verification=offline-preview-not-a-real-token'};return pbrandResult();}
if(path==='/api/branding/domain'&&method==='DELETE'){preview.branding.domain=null;return pbrandResult();}
if(path==='/api/branding/domain/verify')throw Error('DNS verification requires the deployed app. This offline preview does not contact DNS or activate domains.');
if(path==='/api/branding/email-preview'){const b=preview.branding.live;return presult({from:preview.me.shop.name+' <notifications@getkontracts.app>',reply_to:'demo@example.com',html:'<!doctype html><html><body style="font:15px/1.6 system-ui;padding:24px;background:#f7f8f6"><main style="background:white;padding:24px;border-top:5px solid '+b.primary_color+'"><h1>'+e(pshop().name)+'</h1><p>Your booking is confirmed.</p><p>Example service: The full detail<br>Tuesday at 9:00 AM</p><p>This is a sample email. No email is sent.</p>'+(!b.hide_powered_by?'<small>Powered by Kontracts</small>':'')+'</main></body></html>'});}

// Offline-only photo simulation. Blobs stay in memory and disappear on reload.
const photoMatch=path.match(/^\/api\/(?:bookings\/([^/]+)|public\/manage\/([^/]+))\/photos$/);
if(path==='/api/media')return presult({photos:preview.photos.map(x=>({...x,customer_view:false})),storage:pstorage()});
if(photoMatch){
 const bookingId=photoMatch[1]||photoMatch[2], customer=!!photoMatch[2], item=pmanage(bookingId).booking;
 if(method==='GET')return presult({photos:preview.photos.filter(x=>x.booking_id===bookingId&&(!customer||x.uploaded_by==='customer')).map(x=>({...x,customer_view:customer})),storage:pstorage(),customer_name:item.customer_name,vehicle_notes:item.vehicle_notes,booking_id:bookingId,photo_limit:customer?6:24,can_upload:item.status==='confirmed'});
 if(method==='POST'){
  const fd=opts.body,key=fd.get('upload_key');const prior=preview.photos.filter(x=>x._key===key);if(prior.length)return presult({photos:prior,message:'These preview photos were already added.'});
  const files=fd.getAll('files'),originals=JSON.parse(fd.get('original_sizes')||'[]'),photos=[];
  for(let i=0;i<files.length;i++){const file=files[i],image=await decodeBrowserImage(file),url=URL.createObjectURL(file),id=crypto.randomUUID();photos.push({id,booking_id:bookingId,category:customer?'condition':fd.get('category'),caption:fd.get('caption'),profile:fd.get('profile'),size_bytes:file.size,original_bytes:originals[i]||file.size,width:image.width||image.naturalWidth,height:image.height||image.naturalHeight,uploaded_by:customer?'customer':'owner',created_at:pnow(),url,thumbnail_url:url,customer_name:item.customer_name,vehicle_notes:item.vehicle_notes,start_ts:item.start_ts,_key:key,customer_view:customer});if(image.close)image.close();}
  preview.photos.push(...photos);return presult({photos,message:'Added to this offline preview only. Reloading clears demo uploads.'},201);
 }
}
if(path.startsWith('/api/photos/')&&method==='DELETE'){const id=path.split('/').pop();URL.revokeObjectURL(window.__previewPhotoUrls[id]);delete window.__previewPhotoUrls[id];for(const q of preview.quotes)q.photos=q.photos.filter(x=>x!==id);return presult({message:'Preview quote photo removed.'});}
if(path.startsWith('/api/job-photos/')){
 const photo=preview.photos.find(x=>x.id===path.split('/').pop());if(!photo)throw Error('Preview photo not found.');
 if(method==='DELETE'){URL.revokeObjectURL(photo.url);preview.photos=preview.photos.filter(x=>x.id!==photo.id);return presult({message:'Removed from this preview.'});}
 if(method==='PATCH'){Object.assign(photo,b);return presult({message:'Preview photo updated.'});}
}


if(path==='/api/invoices')return presult({invoices:preview.invoices});
if(/^\/api\/bookings\/[^/]+\/approve$/.test(path)){const item=pmanage(path.split('/')[3]).booking;item.approved_at=pnow();item.status='confirmed';return presult({id:item.id,status:item.status,message:'SIMULATED approval. No email or payment request was sent.'});}
if(/^\/api\/bookings\/[^/]+\/complete$/.test(path)){const item=pmanage(path.split('/')[3]).booking;const id='demo-'+crypto.randomUUID(),paid=b.received_in_person?item.total:0;const inv={id,booking_id:item.id,number:'DEMO-NOT-AN-INVOICE',status:'awaiting_signature',created_at:pnow(),signed_at:null,total:item.total,deposit_received:0,collected:paid,balance:item.total-paid,payment_method:'in_person',document_hash:'0'.repeat(64),consent_text:'DEMO ONLY: this simulates an electronic signature. It is not a real invoice, signature, payment or service acknowledgement.',correspondence_email:'demo@example.com',document:{number:'DEMO-NOT-AN-INVOICE',business:{name:'Northline - DEMO ONLY'},customer:{name:'Demo Customer',email:'demo@example.com'},lines:item.snapshot.lines,tax:item.tax,total:item.total}};preview.invoices.unshift(inv);item.status='awaiting_signature';return presult(inv);}
if(path.startsWith('/api/invoices/')&&path.endsWith('/send-link')){const inv=preview.invoices.find(x=>x.id===path.split('/')[3]);window.setTimeout(()=>window.__previewAssign('/invoice/'+inv.id),400);return presult({message:'DEMO: no email sent. Opening the simulated customer view. Use code 123456.'});}
if(path.startsWith('/api/invoices/')&&path.endsWith('/received-in-person')){const inv=preview.invoices.find(x=>x.id===path.split('/')[3]);inv.collected=inv.total;inv.balance=0;return presult({message:'SIMULATED payment only. No money was collected.'});}
if(path.startsWith('/api/public/invoices/')){const inv=preview.invoices.find(x=>x.id===path.split('/')[4]);if(!inv)throw Error('Demo invoice not found. Start in the sample workspace.');if(path.endsWith('/code'))return presult({message:'DEMO signing code: 123456. No email is sent.'});if(path.endsWith('/sign')){if(b.code!=='123456')throw Error('For this simulation use code 123456.');inv.status='signed';inv.signed_at=pnow();pmanage(inv.booking_id).booking.status='completed';return presult({...inv,message:'Simulated signature saved in memory only. This is not a real invoice.'});}if(path.endsWith('/pay'))throw Error('Payments are disabled in the demo.');return presult(inv);}

if(path==='/api/config')return presult(preview.config);
if(path==='/api/auth/demo'){previewSignedIn=true;return presult({csrf:'offline-only'});}
if(path==='/api/auth/me')return previewSignedIn?presult(preview.me):presult({detail:'Open the demo workspace.'},401);
if(path==='/api/auth/logout'){previewSignedIn=false;return presult({ok:true});}
if(path==='/api/setup')return presult({checks:[{id:'prices',name:'Review services and prices',ready:true,url:'/app/services'},{id:'brand',name:'Your business branding',ready:true,url:'/app/branding'},{id:'page',name:'Booking page published',ready:true,url:'/book/northline'}],platform:{email_configured:false,google_configured:false,billing_configured:false,square_configured:false,google_callback:'https://YOUR-APP/api/auth/google/callback'},note:'Offline sample: no external provider is connected.'});
if(path.startsWith('/api/auth/'))throw Error('Account creation, email and password actions run in the real app. Use Explore the demo in this offline preview.');
if(path==='/api/dashboard')return presult(pdashboard());
if(path==='/api/settings'&&method==='PUT'){b.revision=(b.revision||0)+1;preview.me.shop.settings=b;preview.me.shop.name=b.name;return presult({ok:true});}
if(path==='/api/publish'){preview.me.shop.published=b.published;return presult({published:b.published});}
if(path==='/api/quotes')return presult({quotes:preview.quotes});
if(path==='/api/customers'){const groups={};for(const b of preview.bookings){const c=groups[b.email]||{email:b.email,name:b.customer_name,phone:b.phone,bookings:0,completed_value:0,last_booking:0};c.bookings++;c.last_booking=Math.max(c.last_booking,b.start_ts);if(b.status==='completed')c.completed_value+=b.total;groups[b.email]=c;}return presult({customers:Object.values(groups)});}
if(path==='/api/operations')return presult({email_queue:[],activity:[]});
if(path==='/api/operations/retry-emails')return presult({message:'Offline preview: no emails are sent.'});
if(path==='/api/blocks'&&method==='GET')return presult({blocks:preview.blocks});
if(path==='/api/blocks'&&method==='POST'){const id=crypto.randomUUID();preview.blocks.push({...b,id});return presult({id});}
if(path.startsWith('/api/blocks/')&&method==='DELETE'){preview.blocks=preview.blocks.filter(x=>x.id!==path.split('/').pop());return presult({ok:true});}
if(path==='/api/preview-shop'||/^\/api\/public\/shops\/[^/]+$/.test(path))return presult(pshop());
if(path.endsWith('/price'))return presult(pprice(b));
if(path.endsWith('/slots')){if(method==='POST')return presult({slots:pslots(b.date,pprice(b).minutes),timezone:pshop().settings.timezone});const id=path.split('/').slice(-2)[0],item=pmanage(id).booking;return presult({slots:pslots(url.searchParams.get('date'),item.snapshot.minutes,id)});}
if(path==='/api/bookings'&&method==='GET')return presult({bookings:preview.bookings});
if(path.endsWith('/book')||(path==='/api/bookings'&&method==='POST')){const old=preview.bookings.find(x=>x.idempotency_key===b.idempotency_key);if(old)return presult({id:old.id,status:old.status,checkout_url:old.checkout_url,manage_url:'/manage/'+old.id});const p=pprice(b),id=crypto.randomUUID(),manual=path==='/api/bookings';const booking={...b,...p,id,snapshot:p,vehicle:b.vehicle_id,created_at:pnow(),updated_at:pnow(),end_ts:b.start_ts+p.minutes*60,busy_until:b.start_ts+(p.minutes+p.buffer_minutes)*60,status:'pending_approval',payment_method:pshop().settings.payment_method,country:'US',state:'TX',customer_name:'Demo Customer',email:'demo@example.com',phone:'5125550100',address:'100 Sample Street, Austin, TX 78701',payment_status:'unpaid',refunded:0,hold_until:pnow()+900,checkout_url:null};preview.bookings.unshift(booking);return presult({id,status:booking.status,payment_status:'unpaid',manage_url:'/manage/'+id,checkout_url:booking.checkout_url},201);}
if(/^\/api\/public\/manage\/[^/]+$/.test(path))return presult(pmanage(path.split('/').pop()));
if(path.startsWith('/api/demo-checkout/')){const id=path.split('/').pop(),item=pmanage(id).booking;item.status='confirmed';item.payment_status='paid';item.checkout_url=null;return presult({url:'/manage/'+id});}
if(path.endsWith('/action')||path.endsWith('/cancel')){const id=path.split('/').slice(-2)[0],item=pmanage(id).booking;item.status=b.action==='complete'?'completed':b.action==='no_show'?'no_show':'cancelled';return presult({message:'Sample booking updated. No real transaction.'});}
if(path.endsWith('/reschedule')){const item=pmanage(path.split('/').slice(-2)[0]).booking;item.start_ts=b.start_ts;item.end_ts=b.start_ts+item.snapshot.minutes*60;item.busy_until=item.end_ts+item.snapshot.buffer_minutes*60;return presult({message:'Sample appointment rescheduled.'});}
if(path.endsWith('/refund')){const item=pmanage(path.split('/').slice(-2)[0]).booking;item.payment_status='refunded';item.refunded=item.deposit;return presult({status:'refunded'});}
if(path.endsWith('/link'))return presult({url:'https://preview.invalid/manage/'+path.split('/').slice(-2)[0]});
if(path.endsWith('/quote-requests')){const photos=[];for(const file of opts.body.getAll('photos')){const id=crypto.randomUUID();window.__previewPhotoUrls[id]=URL.createObjectURL(file);photos.push(id);}preview.quotes.unshift({...b,customer_name:'Demo Customer',email:'demo@example.com',phone:'5125550100',address:'Sample US address',id:crypto.randomUUID(),status:'new',created_at:pnow(),vehicle:b.vehicle_id,selections:b,photos});return presult({message:'Sample quote and photos saved only in this preview. No email was sent.'});}
if(path.endsWith('/offer')||path.endsWith('/decline')){const id=path.split('/').slice(-2)[0],q=preview.quotes.find(x=>x.id===id);Object.assign(q,b,{status:path.endsWith('/offer')?'offered':'declined',expires_at:pnow()+604800});return presult({message:'Sample quote updated. The real app emails a private booking link.',url:'/quote/'+id});}
if(path.startsWith('/api/public/quotes/')){const q=preview.quotes.find(x=>x.id===path.split('/').pop());if(!q)throw Error('Sample quote not found.');return presult({shop:pshop(),quote:{...q,vehicle_id:q.vehicle}});}
if(path.startsWith('/api/integrations/')||path.startsWith('/api/billing/'))throw Error('External payments are disabled in this preview. The real application contains the Square and Paddle integrations.');
throw Error('This action is available in the real application, not the offline design preview.');
}catch(err){return presult({detail:err.message},400);}};
if(!crypto.randomUUID)crypto.randomUUID=()=>Array.from(crypto.getRandomValues(new Uint8Array(16)),x=>x.toString(16).padStart(2,'0')).join('');
window.__previewURL=new URL(location.hash.slice(1)||'/','https://preview.invalid');
window.__previewGo=url=>{window.__previewURL=new URL(url,'https://preview.invalid');history.replaceState({},'','#'+window.__previewURL.pathname+window.__previewURL.search);};
window.__previewAssign=url=>{window.__previewGo(url);render();};
window.addEventListener('hashchange',()=>{window.__previewURL=new URL(location.hash.slice(1)||'/','https://preview.invalid');render();});
document.addEventListener('click',event=>{const a=event.target.closest('a');if(!a)return;const href=a.getAttribute('href')||'';if(href.startsWith('/api/')||href.startsWith('mailto:')){event.preventDefault();toast('Exports and outgoing email run in the real application. This is an offline preview.',true);}});

document.addEventListener('click',event=>{const a=event.target.closest('a');if(!a)return;const href=a.getAttribute('href')||'';if(href.startsWith('https://preview.invalid/')){event.preventDefault();event.stopImmediatePropagation();window.__previewAssign(new URL(href).pathname);}},true);

// No form navigation, storage, external links or integrations leave this sandbox.
document.addEventListener('submit',event=>event.preventDefault(),true);
document.addEventListener('click',event=>{const a=event.target.closest('a');if(!a)return;const h=a.getAttribute('href')||'';if(h==='/demo'){event.preventDefault();event.stopImmediatePropagation();previewSignedIn=true;window.__previewAssign('/app');return;}if(/^(https?:|mailto:|tel:)/i.test(h)&&!h.startsWith('https://preview.invalid/')){event.preventDefault();event.stopImmediatePropagation();toast('External communication is disabled in this demo.',true);}},true);
