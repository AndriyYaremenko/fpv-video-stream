// dashboard/public/views/dashboard.js — camera feeds + active detections + node telemetry strip.
import { el, pip, cornerCard, occupancyStrip, detectionCard, fmtBitrate, fmtUptime, tempSlot, escapeHtml } from '/views/components.js';
import { detectionKey } from '/alert.js';
import { frameCaption } from '/spectrum.js';

export function render(container, ctx) {
  container.className = 'screen';
  container.innerHTML = '';
  const dash = el('div', 'dash');

  // --- feeds ---
  const feeds = el('div', 'feeds');
  feeds.appendChild(el('div', 'label-caps', 'LIVE ФІДИ'));
  const grid = el('div', 'grid');
  for (const d of ctx.cameras()) {
    const tile = el('section', `tile${d.online?'':' offline'}`);
    tile.innerHTML = `<video autoplay playsinline muted></video>
      <div class="tile-overlay"><div class="tile-top">${pip(d.online)}
        <div class="tile-actions">
          <button class="tile-btn" data-act="restart" title="Перезапуск">🔄</button>
          <button class="tile-btn" data-act="creds" title="Креди">🔑</button>
          <button class="tile-btn" data-act="edit" title="Редагувати">✏️</button>
          <button class="tile-btn" data-act="del" title="Видалити">🗑</button></div></div>
        <div class="tile-meta"><strong>${escapeHtml(d.name)}</strong><small>${escapeHtml(d.location||'')}</small>
          <div class="tile-stats">${d.online?`${fmtBitrate(d.bitrateKbps)} · ${fmtUptime(d.uptimeSec)} · 👁 ${d.readers??0}`:''}</div></div></div>`;
    const video = tile.querySelector('video');
    tile.addEventListener('click', (e) => { if (!e.target.closest('[data-act]')) ctx.handlers.openVideo(d); });
    tile.querySelector('[data-act=restart]').addEventListener('click',(e)=>{e.stopPropagation();ctx.handlers.restartTile(d.id);});
    tile.querySelector('[data-act=creds]').addEventListener('click',(e)=>{e.stopPropagation();ctx.handlers.viewCreds(d.id);});
    tile.querySelector('[data-act=edit]').addEventListener('click',(e)=>{e.stopPropagation();ctx.handlers.openEditForm(d.id);});
    tile.querySelector('[data-act=del]').addEventListener('click',(e)=>{e.stopPropagation();ctx.handlers.deleteDevice(d.id,d.name);});
    grid.appendChild(tile);
    ctx.handlers.startTile(d, video);
  }
  if (!ctx.cameras().length) grid.appendChild(el('p','muted','Немає камер. Додай вузол.'));
  feeds.appendChild(grid);

  // --- active detections (threat logs) ---
  const threats = el('div', 'threats');
  threats.appendChild(el('div','label-caps','АКТИВНІ ДЕТЕКЦІЇ'));
  const store = ctx.scanStore(); const newKeys = ctx.newDetKeys();
  const dets = ctx.scanners().flatMap(s => (store[s.id]?.detection?.detections||[]).map(x=>({...x, _sid:s.id})))
    .sort((a,b)=>(b.power_dbm??-999)-(a.power_dbm??-999));
  if (!dets.length) threats.appendChild(el('p','muted','Немає активних передавачів.'));
  for (const d of dets) {
    const card = detectionCard(d, newKeys.has(detectionKey(d)));
    const v = store[d._sid]?.video;
    if (v && v.frame_png_b64) { card.style.cursor='pointer';
      card.addEventListener('click',()=>ctx.handlers.openImage(`data:image/png;base64,${v.frame_png_b64}`, frameCaption(v))); }
    threats.appendChild(card);
  }

  // --- node telemetry strip ---
  const strip = el('div','node-strip');
  for (const d of ctx.devices()) {
    const live = store[d.id];
    const isScanner = d.kind==='scanner';
    const online = isScanner ? !!(live&&live.online) : d.online;
    const card = cornerCard(`<div class="nc-head"><span class="nc-title">${escapeHtml(d.name)}</span>${pip(online)}</div>
      <div class="nc-grid">
        <div><span class="k">TEMP</span>${tempSlot(null)}</div>
        <div><span class="k">UPTIME</span><span class="mono">${fmtUptime(d.uptimeSec)}</span></div>
      </div>`);
    if (isScanner && live) card.appendChild(occupancyStrip(live.bands, live.detection?.occupancy||{}));
    strip.appendChild(card);
  }

  dash.appendChild(feeds); dash.appendChild(threats); dash.appendChild(strip);
  container.appendChild(dash);
}
