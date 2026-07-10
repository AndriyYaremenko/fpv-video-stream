// dashboard/public/views/logs.js — detection history (cached; no refetch on data ticks) +
// live spectrum (selected band persisted across re-renders) + recovered frames.
import { el, escapeHtml } from '/views/components.js';
import { classColor, fmtFreq, renderMiniSpectrum, frameCaption } from '/spectrum.js';
import { nearestRxChannel } from '/rx5808-channels.js';

let selectedBand = null;   // persisted across re-renders so data ticks don't reset the picker
let historyCache = null;   // fetched once (on mount or via the refresh button), not on every tick

function historyTable(rows){
  if (!rows.length) return el('p','muted','Журнал порожній.');
  const t = el('table','data-table','<thead><tr><th>Час</th><th>Сканер</th><th>Бенд</th><th>Частота</th><th>Клас</th><th>SNR</th><th>Подія</th></tr></thead>');
  const tb = el('tbody');
  for (const e of rows){
    const d=new Date(Number(e.ts)*1000); const p=n=>String(n).padStart(2,'0');
    const when=`${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
    const freq=`${fmtFreq(e.center_mhz)}${e.channel?` (${escapeHtml(e.channel)})`:''}`;
    const ev=e.event==='gone'?'<span style="color:var(--muted)">зник</span>':'<span style="color:var(--on)">з\'явився</span>';
    tb.appendChild(el('tr',null,`<td>${when}</td><td>${escapeHtml(e.scanner_id||'')}</td><td>${escapeHtml(e.band||'')}</td><td>${freq}</td><td style="color:${classColor(e.class)}">${escapeHtml(e.class||'')}</td><td>${e.snr_db==null?'—':escapeHtml(String(e.snr_db))} dB</td><td>${ev}</td>`));
  }
  t.appendChild(tb); return t;
}

export function render(container, ctx){
  container.className='screen';
  container.innerHTML='';
  const layout=el('div','logs');

  const main=el('div','logs-main');
  const head=el('div',null,'<span class="label-caps">ІСТОРІЯ ДЕТЕКЦІЙ</span> <button type="button" class="btn" id="logs-refresh" style="padding:3px 9px;font-size:11px;">оновити</button>');
  main.appendChild(head);
  const tableSlot=el('div',null, historyCache ? '' : '<p class="muted">Завантаження…</p>');
  if (historyCache) tableSlot.appendChild(historyTable(historyCache));
  main.appendChild(tableSlot);
  head.querySelector('#logs-refresh').addEventListener('click', () => {
    historyCache = null; ctx.getDetections().then(rows => { historyCache = rows; ctx.requestRender(); });
  });

  const side=el('div','logs-side');
  const scanners=ctx.scanners(); const store=ctx.scanStore();
  const sid=scanners[0]?.id; const live=sid?store[sid]:null;
  const bands=live?Object.keys(live.bands||{}):[];
  side.appendChild(el('div','label-caps','LIVE SPECTRUM'));
  if (live && bands.length){
    if (!bands.includes(selectedBand)) selectedBand = bands.find(b=>b==='5.8G')||bands[0];
    const picker=el('div','rx5808-ctl');
    for (const b of bands){
      const btn=el('button',`rx-mode${b===selectedBand?' active':''}`,b);
      btn.addEventListener('click',()=>{ selectedBand=b; draw(); for(const x of picker.children)x.classList.toggle('active',x.textContent===b); });
      picker.appendChild(btn);
    }
    side.appendChild(picker);
    const canvas=document.createElement('canvas'); canvas.width=300; canvas.height=60; canvas.className='mini-spectrum'; side.appendChild(canvas);
    function draw(){ const range=live.bands[selectedBand]||{}; const psd=(live.latestPsd&&live.latestPsd[selectedBand])||[];
      const dets=(live.detection?.detections||[]).filter(d=>d.band===selectedBand); const rxFreq=live.rxtune?.freq_mhz??null;
      renderMiniSpectrum(canvas,{psd,range,dets,rxFreq,tunable:range.low_mhz!=null}); }
    canvas.addEventListener('click',(e)=>{ const range=live.bands[selectedBand]||{}; if(range.low_mhz==null)return;
      const r=canvas.getBoundingClientRect(); const x=Math.min(r.width,Math.max(0,e.clientX-r.left));
      const freq=range.low_mhz+(x/r.width)*(range.high_mhz-range.low_mhz); const ch=nearestRxChannel(freq);
      if(ch) ctx.onScanClick(sid,{mode:'manual',channel:ch.name}); });
    draw();
  } else side.appendChild(el('p','muted','Немає активного сканера.'));

  side.appendChild(el('div','label-caps','ВІДНОВЛЕНІ КАДРИ'));
  const frames=el('div','frames-grid'); let any=false;
  for (const s of scanners){ const v=store[s.id]?.video; if (v&&v.frame_png_b64){ any=true;
    const img=document.createElement('img'); img.src=`data:image/png;base64,${v.frame_png_b64}`; img.alt='frame';
    img.addEventListener('click',()=>ctx.handlers.openImage(img.src,frameCaption(v))); frames.appendChild(img);} }
  if (!any) frames.appendChild(el('p','muted','Кадрів ще немає.'));
  side.appendChild(frames);

  layout.appendChild(main); layout.appendChild(side); container.appendChild(layout);

  if (!historyCache) ctx.getDetections().then(rows => { historyCache = rows; tableSlot.innerHTML=''; tableSlot.appendChild(historyTable(rows)); });
}
