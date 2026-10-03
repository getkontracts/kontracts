/* Customer experience. No payment provider SDKs or credentials belong in this file. */
'use strict';
const Experience = {areaDirty:false, areas:[], cities:new Map(), customers:[], searchGeneration:0, chat:null, chatTimer:null, invoices:[]};
const US_STATES='AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY'.split(' ');

function confirmInApp(message, title='Please confirm', accept='Continue') {
  return new Promise(resolve=>{
    const dialog=document.createElement('dialog');dialog.className='app-confirm';
    dialog.setAttribute('aria-label',title);
    dialog.innerHTML=`<div class="confirm-content"><h2>${e(title)}</h2><p class="mt16">${e(message)}</p><div class="modal-actions"><button type="button" class="btn" data-answer="no" autofocus>Go back</button><button type="button" class="btn primary" data-answer="yes">${e(accept)}</button></div></div>`;
    const finish=answer=>{dialog.close();dialog.remove();resolve(answer);};
    dialog.addEventListener('cancel',ev=>{ev.preventDefault();finish(false);},{once:true});
    dialog.addEventListener('click',ev=>{const answer=ev.target.closest('[data-answer]');if(answer)finish(answer.dataset.answer==='yes');});
    document.body.append(dialog);dialog.showModal();
  });
}

function squareReferral() {
  const raw=S.config?.square_referral_url;
  if(!raw)return '<p class="muted small mt16">New to Square? The operator can add a separate Get Square referral link in platform configuration.</p>';
  let url;try{url=new URL(raw);}catch{return '';}
  if(url.protocol!=='https:')return '';
  return `<div class="referral-box mt16"><a class="btn" href="${e(url.href)}" target="_blank" rel="noopener noreferrer sponsored">Get Square ${icon('up')}</a><p class="muted small">Referral link. Opens Square signup in a new tab. It does not connect an account, take a payment or change your subscription.</p></div>`;
}
function customerPaymentsPage() {
  const previous=S.settingsTab;S.settingsTab='payments';const detail=settingsPage();S.settingsTab=previous;
  const wrapper=document.createElement('div');wrapper.innerHTML=detail;
  const card=wrapper.querySelector('.settings-layout > section')?.innerHTML||'';
  return `${heading('Customer payments','Choose how clients pay your detailing business. This is not your Kontracts subscription.')}${squareConnectionNotice()}
    <div class="notice mb16">Square is only for customer service payments. Your $49 monthly software subscription is managed separately under Subscription.</div>
    ${paymentPreferences()}<section class="card card-pad mt24">${card}<div id="square-location-choice"></div>${squareReferral()}</section>`;
}

