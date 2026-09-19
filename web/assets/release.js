/* Kontracts v4: explicit pricing, plans and account onboarding. No framework. */
'use strict';

function resolvedRate(packageItem, vehicle) {
  return packageItem.vehicle_rates?.[vehicle.id] || {
    price: packageItem.price + vehicle.price,
    minutes: packageItem.minutes + vehicle.minutes
  };
}

function pricingPage() {
  const s = S.me.shop.settings;
  return `${heading('Your prices. Clearly yours.',
    'These are the prices your customers pay you. Your Kontracts subscription is separate.',
    `<div class="row wrap">${nav('Subscription','/app/billing','btn')}${btn(icon('plus')+' Add package','package-edit','primary','data-index="-1"')}</div>`)}
    <div class="pricing-intro"><span class="badge-icon">${icon('dollar')}</span><div><strong>Individual pricing for ${e(S.me.shop.name)}</strong><p>Set exact USD prices for every service and vehicle. Changes apply to new bookings only.</p></div>${nav('Preview booking page '+icon('up'),S.me.shop.published?'/book/'+S.me.shop.slug:'/brand-preview','btn small')}</div>
    <div class="pricing-workspace"><section aria-label="Your service prices"><div class="service-grid rate-cards">
      ${s.packages.map((p,i)=>`<article class="card service-card">
        <div class="row between"><span class="pill ${p.active?'green':''}">${p.active?'Available':'Hidden'}</span>${btn(icon('edit')+' Details','package-edit','small',`data-index="${i}"`)}</div>
        <h2 class="mt16">${e(p.name)}</h2><p>${e(p.description)}</p>
        <div class="individual-rates">${s.vehicles.map(v=>{const r=resolvedRate(p,v);return `<div class="rate-row"><div><strong>${e(v.name)}</strong><small>${duration(r.minutes)} &middot; ${p.vehicle_rates?.[v.id]?'Individual price':'Default price'}</small></div><b>${money(r.price)}</b></div>`;}).join('')}</div>
        ${btn('Set individual prices '+icon('arrow'),'rate-edit','wide',`data-index="${i}"`)}
      </article>`).join('')}</div>
      <div class="card mt24"><div class="card-head"><div><h2>Add-ons</h2><p class="muted small">Optional extras, added once to the selected service.</p></div>${btn(icon('plus')+' Add extra','extra-edit','small','data-index="-1"')}</div>
      <div class="table-scroll"><table><thead><tr><th>Add-on</th><th>Price</th><th>Time</th><th>Visibility</th><th></th></tr></thead><tbody>${s.extras.map((x,i)=>`<tr><td><strong>${e(x.name)}</strong></td><td>${money(x.price)}</td><td>${x.minutes} min</td><td>${x.active?'Available':'Hidden'}</td><td>${btn('Edit','extra-edit','small',`data-index="${i}"`)}</td></tr>`).join('')||'<tr><td colspan="5">No add-ons yet.</td></tr>'}</tbody></table></div></div>
      <details class="card card-pad mt24"><summary><strong>Vehicle categories & default adjustments</strong></summary><p class="muted small mt16">Defaults are used only where you have not saved an individual service/vehicle price. They are never added twice.</p>
      ${s.vehicles.map((v,i)=>`<div class="rate-row"><div><strong>${e(v.name)}</strong><small>Default +${money(v.price)} &middot; +${v.minutes} min</small></div>${btn('Edit','vehicle-edit','small',`data-index="${i}"`)}</div>`).join('')}
      ${s.vehicles.length<8?btn(icon('plus')+' Add vehicle category','vehicle-edit','small mt16','data-index="-1"'):''}</details>
      <form data-form="price-rules" data-dirty class="card card-pad mt24"><h2>Deposit & tax</h2><p class="muted small mt8">Applied to this business only. Confirm your tax requirements before publishing.</p>
      <div class="form-grid mt16">${field('Deposit (%)','deposit_percent',s.deposit_percent,'number','required min="0" max="100" step="1" inputmode="numeric"')}${field('Tax (%)','tax_rate',(s.tax_basis_points/100).toFixed(2),'number','required min="0" max="20" step="0.01" inputmode="decimal"')}</div>
      <label class="check-row mt16"><input type="checkbox" name="prices_reviewed" ${s.prices_reviewed?'checked':''}><span>I have reviewed my service prices, tax and deposit settings.</span></label><div class="form-footer"><button class="btn primary" type="submit">Save deposit & tax</button></div></form></section>
      <aside class="card card-pad price-checker"><span class="eyebrow">Check before you share</span><h2 class="mt8">Try a customer quote</h2><p class="muted small mt8">Uses your saved prices and the same server calculation as the live booking page.</p>
      <form data-form="price-check" class="mt16"><div class="field"><label for="check-service">Service</label><select id="check-service" name="package_id">${s.packages.filter(x=>x.active).map(x=>`<option value="${e(x.id)}">${e(x.name)}</option>`).join('')}</select></div>
      <div class="field mt16"><label for="check-vehicle">Vehicle</label><select id="check-vehicle" name="vehicle_id">${s.vehicles.map(x=>`<option value="${e(x.id)}">${e(x.name)}</option>`).join('')}</select></div>
      ${s.extras.filter(x=>x.active).map(x=>`<label class="check-row mt16"><input type="checkbox" name="extra" value="${e(x.id)}"><span>${e(x.name)} <small class="muted">+${money(x.price)}</small></span></label>`).join('')}
      <button class="btn primary wide mt24" type="submit">Calculate customer price</button></form>
      <div id="price-check-result" class="mt24" aria-live="polite"><p class="muted small">Choose a service and vehicle to see the exact total, deposit and remaining balance.</p></div></aside></div>`;
}

