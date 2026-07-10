// dashboard/public/modals.js — fullscreen viewer + form/creds modals + device CRUD.
import { startWhep } from '/whep.js';
import { escapeHtml } from '/views/components.js';

export function createModals(ctx) {
  const modal = document.getElementById('modal');
  const mVideo = document.getElementById('modal-video');
  const mImage = document.getElementById('modal-image');
  const mCap = document.getElementById('modal-caption');
  const formModal = document.getElementById('form-modal');
  const formBody = document.getElementById('form-modal-body');
  let modalPlayer = null;

  function showForm(html) { formBody.innerHTML = html; formModal.classList.remove('hidden'); }
  function hideForm() { formModal.classList.add('hidden'); formBody.innerHTML = ''; }
  formModal.addEventListener('click', (e) => {
    if (e.target === formModal || e.target.hasAttribute('data-close')) hideForm();
    const copyBtn = e.target.closest('.copy');
    if (copyBtn) { const pre = copyBtn.closest('.cred-row').querySelector('pre'); copyText(pre.textContent, copyBtn); }
  });
  async function copyText(text, btn) {
    try { await navigator.clipboard.writeText(text); }
    catch { const ta=document.createElement('textarea'); ta.value=text; ta.style.position='fixed'; ta.style.opacity='0';
      document.body.appendChild(ta); ta.select(); try{document.execCommand('copy');}catch{} document.body.removeChild(ta); }
    if (btn){const t=btn.textContent;btn.textContent='скопійовано ✓';setTimeout(()=>btn.textContent=t,1200);}
  }

  function openVideo(d) {
    mImage.classList.add('hidden'); mVideo.classList.remove('hidden');
    if (modalPlayer){modalPlayer.close();modalPlayer=null;}
    mCap.textContent = `${d.name} — ${d.location||''}`;
    modal.classList.remove('hidden');
    if (d.online && !ctx.isPreview) startWhep(mVideo, `${ctx.cfg.webrtcBase}/${d.id}/whep`, ctx.cfg.readUser, ctx.cfg.readPass).then(p=>modalPlayer=p).catch(()=>{});
    document.getElementById('modal-close').onclick = () => { if(modalPlayer){modalPlayer.close();modalPlayer=null;} modal.classList.add('hidden'); };
  }
  function openImage(src, caption) {
    if (modalPlayer){modalPlayer.close();modalPlayer=null;}
    mVideo.classList.add('hidden'); mImage.src=src; mImage.classList.remove('hidden');
    mCap.textContent = caption||''; modal.classList.remove('hidden');
    document.getElementById('modal-close').onclick = () => { mImage.classList.add('hidden'); mImage.src=''; mVideo.classList.remove('hidden'); modal.classList.add('hidden'); };
  }

  function credRow(label,value){return `<div class="cred-row"><div class="cred-label"><span>${label}</span><button type="button" class="copy">копіювати</button></div><pre>${escapeHtml(value)}</pre></div>`;}
  function showCreds(device,push,isNew){showForm(`<h2>${isNew?'✅ Вузол створено':'🔑 Креди вузла'}: ${escapeHtml(device.id)}</h2><p class="muted">${escapeHtml(device.name||'')}${device.location?` · ${escapeHtml(device.location)}`:''}</p>${credRow('Publish пароль',device.publish_pass)}${credRow('Команда пушу — RTSP',push.rtsp)}${credRow('Команда пушу — SRT',push.srt)}<p class="muted small">Налаштуй WireGuard на Pi вручну, потім встав цю команду пушу.</p><div class="form-actions"><button type="button" data-close class="btn btn-primary">Готово</button></div>`);}
  function scannerInfoModal(device,isNew){showForm(`<h2>${isNew?'✅ Сканер створено':'📡 Сканер'}: ${escapeHtml(device.id)}</h2><p class="muted">${escapeHtml(device.name||'')}${device.location?` · ${escapeHtml(device.location)}`:''}</p><p class="muted small">Вузол-сканер — відео не публікує. Дані йдуть у MQTT.</p>${credRow('SCAN_ID на Pi',device.id)}${credRow('MQTT-топіки',`fpv/${device.id}/{spectrum,detection,status,video}`)}<div class="form-actions"><button type="button" data-close class="btn btn-primary">Готово</button></div>`);}

  function openAddForm(){showForm(`<h2>Додати вузол</h2><form id="add-form" class="form"><label>Device ID <small>(порожньо = автоген)</small><input name="id" autocomplete="off"/></label><label>Тип<select name="kind"><option value="camera">Камера</option><option value="scanner">Сканер (HackRF)</option></select></label><label>Назва<input name="name" required/></label><label>Локація<input name="location"/></label><p class="form-err" id="add-err"></p><div class="form-actions"><button type="button" data-close class="btn btn-ghost">Скасувати</button><button type="submit" class="btn btn-primary">Створити</button></div></form>`);
    document.getElementById('add-form').addEventListener('submit', submitAdd);}
  async function submitAdd(e){e.preventDefault();const fd=new FormData(e.target);const payload={id:(fd.get('id')||'').trim(),name:(fd.get('name')||'').trim(),location:(fd.get('location')||'').trim(),kind:fd.get('kind')||'camera'};const errEl=document.getElementById('add-err');errEl.textContent='';const res=await fetch('/api/devices',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const body=await res.json().catch(()=>({}));if(!res.ok){errEl.textContent=body.error||`Помилка ${res.status}`;return;}if(body.scanner)scannerInfoModal(body.device,true);else showCreds(body.device,body.push,true);}
  function openEditForm(id){const d=ctx.devices().find(x=>x.id===id)||{id,name:'',location:''};showForm(`<h2>Редагувати: ${escapeHtml(id)}</h2><form id="edit-form" class="form"><label>Назва<input name="name" value="${escapeHtml(d.name||'')}" required/></label><label>Локація<input name="location" value="${escapeHtml(d.location||'')}"/></label><p class="muted small">ID та пароль не змінюються.</p><p class="form-err" id="edit-err"></p><div class="form-actions"><button type="button" data-close class="btn btn-ghost">Скасувати</button><button type="submit" class="btn btn-primary">Зберегти</button></div></form>`);
    document.getElementById('edit-form').addEventListener('submit',(e)=>submitEdit(e,id));}
  async function submitEdit(e,id){e.preventDefault();const fd=new FormData(e.target);const payload={name:(fd.get('name')||'').trim(),location:(fd.get('location')||'').trim()};const errEl=document.getElementById('edit-err');errEl.textContent='';const res=await fetch(`/api/devices/${encodeURIComponent(id)}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});if(!res.ok){const b=await res.json().catch(()=>({}));errEl.textContent=b.error||`Помилка ${res.status}`;return;}hideForm();ctx.requestRender();}
  async function viewCreds(id){const res=await fetch(`/api/devices/${encodeURIComponent(id)}/push`);if(!res.ok){alert('Не вдалося отримати креди');return;}const body=await res.json();showCreds(body.device,body.push,false);}
  function scannerInfo(id){const d=ctx.devices().find(x=>x.id===id)||{id,name:id,location:''};scannerInfoModal(d,false);}
  async function deleteDevice(id,name){if(!confirm(`Видалити вузол «${name||id}»?`))return;const res=await fetch(`/api/devices/${encodeURIComponent(id)}`,{method:'DELETE'});if(!res.ok){alert('Помилка видалення');return;}ctx.requestRender();}

  return { openVideo, openImage, openAddForm, openEditForm, viewCreds, scannerInfo, deleteDevice, hideForm };
}
