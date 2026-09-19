/* Kontracts private car photos. Native capture, local compression, real API storage. */
'use strict';
const PhotoState = {pending: [], context: null, key: null, busy: false, uploadBusy: false,
  profile: 'compact', library: [], filter: 'all', search: '', usage: null, current: []};
const photoBytes = n => n < 1024 ? `${n} B` : n < 1024*1024 ? `${Math.round(n/1024)} KB` : `${(n/1024/1024).toFixed(1)} MB`;
const photoLabel = p => ({before:'Before',after:'After',condition:'Condition'})[p] || 'Photo';

function photoPicker(context, limit=3) {
  const quote = context === 'quote';
  const pending = quote ? (S.wizard?.photos || []) : PhotoState.pending;
  return `<div class="photo-picker" data-photo-draft="${context}">
    <div class="photo-picker-heading"><span class="photo-symbol">${icon('camera')}</span><div><strong>${quote ? 'Show us your car' : 'Add car photos'}</strong><p>Take a photo or choose from your gallery. We make the files smaller for you.</p></div></div>
    ${quote ? '' : `<div class="field photo-quality"><label for="photo-quality">Photo quality</label><select id="photo-quality" data-photo-quality ${pending.length?'disabled':''}><option value="compact" ${PhotoState.profile==='compact'?'selected':''}>Compact - smaller files, up to 1280 px</option><option value="detail" ${PhotoState.profile==='detail'?'selected':''}>More detail - up to 2048 px</option></select></div>`}
    <div class="photo-picker-buttons">
      <label class="btn primary" for="${context}-camera">${icon('camera')} Take photo</label>
      <input class="sr-only" id="${context}-camera" type="file" accept="image/*" capture="environment" data-photo-input="${context}" aria-label="Take a car photo">
      <label class="btn" for="${context}-gallery">${icon('image')} Choose photos</label>
      <input class="sr-only" id="${context}-gallery" type="file" accept="image/jpeg,image/png,image/webp,image/heic,image/heif" multiple data-photo-input="${context}" aria-label="Choose car photos">
    </div>
    <p class="photo-footnote">Up to ${limit} at a time. GPS and other image metadata are removed on save. Camera availability depends on your browser.</p>
    <div id="${context}-photo-status" class="photo-status" role="status" aria-live="polite"></div>
    <div id="${context}-photo-pending" class="pending-photos">${pendingPhotoMarkup(pending,context)}</div>
  </div>`;
}

function pendingPhotoMarkup(items,context) {
  return items.map((p,i)=>`<div class="pending-photo"><img src="${e(p.src)}" alt="Selected vehicle photo ${i+1}"><div><strong>Photo ${i+1}</strong><span>${photoBytes(p.originalBytes)} ${icon('arrow')} ${photoBytes(p.file.size)}</span><small>${p.width} &times; ${p.height} px &middot; ready to save</small></div><button class="icon-btn" type="button" data-photo-remove="${i}" data-photo-context="${context}" aria-label="Remove selected photo ${i+1}">${icon('close')}</button></div>`).join('');
}

async function decodeBrowserImage(file) {
  if ('createImageBitmap' in window) {
    try { return await createImageBitmap(file, {imageOrientation:'from-image'}); } catch (_) {}
  }
  const url = URL.createObjectURL(file);
  try {
    return await new Promise((resolve,reject)=>{
      const image = new Image(); image.onload=()=>resolve(image); image.onerror=()=>reject(new Error('This browser could not open that image. Export HEIC/HEIF as JPEG, then try again.')); image.src=url;
    });
  } finally { URL.revokeObjectURL(url); }
}