function serviceAreaPanel() {
  Experience.areas=structuredClone(S.me.shop.settings.service_areas||[]);Experience.areaDirty=false;
  return `<section class="service-areas"><h2>Where do you work?</h2><p class="muted small mt8">Select each US city, then choose all its listed ZIP codes or only the ZIP codes you serve. A saved service area is required before publishing.</p>
    <div class="form-grid mt16"><div class="field"><label for="city-state">State</label><select id="city-state"><option value="">All states</option>${US_STATES.map(s=>`<option>${s}</option>`).join('')}</select></div><div class="field"><label for="city-search">Find a city</label><input type="search" id="city-search" placeholder="Type a city or ZIP code" autocomplete="off" aria-controls="city-results"><small>Type at least two characters, or select a state.</small></div></div>
    <div id="city-results" class="city-results" aria-live="polite"></div><div id="area-selections" class="mt16"></div>
    <p class="muted small mt16">Citywide means all ZIP codes assigned to that postal city in the installed GeoNames registry. Postal areas are not exact city boundaries or street address verification. Check the list before saving.</p>
    <div class="row wrap mt16">${btn('Save service area','areas-save','primary')}<span id="area-status" role="status" class="small muted"></span></div></section>`;
}
async function hydrateAreas() {
  const target=$('#area-selections');if(!target)return;
  try {await Promise.all(Experience.areas.map(async a=>{if(!Experience.cities.has(a.city_id))Experience.cities.set(a.city_id,await api('/locations/cities/'+a.city_id));}));if(target.isConnected)renderAreas();}
  catch(err){if(target.isConnected)target.innerHTML=`<p class="notice error">${e(err.message)}</p>`;}
}
function renderAreas() {
  const target=$('#area-selections');if(!target)return;
  target.innerHTML=Experience.areas.map(a=>{const city=Experience.cities.get(a.city_id);if(!city)return '';
    return `<article class="area-card"><div class="row between"><h3>${e(city.name)}, ${e(city.state)}</h3>${btn('Remove','area-remove','small',`data-id="${e(a.city_id)}"`)}</div>
      <div class="mt16"><label class="check-row"><input type="radio" name="coverage-${e(a.city_id)}" data-area-mode="${e(a.city_id)}" value="city" ${a.coverage==='city'?'checked':''}><span>I serve every listed ZIP in this city (${city.zip_codes.length})</span></label>
      <label class="check-row mt8"><input type="radio" name="coverage-${e(a.city_id)}" data-area-mode="${e(a.city_id)}" value="zip_codes" ${a.coverage==='zip_codes'?'checked':''}><span>I serve specific ZIP codes only</span></label></div>
      ${a.coverage==='zip_codes'?`<fieldset class="zip-choices mt16"><legend>Select the ZIP codes you serve</legend>${city.zip_codes.map(z=>`<label class="check-row"><input type="checkbox" data-area-zip="${e(a.city_id)}" value="${e(z)}" ${a.zip_codes.includes(z)?'checked':''}><span>${e(z)}</span></label>`).join('')}</fieldset>`:`<details class="mt16"><summary>View covered ZIP codes</summary><p class="small muted mt8">${e(city.zip_codes.join(', '))}</p></details>`}</article>`;
  }).join('')||'<p class="notice amber">No cities selected. Search above and select a city to begin.</p>';
}
async function searchCities() {
  const target=$('#city-results');if(!target)return;
  const generation=++Experience.searchGeneration,q=$('#city-search').value.trim(),state=$('#city-state').value;
  if(q.length<2&&!state){target.innerHTML='';return;}
  target.textContent='Finding cities...';
  try {const r=await api('/locations/cities?q='+encodeURIComponent(q)+'&state='+state);if(generation!==Experience.searchGeneration||!target.isConnected)return;
    target.innerHTML=(r.fixture?'<p class="notice amber small">Development sample only. The operator must install the full US registry before launch.</p>':'')+
      (r.cities.length?r.cities.map(c=>`<button type="button" class="city-result" data-action="area-add" data-id="${e(c.id)}"><strong>${e(c.name)}, ${e(c.state)}</strong><span>Add city ${icon('plus')}</span></button>`).join(''):'<p class="notice">No matching cities. Check the spelling or search by a nearby ZIP code.</p>')+
      (r.more?'<p class="small muted">Showing 40 matches. Type more letters to narrow the results.</p>':'');
  } catch(err){if(generation===Experience.searchGeneration&&target.isConnected)target.innerHTML=`<p class="notice error">${e(err.message)}</p>`;}
}