function rateEditor(index) {
  const p=S.me.shop.settings.packages[index];
  openModal('Individual prices - '+p.name,`<form data-form="rate-edit" data-index="${index}" data-dirty>
    <p class="muted small">Each price is the complete service price for that vehicle, before add-ons and tax. The default vehicle adjustment is not added again.</p>
    <div class="rate-edit-rows">${S.me.shop.settings.vehicles.map(v=>{const r=resolvedRate(p,v),custom=!!p.vehicle_rates?.[v.id];return `<fieldset class="rate-edit-row"><legend>${e(v.name)}</legend><div class="form-grid">
    ${field('Price (USD)','rate-'+v.id,(r.price/100).toFixed(2),'number',`required min="1" max="15000" step="0.01" inputmode="decimal" data-rate-vehicle="${e(v.id)}"`)}
    ${field('Service time (minutes)','time-'+v.id,r.minutes,'number',`required min="30" max="720" step="1" inputmode="numeric" data-rate-vehicle="${e(v.id)}"`)}</div>
    <label class="check-row mt8"><input type="checkbox" name="custom-${e(v.id)}" ${custom?'checked':''}><span>Use this individual price and time</span></label><small class="muted">Unchecked: use the package base + vehicle defaults.</small></fieldset>`;}).join('')}</div>
    <div class="form-footer"><button class="btn primary" type="submit">Save individual prices</button></div></form>`);
}

function subscriptionCards(){return singlePlanCard(false);}
function plansPage(){return minimalBilling();}

function googleButton() {
  const ready=S.config?.google_enabled;
  return `<button type="button" class="google-button wide" data-action="google-start" ${ready?'':'disabled'}>
    <svg viewBox="0 0 48 48" width="20" height="20" aria-hidden="true"><path fill="#4285F4" d="M43.6 24.5c0-1.5-.1-3-.4-4.5H24v8.5h11a9.4 9.4 0 0 1-4.1 6.2v5.2h6.7c3.9-3.6 6-8.9 6-15.4z"/><path fill="#34A853" d="M24 44c5.5 0 10.1-1.8 13.5-5l-6.7-5.2c-1.8 1.2-4.1 1.9-6.8 1.9-5.3 0-9.9-3.6-11.5-8.4H5.6v5.4A20.4 20.4 0 0 0 24 44z"/><path fill="#FBBC05" d="M12.5 27.3a12.3 12.3 0 0 1 0-7.1v-5.4H5.6a20 20 0 0 0 0 17.9z"/><path fill="#EA4335" d="M24 12.3c3 0 5.6 1 7.7 3l5.8-5.8A19.4 19.4 0 0 0 24 4 20.4 20.4 0 0 0 5.6 15.1l6.9 5.4C14.1 15.6 18.7 12.3 24 12.3z"/></svg>
    <span>Continue with Google</span></button>${ready?'':'<p class="muted small mt8">Google sign-in needs operator setup. You can use email and password below.</p>'}<div class="auth-divider"><span>or continue with email</span></div>`;
}

