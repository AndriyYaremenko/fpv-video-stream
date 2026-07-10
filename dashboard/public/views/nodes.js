// dashboard/public/views/nodes.js — node management: all devices as cards, CRUD, RX5808 controls.
import { el, pip, occupancyStrip, fmtUptime, fmtBitrate, tempSlot, telemetryLine, escapeHtml } from '/views/components.js';
import { RX5808_CHANNELS } from '/rx5808-channels.js';

function rx5808Controls(scannerId, activeMode, ctx) {
  const row = el('div', 'rx5808-ctl');
  for (const m of ['auto','scan','random','manual']) {
    const b = el('button', `rx-mode${m===activeMode?' active':''}`, m);
    b.addEventListener('click', () => ctx.onScanClick(scannerId, { mode:m }));
    row.appendChild(b);
  }
  const sel = el('select', 'rx5808-ch');
  for (const ch of RX5808_CHANNELS){ const o=document.createElement('option'); o.value=ch.name; o.textContent=`${ch.name} · ${ch.freq}`; sel.appendChild(o); }
  sel.addEventListener('change', () => ctx.onScanClick(scannerId, { mode:'manual', channel: sel.value }));
  row.appendChild(sel);
  return row;
}

export function render(container, ctx) {
  container.className = 'screen screen-pad';
  container.innerHTML = '';
  container.appendChild(el('div','label-caps','КЕРУВАННЯ ВУЗЛАМИ'));
  const grid = el('div','node-strip');
  const store = ctx.scanStore();
  for (const d of ctx.devices()) {
    const isScanner = d.kind==='scanner';
    const live = store[d.id];
    const online = isScanner ? !!(live&&live.online) : d.online;
    const card = el('div','node-card');
    card.innerHTML = `<div class="nc-head"><div><div class="nc-title">${escapeHtml(d.name)}</div>
        <div class="nc-sub">${escapeHtml(d.id)} · ${isScanner?'SCANNER':'CAMERA'}</div></div>${pip(online)}</div>
      <div class="nc-grid">
        <div><span class="k">TEMP</span>${tempSlot(null)}</div>
        <div><span class="k">UPTIME</span><span class="mono">${fmtUptime(d.uptimeSec)}</span></div>
        <div><span class="k">${isScanner?'ЛОКАЦІЯ':'BITRATE'}</span><span class="mono">${isScanner?escapeHtml(d.location||'—'):fmtBitrate(d.bitrateKbps)}</span></div>
        <div><span class="k">${isScanner?'ДЕТЕКЦІЙ':'TELEMETRY'}</span><span class="mono">${isScanner?((live?.detection?.detections?.length)||0):escapeHtml(telemetryLine(d.telemetry)||'—')}</span></div>
      </div>`;
    if (isScanner && live) card.appendChild(occupancyStrip(live.bands, live.detection?.occupancy||{}));
    if (isScanner) card.appendChild(rx5808Controls(d.id, live?.rxtune?.mode||null, ctx));
    const actions = el('div','nc-actions',
      `<button class="btn" data-act="edit">✏️ Редагувати</button>
       <button class="btn" data-act="${isScanner?'info':'creds'}">🔑 ${isScanner?'Інфо':'Креди'}</button>
       ${isScanner?'':'<button class="btn" data-act="restart">🔄 Перезапуск</button>'}
       <button class="btn" data-act="del">🗑 Видалити</button>`);
    actions.querySelector('[data-act=edit]').addEventListener('click',()=>ctx.handlers.openEditForm(d.id));
    actions.querySelector('[data-act=del]').addEventListener('click',()=>ctx.handlers.deleteDevice(d.id,d.name));
    const infoBtn = actions.querySelector('[data-act=info]'); if(infoBtn) infoBtn.addEventListener('click',()=>ctx.handlers.scannerInfo(d.id));
    const credBtn = actions.querySelector('[data-act=creds]'); if(credBtn) credBtn.addEventListener('click',()=>ctx.handlers.viewCreds(d.id));
    const reBtn = actions.querySelector('[data-act=restart]'); if(reBtn) reBtn.addEventListener('click',()=>ctx.handlers.restartTile(d.id));
    card.appendChild(actions);
    grid.appendChild(card);
  }
  container.appendChild(grid);
}
