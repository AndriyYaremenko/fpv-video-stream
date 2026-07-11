// dashboard/public/views/detections.js — «Детекції» screen: detection journal history table
// (cached; no refetch on data ticks — only on mount or via the «оновити» button). v2 dropped the
// v1 logs.js side panel (live spectrum + recovered frames now live on the FPV Viewer / Frames screens).
import { el, escapeHtml } from '/views/components.js';
import { classColor, fmtFreq } from '/spectrum.js';

let historyCache = null; // fetched once (on mount or via the refresh button), not on every tick

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
  container.className='screen screen-pad';
  container.innerHTML='';

  const head=el('div',null,'<span class="label-caps">ІСТОРІЯ ДЕТЕКЦІЙ</span> <button type="button" class="btn" id="detections-refresh" style="padding:3px 9px;font-size:11px;">оновити</button>');
  container.appendChild(head);
  const tableSlot=el('div',null, historyCache ? '' : '<p class="muted">Завантаження…</p>');
  if (historyCache) tableSlot.appendChild(historyTable(historyCache));
  container.appendChild(tableSlot);
  head.querySelector('#detections-refresh').addEventListener('click', () => {
    historyCache = null; ctx.getDetections().then(rows => { historyCache = rows; ctx.requestRender(); });
  });

  if (!historyCache) ctx.getDetections().then(rows => { historyCache = rows; tableSlot.innerHTML=''; tableSlot.appendChild(historyTable(rows)); });
}