function googleNotice() {
  const notices={
    'google-cancelled':'Google sign-in was cancelled. Try again or sign in with your password.',
    'google-failed':'Google could not complete sign-in. Try again, or use email and password.',
    'google-link-required':'An account already uses that email. Sign in with your password, then connect Google in Settings. This protects your existing account.',
    'google-linked':'Google is now connected to your account.'
  };
  const text=notices[new URLSearchParams(location.search).get('notice')];
  return text?`<div class="notice mb16" role="status">${e(text)}</div>`:'';
}

async function googleCompletePage() {
  const p=await api('/auth/google/pending');
  return `<div class="auth-page"><aside class="auth-brand-panel">${brand()}<div><h1>One last step.<br>Your own booking page.</h1><p>Google handles sign-in. You stay in control of your business.</p></div></aside><main id="main" class="auth-content"><div class="auth-form"><h1>Welcome, ${e(p.name.split(' ')[0])}.</h1><p>Signed in with ${e(p.email)}. Let's create your business workspace.</p>
    <form data-form="google-complete">${field('Business name','business','','text','required minlength="2" maxlength="80" autocomplete="organization"')}${field('Booking page name','slug','','text','required minlength="3" maxlength="45" pattern="[a-z0-9]+(-[a-z0-9]+)*" placeholder="e.g. mikes-detailing"')}
    <div class="field mt16"><label for="google-zone">Business timezone</label><select id="google-zone" name="timezone">${zoneOptions('America/New_York')}</select></div>
    <div class="field mt16"><label for="google-plan">Plan after your trial</label><select id="google-plan" name="plan_id">${(S.config.plans||[]).map(x=>`<option value="${x.id}" ${x.id===p.plan_id?'selected':''}>${e(x.name)} - ${money(x.amount)}/month</option>`).join('')}</select></div>
    <label class="check-row mt24"><input type="checkbox" name="accepted_terms" required><span>I agree to the ${nav('Terms','/terms','link-button')} and ${nav('Privacy Policy','/privacy','link-button')}.</span></label>
    <p class="muted small mt16">14 days to try it. No card and no automatic charge.</p><button class="btn primary wide" type="submit">Create my workspace ${icon('arrow')}</button></form></div></main></div>`;
}

function googleAccountPanel() {
  const u=S.me.user;
  return `<section class="card card-pad mt24"><h2>Sign-in & account access</h2><p class="muted small mt8">${u.google_linked?'Google is connected to '+e(u.google_email||u.email)+'.':'Connect your Google account for a simpler sign-in.'}</p>
    ${u.google_linked?(u.local_login_enabled?btn('Disconnect Google','google-unlink','mt16'): '<p class="small mt16">Google is your sign-in method. Set a password through the reset email before disconnecting or deleting your account.</p>'):
      btn('Connect Google','google-link','mt16',S.config.google_enabled?'':'disabled')}
    ${!u.local_login_enabled?btn('Email me a password setup link','google-set-password','mt16'):''}
    ${!S.config.google_enabled?'<p class="muted small mt8">The platform operator has not connected Google yet.</p>':''}</section>`;
}

async function setupPage() {
  const data=await api('/setup');
  return `${heading('A few steps. Then you are ready.','Your booking page stays private until you publish it.',nav('Preview booking page',S.me.shop.published?'/book/'+S.me.shop.slug:'/brand-preview','btn'))}
    <div class="card card-pad"><h2>Your launch checklist</h2>${data.checks.map(x=>`<div class="setup-row"><span class="badge-icon ${x.ready?'':'pending-icon'}">${icon(x.ready?'check':'arrow')}</span><div class="grow"><strong>${e(x.name)}</strong><p class="muted small">${x.ready?'Completed':'Needs your attention'}</p></div>${nav(x.ready?'Review':'Set up',x.url,'btn small')}</div>`).join('')}</div>
    <details class="card card-pad mt24"><summary><strong>Connection status</strong></summary><p class="muted small mt16">${e(data.note)}</p><div class="mt16">${Object.entries(data.platform).filter(([k])=>k.endsWith('_configured')&&k!=='google_configured'&&(k!=='square_configured'||S.me.shop.settings.payment_method==='square')).map(([k,v])=>`<div class="rate-row"><strong>${e(k.replace('_configured','').replace('google','Google').replace('square','Square'))}</strong><span class="pill ${v?'green':'red'}">${v?'Configured':'Setup required'}</span></div>`).join('')}</div></details>`;
}