async function compactPhoto(file, profile='compact') {
  if (file.size > 30*1024*1024) throw new Error('Choose a photo smaller than 30 MB.');
  if (!file.size) throw new Error('That file is empty. Choose a photo.');
  const source = await decodeBrowserImage(file);
  try {
    const width=source.naturalWidth||source.width, height=source.naturalHeight||source.height;
    if (width*height>50_000_000) throw new Error('This photo is larger than 50 megapixels. Resize it before adding it.');
    const edge = profile==='detail'?2048:1280, target=(profile==='detail'?350:150)*1024;
    const scale=Math.min(1,edge/Math.max(width,height));
    const canvas=document.createElement('canvas'); canvas.width=Math.max(1,Math.round(width*scale));canvas.height=Math.max(1,Math.round(height*scale));
    const ctx=canvas.getContext('2d',{alpha:false});
    if (!ctx) throw new Error('Photo conversion is not available in this browser. Try a recent browser.');
    ctx.fillStyle='#ffffff';ctx.fillRect(0,0,canvas.width,canvas.height);ctx.drawImage(source,0,0,canvas.width,canvas.height);
    let blob;
    const encode=(quality,type='image/webp')=>new Promise((resolve,reject)=>canvas.toBlob(b=>b?resolve(b):reject(new Error('The browser could not compress this photo.')),type,quality));
    for(let pass=0;pass<4;pass++) {
      for(const quality of [.78,.68,.58]) {
        blob=await encode(quality);
        if(blob.type!=='image/webp') blob=await encode(quality,'image/jpeg');
        if(blob.size<=target) break;
      }
      if(blob.size<=target||Math.max(canvas.width,canvas.height)<=640) break;
      const copy=document.createElement('canvas');copy.width=canvas.width;copy.height=canvas.height;copy.getContext('2d').drawImage(canvas,0,0);
      const nextScale=Math.max(640,Math.round(Math.max(canvas.width,canvas.height)*.82))/Math.max(canvas.width,canvas.height);
      canvas.width=Math.max(1,Math.round(canvas.width*nextScale));canvas.height=Math.max(1,Math.round(canvas.height*nextScale));
      ctx.drawImage(copy,0,0,canvas.width,canvas.height); copy.width=copy.height=1;
    }
    if(!blob||blob.size>1024*1024) throw new Error('This photo could not be made small enough. Crop it and try again.');
    const extension=blob.type==='image/webp'?'webp':'jpg';
    const optimized=new File([blob],`vehicle-${crypto.randomUUID()}.${extension}`,{type:blob.type});
    return {file:optimized,src:URL.createObjectURL(blob),originalBytes:file.size,width:canvas.width,height:canvas.height};
  } finally { if(source.close)source.close(); }
}

function clearPhotoPending() {
  for(const p of PhotoState.pending)URL.revokeObjectURL(p.src);
  PhotoState.pending=[];PhotoState.key=null;
}

async function handlePhotoSelection(input) {
  const context=input.dataset.photoInput, files=[...input.files]; input.value='';
  if(!files.length)return;
  if(PhotoState.busy||PhotoState.uploadBusy){toast('Finish the current photo operation first.',true);return;}
  const items=context==='quote'?S.wizard.photos:PhotoState.pending;
  if(items.length+files.length>3){toast('Select up to three photos at a time. Save these before adding more.',true);return;}
  PhotoState.busy=true;
  const root=input.closest('.photo-picker'); root?.classList.add('processing');
  const status=document.getElementById(context+'-photo-status');
  const submitButton=context==='quote'?$('[data-form="wizard-details"] button[type="submit"]'):$('[data-action="photos-save"]');
  if(submitButton)submitButton.disabled=true;
  const errors=[];
  try {
    for(let i=0;i<files.length;i++) {
      if(status)status.textContent=`Making photo ${i+1} of ${files.length} smaller...`;
      try { items.push(await compactPhoto(files[i],context==='quote'?'compact':PhotoState.profile)); }
      catch(error){errors.push(error.message);}
    }
    if(context!=='quote')PhotoState.key=crypto.randomUUID();
    const pending=document.getElementById(context+'-photo-pending');
    if(pending)pending.innerHTML=pendingPhotoMarkup(items,context);
    if(status)status.textContent=items.length?`${items.length} photo${items.length===1?'':'s'} ready. ${photoBytes(items.reduce((n,p)=>n+p.file.size,0))} before the final server optimization.`:'No photos selected.';
    const quality=document.getElementById('photo-quality');if(quality)quality.disabled=items.length>0;
    if(errors.length)toast(errors[0],true);
  } finally {
    PhotoState.busy=false;root?.classList.remove('processing');
    if(submitButton)submitButton.disabled=context==='quote'?false:items.length===0;
  }
}

function photoCard(p, compact=false) {
  return `<article class="vehicle-photo-card ${compact?'compact':''}"><button type="button" class="photo-open" data-action="photo-view" data-id="${e(p.id)}" aria-label="Open ${e(photoLabel(p.category))} photo${p.customer_name?' for '+e(p.customer_name):''}"><img loading="lazy" decoding="async" src="${e(p.thumbnail_url)}" alt="${e(p.caption||photoLabel(p.category)+' vehicle photo')}"><span class="photo-category ${e(p.category)}">${photoLabel(p.category)}</span></button><div class="photo-card-info"><strong>${e(p.customer_name||p.caption||photoLabel(p.category)+' photo')}</strong><small>${e(p.vehicle_notes||p.caption||'Private job record')}</small><div><span>${photoBytes(p.size_bytes)}</span><span>${p.uploaded_by==='customer'?'Customer upload':'Owner upload'}</span></div></div></article>`;
}

