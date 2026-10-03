/* In-memory demonstration only. No account, outgoing message, review or charge is real. */
preview.messages=[];preview.reviews={};preview.customerNotes={};
function pexperience(path,url,method,b) {
  if(path==='/api/locations/cities') {
    const q=(url.searchParams.get('q')||'').toLowerCase(),state=url.searchParams.get('state')||'';
    const list=Object.values(PREVIEW_SEED.cities||{}).filter(c=>(!state||c.state===state)&&(!q||c.name.toLowerCase().includes(q)||c.zip_codes.includes(q)));
    return {cities:list.slice(0,40),more:list.length>40,fixture:true};
  }
  if(path.startsWith('/api/locations/cities/')) {const city=PREVIEW_SEED.cities[path.split('/').pop()];if(!city)throw Error('City not in the offline sample.');return city;}
  if(path.endsWith('/coverage')) {const zip=url.searchParams.get('zip'),valid=Object.values(PREVIEW_SEED.cities||{}).some(c=>c.zip_codes.includes(zip)),available=pshop().settings.zip_codes.includes(zip);return {valid,available,zip,states:available?['TX']:[],message:available?'The sample detailer serves ZIP '+zip+'.':'Outside the sample area. Use 78701 to explore booking.'};}
  const msg=path.match(/^\/api\/(bookings|quotes|public\/manage|public\/quote-requests)\/([^/]+)\/messages(\/read)?$/);
  if(msg) {
    const kind=msg[1].includes('quote')?'quote':'booking',id=msg[2],sender=msg[1].startsWith('public')?'customer':'owner';
    const rows=preview.messages.filter(m=>m.kind===kind&&m.thread_id===id);
    if(msg[3]){rows.filter(m=>m.sender!==sender).forEach(m=>m.read_at=pnow());return {ok:true};}
    if(method==='POST'){if(!rows.some(m=>m.idempotency_key===b.idempotency_key))preview.messages.push({...b,id:crypto.randomUUID(),kind,thread_id:id,sender,created_at:pnow(),read_at:null});return {message:'Demo message saved in memory. No email sent.'};}
    return {messages:rows,next_before:null};
  }
  if(path==='/api/conversations') {
    const groups=new Map();for(const m of preview.messages)groups.set(m.kind+':'+m.thread_id,m);
    return {conversations:Array.from(groups.values()).reverse().map(m=>{const item=(m.kind==='booking'?preview.bookings:preview.quotes).find(x=>x.id===m.thread_id);return {id:m.thread_id,kind:m.kind,customer_name:item?.customer_name||'Demo customer',status:item?.status||'new',last_message:m.body,updated_at:m.created_at,unread:preview.messages.filter(x=>x.kind===m.kind&&x.thread_id===m.thread_id&&x.sender==='customer'&&!x.read_at).length};})};
  }
  if(path==='/api/customers/detail') {const email=url.searchParams.get('email'),bookings=preview.bookings.filter(x=>x.email===email),quotes=preview.quotes.filter(x=>x.email===email),notes=preview.customerNotes[email]||{notes:'',revision:0};return {email,bookings,quotes,...notes,reviews:Object.entries(preview.reviews).filter(([id])=>bookings.some(b=>b.id===id)).map(([booking_id,v])=>({booking_id,...v}))};}
  if(path==='/api/customers/notes') {const old=preview.customerNotes[b.email]||{revision:0};if(old.revision!==b.revision)throw Error('Reopen the sample customer before saving.');preview.customerNotes[b.email]={notes:b.notes,revision:old.revision+1};return {...preview.customerNotes[b.email],message:'Private notes saved in this demo only.'};}
  if(/^\/api\/public\/quote-requests\/[^/]+$/.test(path)) {const q=preview.quotes.find(x=>x.id===path.split('/').pop());if(!q)throw Error('Sample request not found.');return {business:pshop().name,status:q.status,customer_name:q.customer_name,notes:q.notes,offer_available:q.status==='offered',booking_url:'/quote/'+q.id};}
  if(path.endsWith('/request-review')) {const id=path.split('/')[3],job=pmanage(id).booking;if(job.status!=='completed')throw Error('Finish the sample job and customer signature first.');preview.reviews[id]=preview.reviews[id]||{requested_at:pnow()};return {message:'Demo review requested. No email was sent. Open the sample customer link to review.'};}
  if(/^\/api\/public\/manage\/[^/]+\/review$/.test(path)) {const id=path.split('/')[4],review=preview.reviews[id];if(method==='POST'){if(!review)throw Error('Request a review from the completed job first.');Object.assign(review,b,{submitted_at:pnow()});return {message:'Simulated review saved in memory only.'};}return {review:review||null,can_review:!!review&&!review.submitted_at};}
  if(path==='/api/integrations/square/status')return {connected:false,location_id:null,pending_locations:[]};
  return undefined;
}
