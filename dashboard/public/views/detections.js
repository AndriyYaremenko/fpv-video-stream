// dashboard/public/views/detections.js — «Детекції» screen: detection journal history table
// (cached; no refetch on data ticks — only on mount or via the «оновити» button). v2 dropped the
// v1 logs.js side panel (live spectrum + recovered frames now live on the FPV Viewer / Frames screens).
import { el, escapeHtml } from '/views/components.js';
import { classColor, fmtFreq } from '/spectrum.js';
import { scannerThresholdCards, THRESHOLD_FIELDS } from '/thresholds.js';

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

  // Per-scanner detection-sensitivity panel (non-live screen: a plain build is safe here — inputs
  // aren't tick-churned since detections only re-renders on mount/refresh, not on data ticks).
  const cards = scannerThresholdCards(ctx.scanStore());
  if (cards.length) {
    const wrap = el('div', 'thresholds-wrap');
    for (const c of cards) {
      const panel = el('div', 'thresholds-panel');
      const inputs = THRESHOLD_FIELDS.map((f) => {
        const cur = c.scancfg[f.key];
        return `<label class="th-field"><span>${escapeHtml(f.label)}</span>`
          + `<input class="th-in" data-key="${f.key}" type="number" min="${f.lo}" max="${f.hi}" step="${f.step}"`
          + ` value="${cur == null ? '' : cur}" /></label>`;
      }).join('');
      panel.innerHTML = `<div class="th-head mono">${escapeHtml(c.id)} · чутливість</div>`
        + `<div class="th-fields">${inputs}</div>`
        + `<div class="th-actions"><button type="button" class="btn th-apply">Застосувати</button>`
        + `<button type="button" class="btn th-reset">Скинути</button></div>`;
      panel.querySelector('.th-apply').addEventListener('click', () => {
        const obj = {};
        panel.querySelectorAll('.th-in').forEach((i) => { obj[i.dataset.key] = i.value; });
        ctx.handlers.onThresholds(c.id, obj);
      });
      panel.querySelector('.th-reset').addEventListener('click', () => ctx.handlers.onThresholds(c.id, 'reset'));
      wrap.appendChild(panel);
    }
    container.appendChild(wrap);
  }

  const head=el('div',null,'<span class="label-caps">ІСТОРІЯ ДЕТЕКЦІЙ</span> <button type="button" class="btn" id="detections-refresh" style="padding:3px 9px;font-size:11px;">оновити</button>');
  container.appendChild(head);
  const tableSlot=el('div','table-scroll', historyCache ? '' : '<p class="muted">Завантаження…</p>');
  if (historyCache) tableSlot.appendChild(historyTable(historyCache));
  container.appendChild(tableSlot);
  head.querySelector('#detections-refresh').addEventListener('click', () => {
    historyCache = null; ctx.getDetections().then(rows => { historyCache = rows; ctx.requestRender(); });
  });

  if (!historyCache) ctx.getDetections().then(rows => { historyCache = rows; tableSlot.innerHTML=''; tableSlot.appendChild(historyTable(rows)); });
}