async function photosPage() {
  const data=await api('/media');PhotoState.library=data.photos;PhotoState.usage=data.storage;
  return renderPhotoLibrary();
}
function renderPhotoLibrary() {
  const usage=PhotoState.usage||{count:0,used_bytes:0,limit_bytes:1};
  const matches=PhotoState.library.filter(p=>(PhotoState.filter==='all'||p.category===PhotoState.filter)&&`${p.customer_name} ${p.vehicle_notes} ${p.caption}`.toLowerCase().includes(PhotoState.search.toLowerCase()));
  const percent=Math.min(100,100*usage.used_bytes/usage.limit_bytes);
  return `${heading('Every car. Every detail.','Private before, after and condition photos, attached to the right job.',`<div class="row wrap"><a class="btn" href="/api/export/photos.zip">${icon('download')} Export photos</a>${btn(icon('camera')+' Add car photos','photos-choose-job','primary')}</div>`)}
  <div class="photo-overview"><div><span class="eyebrow">Job photo library</span><h2>${usage.count} <span>photos saved</span></h2><p>Small files. A clear record of your work.</p></div><div class="photo-storage"><div><strong>Photo storage</strong><span>${photoBytes(usage.used_bytes)} / ${photoBytes(usage.limit_bytes)}</span></div><progress max="100" value="${percent}" aria-label="Photo storage used"></progress><small>Includes job photos, quote photos and thumbnails. Limits are workspace-wide.</small></div></div>
  <div class="photo-library-toolbar"><div class="segmented" aria-label="Filter photos">${['all','before','after','condition'].map(category=>`<button data-action="photos-filter" data-value="${category}" class="${PhotoState.filter===category?'active':''}" aria-pressed="${PhotoState.filter===category}">${category==='all'?'All photos':photoLabel(category)}</button>`).join('')}</div><input type="search" class="photo-search" placeholder="Search customer, car or caption" aria-label="Search car photos" data-photo-search value="${e(PhotoState.search)}"></div>
  <div class="vehicle-photo-grid">${matches.length?matches.map(p=>photoCard(p)).join(''):`<div class="card photo-empty">${empty(PhotoState.library.length?'No matching photos':'Your work deserves a clear record',PhotoState.library.length?'Try a different search or filter.':'Choose a booking, then take the first before photo. It will appear here, safely linked to that customer.','camera',btn(icon('plus')+' Add the first photo','photos-choose-job','primary'))}</div>`}</div>
  <p class="photo-library-note">Only your workspace can view this library. Compression is lossy; keep original evidence separately when documenting damage. The newest 500 job photos are shown.</p>`;
}

async function choosePhotoJob() {
  if(PhotoState.uploadBusy)throw new Error('Wait until the photo save finishes.');
  const rows=(await api('/bookings')).bookings;S.bookings=rows;
  openModal('Choose a job',`<p class="muted small mb16">Photos stay with their booking and customer record.</p><input type="search" id="photo-job-search" placeholder="Search customer or vehicle" aria-label="Search jobs for photos"><div id="photo-job-options" class="photo-job-options">${photoJobOptions(rows)}</div>`);
}
function photoJobOptions(rows) {
  return rows.length?rows.slice().sort((a,b)=>b.start_ts-a.start_ts).slice(0,80).map(b=>`<button class="photo-job-option" data-action="photos-open" data-id="${e(b.id)}"><span class="avatar">${e(b.customer_name[0])}</span><span class="grow"><strong>${e(b.customer_name)}</strong><small>${e(b.vehicle_notes||b.snapshot.vehicle)} &middot; ${dateLabel(b.start_ts,{month:'short',day:'numeric'})}</small></span>${icon('arrow')}</button>`).join(''):empty('No jobs yet','Create a booking first, then attach photos to it.','calendar',nav('Add a booking','/app/new-booking','btn primary'));
}