async function releaseAction(name,node) {
  switch(name) {
    case 'rate-edit': rateEditor(Number(node.dataset.index));return true;
    case 'plan-select':window.location.assign((await api(S.me.shop.has_subscription?'/billing/portal':'/billing/checkout',{method:'POST',body:S.me.shop.has_subscription?undefined:{plan_id:'solo'}})).url);return true;
    case 'google-start':window.location.assign((await api('/auth/google/start',{method:'POST',body:{plan_id:'solo'}})).url);return true;
    case 'google-link':case 'google-unlink':openModal(name==='google-link'?'Connect your Google account':'Disconnect Google',`<form data-form="${name}"><p class="muted small mb16">Confirm your password to protect your account.${name==='google-link'?' Choose the Google account with the same email as your Kontracts account.':''}</p>${field('Current password','password','','password','required autocomplete="current-password"')}<div class="form-footer"><button class="btn primary" type="submit">${name==='google-link'?'Continue to Google':'Disconnect Google'}</button></div></form>`);return true;
    case 'google-set-password':toast((await api('/auth/forgot-password',{method:'POST',body:{email:S.me.user.email}})).message);return true;
  }
  return false;
}

async function releaseSubmit(form) {
  const fd=new FormData(form),data=Object.fromEntries(fd),type=form.dataset.form;
  if(type==='rate-edit') {
    const s=structuredClone(S.me.shop.settings),p=s.packages[Number(form.dataset.index)];p.vehicle_rates={};
    for(const v of s.vehicles)if(fd.has('custom-'+v.id))p.vehicle_rates[v.id]={price:cents(data['rate-'+v.id]),minutes:Number(data['time-'+v.id])};
    await persistSettings(s);closeModal();await renderPage();return true;
  }
  if(type==='price-rules') {
    const s=structuredClone(S.me.shop.settings);s.deposit_percent=Number(data.deposit_percent);s.tax_basis_points=cents(data.tax_rate);s.prices_reviewed=fd.has('prices_reviewed');
    await persistSettings(s);await renderPage();return true;
  }
  if(type==='price-check') {
    const r=await api('/preview/price',{method:'POST',body:{package_id:data.package_id,vehicle_id:data.vehicle_id,extra_ids:fd.getAll('extra'),zip:S.me.shop.settings.zip_codes[0],condition:'standard'}});
    $('#price-check-result').innerHTML=`<div class="money-lines">${r.lines.map(x=>`<div><span>${e(x.name)}</span><strong>${money(x.amount)}</strong></div>`).join('')}<div><span>Tax</span><strong>${money(r.tax)}</strong></div><div class="total"><span>Customer total</span><strong>${money(r.total)}</strong></div></div><div class="deposit-box"><span>Deposit now</span><strong>${money(r.deposit)}</strong></div><div class="rate-row"><span>Remaining balance</span><strong>${money(r.total-r.deposit)}</strong></div><p class="muted small">${duration(r.minutes)} service + ${r.buffer_minutes} min travel buffer.</p>`;return true;
  }
  if(type==='google-complete') {
    data.accepted_terms=fd.has('accepted_terms');const r=await api('/auth/google/complete',{method:'POST',body:data});S.me={csrf:r.csrf};go(r.redirect);return true;
  }
  if(type==='google-link'||type==='google-unlink') {
    const r=await api('/auth/google/'+(type==='google-link'?'link':'unlink'),{method:'POST',body:{password:data.password}});
    if(r.url)window.location.assign(r.url);else{closeModal();toast(r.message);await refreshMe();await renderPage();}return true;
  }
  return false;
}

document.addEventListener('input',event=>{
  const v=event.target.dataset.rateVehicle;
  if(v){const checkbox=event.target.form?.elements['custom-'+v];if(checkbox)checkbox.checked=true;}
});