function improvedCustomers(rows) {
  Experience.customers=rows;
  return `${heading('Customers, all in one place','Find contact details, past jobs, quote requests, private notes and conversations.')}
    <div class="field customer-search"><label for="customer-search">Find a customer</label><input type="search" id="customer-search" placeholder="Search name, email or phone"></div>
    <div id="customer-cards" class="customer-cards mt24">${customerCards(rows)}</div>`;
}
function customerCards(rows) {
  return rows.length?rows.map(c=>`<article class="card card-pad"><div class="row"><span class="avatar">${e(c.name.slice(0,1))}</span><div class="grow"><h3>${e(c.name)}</h3><p class="muted small break-word">${e(c.email)}</p></div></div>
    <div class="customer-stats mt16"><span>${c.bookings} bookings</span><span>${c.quotes} quotes</span>${c.needs_approval?`<span class="pill amber">${c.needs_approval} to approve</span>`:''}${c.unread_messages?`<span class="pill">${c.unread_messages} unread messages</span>`:''}</div>
    <p class="small muted mt16">Completed service value: ${money(c.completed_value)}<br>This is not a verified payment total.</p>
    <div class="row wrap mt16">${btn('Open customer','customer-open','primary small',`data-email="${e(c.email)}"`)}<a class="btn small" href="mailto:${e(c.email)}">Email</a></div></article>`).join(''):
    empty('No matching customers','Customers appear after a booking or quote request. Try a different name or email.','people');
}
async function openCustomer(email) {
  const d=await api('/customers/detail?email='+encodeURIComponent(email));
  const person=Experience.customers.find(c=>c.email===d.email);
  S.bookings=[...S.bookings.filter(b=>!d.bookings.some(x=>x.id===b.id)),...d.bookings];
  openModal(person?.name||'Customer history',`<p class="break-word">${e(d.email)}${person?.phone?' &middot; '+e(person.phone):''}</p>
    <section class="mt24"><h3>Bookings</h3>${d.bookings.map(b=>`<article class="history-row"><div><strong>${e(b.snapshot.service)}</strong><p class="muted small">${dateLabel(b.start_ts,{month:'short',day:'numeric',year:'numeric'})} &middot; ${money(b.total)}</p>${statusBadge(b.status)}</div><div class="row wrap">${btn('Open job','booking-open','small',`data-id="${e(b.id)}"`)}${threadButton('booking',b.id,'Message')}</div></article>`).join('')||'<p class="muted small">No bookings yet.</p>'}</section>
    <section class="mt24"><h3>Quote requests</h3>${d.quotes.map(q=>`<article class="history-row"><div><strong>${e(q.vehicle_notes||'Quote request')}</strong><p>${statusBadge(q.status)}</p></div>${threadButton('quote',q.id,'Message')}</article>`).join('')||'<p class="muted small">No quote requests.</p>'}</section>
    <section class="mt24"><h3>Service reviews</h3>${d.reviews.map(r=>`<p class="quote-note">${r.submitted_at?`<strong>${r.rating} out of 5</strong><br>${e(r.comment||'No comment added.')}`:'Review requested. Awaiting a response.'}</p>`).join('')||'<p class="muted small">Open a completed job to request a review.</p>'}</section>
    <form data-form="customer-notes" data-email="${e(d.email)}" data-revision="${d.revision}" class="mt24"><div class="field"><label for="customer-notes">Private business notes</label><textarea id="customer-notes" name="notes" maxlength="4000">${e(d.notes)}</textarea><small>Only your business can read these notes. Do not store card details or unnecessary sensitive information.</small></div><button type="submit" class="btn primary mt16">Save private notes</button></form>`);
}
function threadButton(kind,id,label='Ask for more details',token='') {
  return btn(label,'thread-open','small',`data-kind="${e(kind)}" data-id="${e(id)}" ${token?`data-token="${e(token)}"`:''}`);
}
async function conversationsPage() {
  const r=await api('/conversations');
  return `${heading('Customer messages','Keep booking questions and quote details with the right job.')}
    <p class="notice mb16">Need information before approval? Open the booking or quote and choose Ask for more details. Sending a message does not approve a booking or take a payment.</p>
    <div class="card">${r.conversations.map(t=>`<article class="conversation-row"><div class="grow"><div class="row wrap"><h3>${e(t.customer_name)}</h3>${statusBadge(t.status)}${t.unread?`<span class="pill green">${t.unread} unread</span>`:''}</div><p class="message-preview mt8">${e(t.last_message)}</p><small class="muted">${e(t.kind)} &middot; ${dateLabel(t.updated_at,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})}</small></div>${threadButton(t.kind,t.id,'Open conversation')}</article>`).join('')||empty('No conversations yet','Open a booking or quote to ask your customer a question.','chat')}</div>`;
}
function chatMarkup() {
  return `<p class="muted small">Messages are private to this request. Email notifications include a link back here. Reply here, not to the notification email.</p><div id="chat-log" class="chat-log mt16" role="log" aria-live="polite" aria-label="Conversation"></div><div class="row wrap mt8">${btn('Refresh messages','chat-refresh','small')}<span id="chat-status" class="muted small" role="status"></span></div><form data-form="chat-send" class="mt16"><div class="field"><label for="chat-message">Your message</label><textarea id="chat-message" name="body" required maxlength="4000" placeholder="Ask a question or share the details needed for this job"></textarea></div><button type="submit" class="btn primary mt16">Send message</button></form>`;
}
async function openThread(kind,id,token='') {
  if(PhotoState.pending.length)throw Error('Save or remove your selected photos before opening messages.');
  if(!await confirmModalDraft())return;
  clearTimeout(Experience.chatTimer);
  Experience.chat={kind,id,token,base:token?'/public/'+(kind==='booking'?'manage':'quote-requests')+'/'+encodeURIComponent(token):'/'+(kind==='booking'?'bookings':'quotes')+'/'+id,items:[],key:crypto.randomUUID(),loading:false};
  openModal('Private customer conversation',chatMarkup());
  await refreshChat(true);
}
async function refreshChat(initial=false,older=false) {
  const t=Experience.chat,target=$('#chat-log');if(!t||!target||!$('#modal').open)return;if(t.loading){t.refreshAgain=true;return;}
  t.loading=true;clearTimeout(Experience.chatTimer);
  const status=$('#chat-status');if(initial)status.textContent='Loading conversation...';
  try {
    const r=await api(t.base+'/messages'+(older&&t.before?'?before='+t.before:''));if(Experience.chat!==t||!target.isConnected)return;
    if(older){t.items=[...r.messages,...t.items];t.before=r.next_before;}
    else {const merged=new Map(t.items.map(x=>[x.id,x]));r.messages.forEach(x=>merged.set(x.id,x));t.items=Array.from(merged.values());if(initial)t.before=r.next_before;}
    target.innerHTML=(t.before?btn('Load earlier messages','chat-older','small mb16'):'')+(t.items.map(m=>`<article class="chat-message ${m.sender===(t.token?'customer':'owner')?'mine':''}"><div class="row between"><strong>${m.sender==='owner'?'Detailer':'Customer'}</strong><small>${dateLabel(m.created_at,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})}</small></div><p>${e(m.body)}</p></article>`).join('')||'<p class="muted">No messages yet. Start the conversation below.</p>');
    if(initial)target.scrollTop=target.scrollHeight;
    status.textContent='Up to date. Updates every 15 seconds while this conversation is open.';
    await api(t.base+'/messages/read',{method:'POST'});
  }catch(err){if(target.isConnected)status.textContent=err.message;}
  finally {t.loading=false;if(Experience.chat===t&&target.isConnected){const delay=t.refreshAgain?50:15000;t.refreshAgain=false;Experience.chatTimer=setTimeout(()=>{if(!document.hidden&&$('#modal')?.open)refreshChat();},delay);}}
}
async function publicRequest(raw) {
  const r=await api('/public/quote-requests/'+encodeURIComponent(raw));
  $('#app').innerHTML=`<main id="main" class="booking-manage">${brand()}<div class="card card-pad mt32"><span class="eyebrow">${e(r.business)}</span><h1 class="mt16">Your quote request</h1><p class="mt16">${statusBadge(r.status)}</p><p class="mt16">Hello ${e(r.customer_name)}. No appointment is reserved until you select a quoted service and the detailer approves the booking.</p><p class="quote-note">${e(r.notes||'No additional notes submitted.')}</p><div class="row wrap mt16">${threadButton('quote','', 'Read or send a message',raw)}${r.offer_available?nav('View quote and choose a time',r.booking_url,'btn primary'):''}</div><p class="small muted mt24">Keep this link private. Anyone with this link can access this request and its messages.</p></div></main>`;
}
async function enhanceManage(raw) {
  const target=$('#customer-experience');if(!target)return;
  target.innerHTML=`<section class="card card-pad mt24"><h3>Message your detailer</h3><p class="muted small mt8">Share missing details or ask about this booking. Messages do not change approval or payment status.</p><div class="mt16">${threadButton('booking','', 'Open private conversation',raw)}</div></section><section id="review" class="mt24"></section>`;
  try {const r=await api('/public/manage/'+encodeURIComponent(raw)+'/review');const review=$('#review');if(!review)return;
    if(r.can_review)review.innerHTML=`<form data-form="customer-review" data-token="${e(raw)}" class="card card-pad"><h3>How was your detail?</h3><p class="muted small mt8">Share an honest review. All ratings are welcome. Your review is shared privately with the detailer.</p><div class="field mt16"><label for="review-rating">Rating</label><select id="review-rating" name="rating" required><option value="">Choose a rating</option>${[5,4,3,2,1].map(x=>`<option value="${x}">${x} out of 5</option>`).join('')}</select></div><div class="field mt16"><label for="review-comment">Your comments (optional)</label><textarea id="review-comment" name="comment" maxlength="2000"></textarea></div><button type="submit" class="btn primary mt16">Submit review</button></form>`;
    else if(r.review?.submitted_at)review.innerHTML=`<div class="card card-pad"><h3>Thank you for your review</h3><p class="mt16">${r.review.rating} out of 5</p><p class="quote-note">${e(r.review.comment||'Your rating has been shared with the detailer.')}</p></div>`;
  }catch(err){const review=$('#review');if(review)review.innerHTML=`<p class="notice">${e(err.message)}</p>`;}
}
async function openRecordPayment(id) {
  const list=Experience.invoices.length?Experience.invoices:(await api('/invoices')).invoices;
  const i=list.find(x=>x.id===id);if(!i)throw Error('Refresh invoices before recording this payment.');
  openModal('Record a payment received',`<p>Invoice <strong>${e(i.number)}</strong> has <strong>${money(i.balance)}</strong> remaining. This records money you already received; it does not charge the customer.</p>
    <form data-form="record-payment" data-id="${e(id)}"><div class="field mt16"><label for="receipt-method">How did the customer pay?</label><select id="receipt-method" name="method" required><option value="cash">Cash</option><option value="venmo">Venmo</option><option value="cash_app">Cash App</option><option value="other">Other direct payment</option></select></div>
    ${field('Payment reference (optional)','reference','','text','maxlength="120" placeholder="Transaction reference, not account or card details"')}
    <p class="notice mt16">For Venmo or Cash App, check your own account before confirming. A screenshot alone does not verify payment.</p>
    <label class="check-row mt16"><input type="checkbox" name="confirmed" required><span>I have actually received the full remaining ${money(i.balance)}.</span></label>
    <div class="modal-actions">${btn('Cancel','modal-close')}<button type="submit" class="btn primary">Record received payment</button></div></form>
    <hr><p class="muted small">Payment proof can be saved separately to the job. Avoid uploading account balances or unrelated personal information.</p>${btn('Add payment proof','proof-open','small mt16',`data-id="${e(i.booking_id)}"`)}`);
}