async function openJobPhotos(id, keepDraft=false) {
  if(PhotoState.uploadBusy)throw new Error('Wait until the photo save finishes.');
  if(!keepDraft&&PhotoState.pending.length&&!confirm('Discard the photos that have not been saved?'))return;
  if(!keepDraft)clearPhotoPending();
  PhotoState.context={type:'owner',id};
  const data=await api('/bookings/'+id+'/photos');PhotoState.current=data.photos;
  openModal('Car photos',`<div class="job-photo-title"><div><h3>${e(data.customer_name)}</h3><p>${e(data.vehicle_notes||'Vehicle record')}</p></div><span class="pill">${data.photos.length} / ${data.photo_limit} photos</span></div><div class="photo-upload-fields"><div class="field"><label for="photo-category">Photo label</label><select id="photo-category"><option value="before">Before</option><option value="after">After</option><option value="condition">Condition / existing damage</option></select></div>${field('Optional caption','photo_caption','','text','maxlength="240" placeholder="e.g. Driver-side seat before cleaning"')}</div>${photoPicker('job')}<div class="photo-save-row"><span>Saved photos are private to your business.</span>${btn(icon('upload')+' Save photos','photos-save','primary',PhotoState.pending.length?'':'disabled')}</div><hr><div class="row between mb16"><h3>Job gallery</h3><span class="muted small">Before &middot; After &middot; Condition</span></div><div class="job-photo-grid">${data.photos.length?data.photos.map(p=>photoCard(p,true)).join(''):empty('No photos on this job yet','A quick before photo is a good place to start.','camera')}</div><p class="photo-footnote mt16">Images are optimized, not preserved originals. Keep originals separately for insurance, disputes or fine-damage evidence.</p>`);
}

async function customerPhotoPanel(raw) {
  const data=await api('/public/manage/'+raw+'/photos');
  PhotoState.customerPhotos=data.photos;
  const target=document.getElementById('customer-photos');if(!target)return;
  target.innerHTML=`<div class="row between"><h3>Your car photos</h3><span class="pill">${data.photos.length} / ${data.photo_limit}</span></div><p class="muted small mt8">Help the detailer prepare. Show areas that need attention. Only photos you submitted appear here; the business's own job notes stay private.</p><div class="job-photo-grid mt16">${data.photos.map(p=>photoCard(p,true)).join('')}</div>${data.can_upload?btn(icon('camera')+' Add condition photos','photos-customer-open','mt16','data-token="'+e(raw)+'"'):'<p class="muted small mt16">New photos can be added to confirmed bookings.</p>'}`;
}
async function openCustomerPhotos(raw) {
  if(PhotoState.uploadBusy)throw new Error('Wait until the photo save finishes.');
  if(PhotoState.pending.length&&!confirm('Discard the photos that have not been saved?'))return;
  clearPhotoPending();PhotoState.context={type:'customer',token:raw};
  openModal('Show us your car',`<p class="muted small mb16">Add condition photos for your confirmed appointment. Your detailer can see them; they are not public.</p>${field('Optional note','photo_caption','','text','maxlength="240" placeholder="e.g. Pet hair on the rear seats"')}${photoPicker('job')}<div class="photo-save-row"><span>Up to six photos per booking.</span>${btn(icon('upload')+' Save photos','photos-save','primary','disabled')}</div>`);
}
async function saveJobPhotos() {
  if(PhotoState.busy||PhotoState.uploadBusy)throw new Error('Finish the current photo operation first.');
  if(!PhotoState.pending.length)throw new Error('Take a photo or choose one from your gallery.');
  const context=PhotoState.context,body=new FormData();
  body.append('category',context.type==='customer'?'condition':$('#photo-category').value);
  body.append('caption',$('[name="photo_caption"]')?.value||'');body.append('profile',PhotoState.profile);
  body.append('upload_key',PhotoState.key||crypto.randomUUID());
  body.append('original_sizes',JSON.stringify(PhotoState.pending.map(p=>p.originalBytes)));
  for(const photo of PhotoState.pending)body.append('files',photo.file);
  PhotoState.uploadBusy=true;
  const status=$('#job-photo-status');if(status)status.textContent='Saving privately. Keep this page open...';
  try {
    const path=context.type==='customer'?'/public/manage/'+context.token+'/photos':'/bookings/'+context.id+'/photos';
    const result=await api(path,{method:'POST',body});clearPhotoPending();toast(result.message);
    PhotoState.uploadBusy=false;
    if(context.type==='customer'){closeModal();await customerPhotoPanel(context.token);}
    else {await openJobPhotos(context.id,true);if(location.pathname==='/app/photos')$('#main').innerHTML=await photosPage();}
  } catch(error) {
    if(status)status.textContent='Not confirmed saved. Your selected photos are still here. Retry to avoid duplicates.';
    throw error;
  } finally {PhotoState.uploadBusy=false;}
}