async function finishWizardResult(w) {
  const result=w.result;
  if(!result)throw Error('No saved booking was found. Submit the request first.');
  if(w.manual){toast('Request saved. Review and approve it from Bookings.');await go('/app/bookings');}
  else if(result.checkout_url&&result.status==='held'){if(result.checkout_url.startsWith('/'))await go(result.checkout_url);else window.location.assign(result.checkout_url);}
  else await go(result.manage_url);
}
async function improvedSubmitWizard(form) {
  const w=S.wizard;if(w.preview)throw Error('This is a draft preview. Publish your page before accepting bookings.');
  if(PhotoState.busy||PhotoState.uploadBusy)throw Error('Your photos are still being prepared. Submit again when preparation finishes.');
  const fd=new FormData(form),customer={country:'US',state:fd.get('state')||'',customer_name:fd.get('customer_name'),email:fd.get('email'),phone:fd.get('phone'),address:fd.get('address'),vehicle_notes:fd.get('vehicle_notes'),notes:fd.get('notes'),has_water:fd.has('has_water'),has_power:fd.has('has_power')};w.customer=customer;
  if(w.condition==='heavy') {
    const data={...selection(),...customer,website:fd.get('website')||'',idempotency_key:w.idempotency_key};delete data.has_water;delete data.has_power;
    const request=new FormData();request.append('data',JSON.stringify(data));for(const f of w.photos||[])request.append('photos',f.file);
    const result=await api('/public/shops/'+w.shop.slug+'/quote-requests',{method:'POST',body:request});
    for(const p of w.photos||[])URL.revokeObjectURL(p.src);w.photos=[];
    if(result.conversation_url)await go(result.conversation_url);
    else $('#wizard-content').innerHTML=`<div class="card">${empty('Your request is on its way.',result.message,'check')}</div>`;
    return;
  }
  if(!w.submission)w.submission={...selection(),...customer,start_ts:w.start_ts,idempotency_key:w.idempotency_key,accepted_policy:true,website:fd.get('website')||''};
  if(!w.result)w.result=await api(w.manual?'/bookings':'/public/shops/'+w.shop.slug+'/book',{method:'POST',body:w.submission});
  if(w.photos?.length) {
    if(!w.photoSubmission)w.photoSubmission={photos:[...w.photos],profile:PhotoState.profile};
    const attachments=w.photoSubmission.photos;
    const body=new FormData();body.append('category','condition');body.append('caption','Submitted with the booking request');body.append('profile',w.photoSubmission.profile);
    w.photoUploadKey=w.photoUploadKey||crypto.randomUUID();body.append('upload_key',w.photoUploadKey);body.append('original_sizes',JSON.stringify(attachments.map(p=>p.originalBytes)));
    for(const p of attachments)body.append('files',p.file);
    PhotoState.uploadBusy=true;
    try {const token=w.result.manage_url.split('/').pop();await api(w.manual?'/bookings/'+w.result.id+'/photos':'/public/manage/'+encodeURIComponent(token)+'/photos',{method:'POST',body});for(const p of w.photos)URL.revokeObjectURL(p.src);w.photos=[];}
    catch(err){formError(form,'Your booking request is already saved, but the photos were not confirmed saved. '+err.message+' Retry this same form to upload them without creating another booking.');if(!$('#continue-saved-request'))form.insertAdjacentHTML('beforeend',`<div id="continue-saved-request" class="mt16">${btn('Continue to saved booking','wizard-continue-saved')}</div>`);return;}
    finally {PhotoState.uploadBusy=false;}
  }
  await finishWizardResult(w);
}
function modalHasDraft() {
  return Boolean($('#modal')?.open && ($('#chat-message')?.value.trim() || $('#modal [data-form="customer-notes"]')?.dataset.unsaved==='true'));
}
async function confirmModalDraft() {
  return !modalHasDraft() || await confirmInApp('Discard the unsaved message or private notes in this dialog?', 'Unsaved changes', 'Discard changes');
}
async function improvedPriceWizard() {
  const w=S.wizard;if(!w)return;const generation=++w.version,requested=selection(),fingerprint=JSON.stringify(requested),fresh=()=>generation===w.version&&S.wizard===w&&JSON.stringify(selection())===fingerprint;w.priceError='';w.coverage=null;
  if(!/^[0-9]{5}$/.test(w.zip)){w.price=null;updateWizardPrice();return;}
  try {const checked=await api(wizardBase()+'/coverage?zip='+encodeURIComponent(w.zip));if(!fresh())return;
    if(!checked.available)throw Error(checked.message);w.coverage=checked;
    if(checked.states?.length===1&&!w.customer?.state)w.customer={...w.customer,state:checked.states[0]};
    const quoted=w.condition==='heavy'?null:await api(wizardBase()+'/price',{method:'POST',body:requested});if(!fresh())return;w.price=quoted;
  }catch(err){if(!fresh())return;w.price=null;w.priceError=err.message;}
  updateWizardPrice();const note=$('#zip-coverage-status');if(note){note.textContent=w.priceError||w.coverage?.message||'';note.className='small mt8 '+(w.priceError?'error-text':'muted');}
}

function demoPurchaseURL() {
  const actual=window['location'];
  return /^https?:$/.test(actual.protocol)&&actual.hostname!=='preview.invalid'?actual.origin+'/register':'https://getkontracts.app/register';
}
function demoExitLink(label='Leave demo and subscribe') {return `<a href="${e(demoPurchaseURL())}" class="btn small" data-demo-exit>${e(label)}</a>`;}

async function experienceAction(name,node) {
  const id=node.dataset.id;
  if(['customer-open','booking-open','photos-open','photo-view','finish-invoice','before-open','after-open','proof-open','invoice-cash'].includes(name)&&!await confirmModalDraft())return true;
  switch(name) {
    case'area-add':{if(Experience.areas.some(a=>a.city_id===id)){toast('This city is already selected. Edit its coverage below.');return true;}if(Experience.areas.length>=30)throw Error('You can select up to 30 cities.');const city=await api('/locations/cities/'+id);Experience.cities.set(id,city);Experience.areas.push({city_id:id,coverage:'city',zip_codes:[]});S.dirty=true;Experience.areaDirty=true;renderAreas();return true;}
    case'area-remove':Experience.areas=Experience.areas.filter(a=>a.city_id!==id);S.dirty=true;Experience.areaDirty=true;renderAreas();return true;
    case'areas-save':{if($('[data-form="settings-business"]')?.dataset.unsaved==='true')throw Error('Save your business profile changes first, then save this service area. Your city selections will be retained.');if(!Experience.areas.length)throw Error('Select at least one city before saving your service area.');const s=structuredClone(S.me.shop.settings);s.service_areas=structuredClone(Experience.areas);s.service_area_reviewed=true;await persistSettings(s);Experience.areaDirty=false;$('#area-status').textContent='Saved. New bookings are checked against this service area.';return true;}
    case'customer-open':await openCustomer(node.dataset.email);return true;
    case'thread-open':await openThread(node.dataset.kind,id||'',node.dataset.token||'');return true;
    case'chat-refresh':await refreshChat();return true;
    case'chat-older':await refreshChat(false,true);return true;
    case'request-review':toast((await api('/bookings/'+id+'/request-review',{method:'POST'})).message);return true;
    case'before-open':case'after-open':case'proof-open':await openJobPhotos(id);$('#photo-category').value=name==='proof-open'?'payment_proof':name==='before-open'?'before':'after';return true;
    case'invoice-cash':await openRecordPayment(id);return true;
    case'wizard-continue-saved':for(const p of S.wizard.photos||[])URL.revokeObjectURL(p.src);S.wizard.photos=[];await finishWizardResult(S.wizard);return true;
    case'billing-refresh':await syncMembership();return true;
  }
  return false;
}
async function experienceSubmit(form) {
  const fd=new FormData(form);
  switch(form.dataset.form) {
    case'square-location':toast((await api('/integrations/square/location',{method:'POST',body:{location_id:fd.get('location_id')}})).message);await refreshMe();await renderPage();return true;
    case'customer-notes':{const r=await api('/customers/notes',{method:'PUT',body:{email:form.dataset.email,notes:fd.get('notes'),revision:Number(form.dataset.revision)}});form.dataset.revision=String(r.revision);form.dataset.unsaved='false';toast(r.message);return true;}
    case'chat-send':{const t=Experience.chat,text=String(fd.get('body')||'').trim();if(!text)throw Error('Write a message before sending.');
      if(t.attempt&&t.attempt.body!==text)throw Error('The previous send was not confirmed. Retry its original text or refresh this conversation before sending a different message.');
      t.attempt=t.attempt||{body:text,idempotency_key:t.key};await api(t.base+'/messages',{method:'POST',body:t.attempt});t.attempt=null;t.key=crypto.randomUUID();form.reset();await refreshChat();$('#chat-log').scrollTop=$('#chat-log').scrollHeight;return true;}
    case'customer-review':toast((await api('/public/manage/'+encodeURIComponent(form.dataset.token)+'/review',{method:'POST',body:{rating:Number(fd.get('rating')),comment:fd.get('comment')||''}})).message);await enhanceManage(form.dataset.token);return true;
    case'record-payment':if(!fd.has('confirmed'))throw Error('Confirm that you received the payment before recording it.');toast((await api('/invoices/'+form.dataset.id+'/received-in-person',{method:'POST',body:{confirmation:'PAYMENT RECEIVED',method:fd.get('method'),reference:fd.get('reference')||''}})).message);await closeModal();await renderPage();return true;
  }
  return false;
}
async function syncMembership() {
  const r=await api('/billing/sync',{method:'POST'});await refreshMe();toast(r.message);await renderPage('/app/billing');
}
let citySearchTimer;
document.addEventListener('input',event=>{
  const draft=event.target.closest('[data-form="customer-notes"], [data-form="settings-business"]');if(draft)draft.dataset.unsaved='true';
  if(event.target.id==='city-search'){clearTimeout(citySearchTimer);citySearchTimer=setTimeout(searchCities,250);}
  if(event.target.id==='customer-search'){const q=event.target.value.trim().toLowerCase();$('#customer-cards').innerHTML=customerCards(Experience.customers.filter(c=>[c.name,c.email,c.phone].join(' ').toLowerCase().includes(q)));}
});
document.addEventListener('change',event=>{
  const n=event.target;if(n.id==='city-state')searchCities();
  if(n.dataset.areaMode){const a=Experience.areas.find(a=>a.city_id===n.dataset.areaMode);if(a){a.coverage=n.value;S.dirty=true;Experience.areaDirty=true;renderAreas();}}
  if(n.dataset.areaZip){const a=Experience.areas.find(a=>a.city_id===n.dataset.areaZip);if(a){a.zip_codes=n.checked?[...new Set([...a.zip_codes,n.value])]:a.zip_codes.filter(z=>z!==n.value);S.dirty=true;Experience.areaDirty=true;}}
});
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&$('#chat-log')&&$('#modal')?.open)refreshChat();});
let membershipCheckRunning=false;
async function startMembershipVerification() {
  if(membershipCheckRunning)return;membershipCheckRunning=true;
  try {
    for(let attempt=0;attempt<3;attempt++) {
      if(location.pathname!=='/app/billing')break;
      const target=$('#subscription-sync-status');if(target)target.textContent='Checking your subscription directly with Paddle...';
      try {const r=await api('/billing/sync',{method:'POST'});await refreshMe();
        if(r.confirmed&&location.pathname==='/app/billing'){$('#main').innerHTML=billingPage();$('#subscription-sync-status').textContent=r.message;break;}
        if(target?.isConnected)target.textContent=r.message;
      }catch(err){if(target?.isConnected)target.textContent=err.message;break;}
      if(attempt<2)await new Promise(resolve=>setTimeout(resolve,4000));
    }
  }finally{membershipCheckRunning=false;}
}
function squareConnectionNotice() {
  const messages={
    'square-connected':'Square connected successfully. Service payments use this account; your Kontracts subscription is unchanged.',
    'square-declined':'Square connection was cancelled. No account was changed. Use Connect Square to try again.',
    'square-select-location':'Choose which Square location this detailing business uses. Your previous connection remains unchanged until you save.',
    'square-location-conflict':'This Square account is linked to another workspace, or existing payments use a different location. Reconnect the original account and location, or contact support. Nothing was replaced.',
    'square-location-unavailable':'No eligible Square location was found. In Square, confirm an active US location with USD card processing, then reconnect.',
    'square-connect-error':'Square could not finish connecting. Check your connection and try Connect Square again. Existing payments and your subscription were not changed.'
  };
  const text=messages[new URLSearchParams(location.search).get('notice')];return text?`<div class="notice mb16" role="status">${e(text)}</div>`:'';
}
async function hydrateSquareConnection() {
  const target=$('#square-location-choice');if(!target)return;
  try {const r=await api('/integrations/square/status');if(!target.isConnected)return;
    if(r.pending_locations.length)target.innerHTML=`<form data-form="square-location" class="notice mt16"><h3>Select your Square location</h3><div class="field mt16"><label for="square-location">Active US location</label><select id="square-location" name="location_id" required><option value="">Choose a location</option>${r.pending_locations.map(x=>`<option value="${e(x.id)}">${e(x.name)} (${e(x.id)})</option>`).join('')}</select></div><p class="small mt8">Finish within 10 minutes of authorization. No deposits move between locations.</p><button type="submit" class="btn primary mt16">Use this Square location</button></form>`;
    else if(r.connected)target.innerHTML=`<p class="muted small mt16">Connected location: ${e(r.location_id)}</p>`;
  }catch(err){if(target.isConnected)target.innerHTML=`<p class="notice error mt16">${e(err.message)}</p>`;}
}