function findPhoto(id) {
  if(location.pathname.startsWith('/manage/'))return PhotoState.customerPhotos?.find(p=>p.id===id);
  return PhotoState.current.find(p=>p.id===id)||PhotoState.library.find(p=>p.id===id)||PhotoState.customerPhotos?.find(p=>p.id===id);
}
function viewPhoto(id) {
  if(PhotoState.pending.length)throw new Error('Save or remove your selected photos before opening another photo.');
  const p=findPhoto(id);if(!p)throw new Error('Refresh this gallery before opening the photo.');
  PhotoState.viewing=p;
  const customer=p.customer_view||p.url.includes('/public/manage/');
  openModal(photoLabel(p.category)+' photo',`<img class="photo-lightbox" src="${e(p.url)}" alt="${e(p.caption||photoLabel(p.category)+' vehicle photo')}"><div class="photo-detail-meta"><span>${p.width} &times; ${p.height} px</span><span>${photoBytes(p.size_bytes)} with thumbnail</span><span>${e(p.profile)} quality</span></div>${customer?`<p>${e(p.caption)}</p>`:`<form data-form="photo-edit" data-id="${e(p.id)}"><div class="form-grid"><div class="field"><label for="edit-photo-category">Label</label><select id="edit-photo-category" name="category">${['before','after','condition'].map(x=>`<option value="${x}" ${x===p.category?'selected':''}>${photoLabel(x)}</option>`).join('')}</select></div>${field('Caption','caption',p.caption,'text','maxlength="240"')}</div><div class="form-footer"><button class="btn" type="submit">Save details</button></div></form>`}<div class="modal-actions"><a class="btn" download="car-${e(p.category)}.webp" href="${e(p.url)}">${icon('download')} Download</a>${customer?'':btn('Delete photo','photo-delete','danger','data-id="'+e(p.id)+'"')}${customer?'':btn('Back to job','photos-open','','data-id="'+e(p.booking_id)+'"')}</div>`);
}
async function deleteJobPhoto(id) {
  if(!confirm('Delete this photo from active storage? Existing backups follow their retention schedule.'))return;
  const p=findPhoto(id);const result=await api('/job-photos/'+id,{method:'DELETE'});toast(result.message);
  if(location.pathname==='/app/photos')$('#main').innerHTML=await photosPage();
  await openJobPhotos(p.booking_id);
}
async function editJobPhoto(form) {
  const fd=new FormData(form),p=PhotoState.viewing;
  await api('/job-photos/'+form.dataset.id,{method:'PATCH',body:{category:fd.get('category'),caption:fd.get('caption')}});
  toast('Photo details saved.');if(location.pathname==='/app/photos')$('#main').innerHTML=await photosPage();
  await openJobPhotos(p.booking_id);
}

// Native file-picker change events keep mobile capture in a direct user gesture.
document.addEventListener('change',event=>{
  if(event.target.dataset.photoInput)handlePhotoSelection(event.target).catch(err=>toast(err.message,true));
  if(event.target.hasAttribute('data-photo-quality'))PhotoState.profile=event.target.value;
});
document.addEventListener('click',event=>{
  const remove=event.target.closest('[data-photo-remove]');if(!remove)return;
  event.preventDefault();if(PhotoState.busy||PhotoState.uploadBusy)return;
  const context=remove.dataset.photoContext,items=context==='quote'?S.wizard.photos:PhotoState.pending;
  const [removed]=items.splice(Number(remove.dataset.photoRemove),1);if(removed)URL.revokeObjectURL(removed.src);
  document.getElementById(context+'-photo-pending').innerHTML=pendingPhotoMarkup(items,context);
  const save=$('[data-action="photos-save"]');if(save)save.disabled=!items.length;
  const quality=$('#photo-quality');if(quality)quality.disabled=items.length>0;
  PhotoState.key=crypto.randomUUID();
});
let photoSearchTimer;
document.addEventListener('input',event=>{
  const input=event.target;
  if(input.id==='photo-job-search')$('#photo-job-options').innerHTML=photoJobOptions(S.bookings.filter(b=>`${b.customer_name} ${b.vehicle_notes}`.toLowerCase().includes(input.value.toLowerCase())));
  if(input.hasAttribute('data-photo-search')){PhotoState.search=input.value;clearTimeout(photoSearchTimer);photoSearchTimer=setTimeout(()=>{const pos=input.selectionStart;$('#main').innerHTML=renderPhotoLibrary();const next=$('[data-photo-search]');next.focus();if(next.type!=='search')next.setSelectionRange(pos,pos);},180);}
});
window.addEventListener('beforeunload',event=>{if(PhotoState.pending.length||PhotoState.uploadBusy||PhotoState.busy){event.preventDefault();event.returnValue='';}});
