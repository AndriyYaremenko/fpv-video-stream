// dashboard/public/views/dashboard.js — camera feeds (reused tiles + persistent players),
// a compact active-detections summary, and node telemetry strip. render() is reconcile-based: it
// never tears down a live <video>, so it is safe to call on every SSE/MQTT tick.
// v2 note: the FULL merged detection list now lives on the separate FPV Viewer screen
// (views/viewer.js); this screen only shows a lightweight count + top few, so it stays scannable
// alongside the feeds grid. d.telemetry is a dead field (never populated upstream) — not rendered.
import { el, pip, cornerCard, occupancyStrip, detectionCard, fmtBitrate, fmtUptime, tempSlot, escapeHtml } from '/views/components.js';
import { detectionKey } from '/alert.js';
import { frameCaption } from '/spectrum.js';

const TOP_DETECTIONS = 3; // how many detection cards to show in the compact summary

// Build a camera tile once (video + overlay + listeners). Reused across renders to keep its player alive.
function buildTile(d, ctx){
  const tile = el('section', 'tile');
  tile.id = `tile-${d.id}`;
  tile.innerHTML = `<video autoplay playsinline muted></video>
    <div class="tile-overlay"><div class="tile-top"><span class="tile-badge"></span>
      <div class="tile-actions">
        <button class="tile-btn" data-act="restart" title="Перезапуск">🔄</button>
        <button class="tile-btn" data-act="creds" title="Креди">🔑</button>
        <button class="tile-btn" data-act="edit" title="Редагувати">✏️</button>
        <button class="tile-btn" data-act="del" title="Видалити">🗑</button></div></div>
      <div class="tile-meta"><strong></strong><small></small><div class="tile-stats"></div></div></div>`;
  tile.addEventListener('click', (e) => { if (!e.target.closest('[data-act]') && tile.__d) ctx.handlers.openVideo(tile.__d); });
  tile.querySelector('[data-act=restart]').addEventListener('click',(e)=>{ e.stopPropagation(); ctx.handlers.restartTile(d.id); });
  tile.querySelector('[data-act=creds]').addEventListener('click',(e)=>{ e.stopPropagation(); ctx.handlers.viewCreds(d.id); });
  tile.querySelector('[data-act=edit]').addEventListener('click',(e)=>{ e.stopPropagation(); ctx.handlers.openEditForm(d.id); });
  tile.querySelector('[data-act=del]').addEventListener('click',(e)=>{ e.stopPropagation(); ctx.handlers.deleteDevice(d.id, (tile.__d||d).name); });
  return tile;
}

export function render(container, ctx){
  container.className = 'screen';
  // Build the .dash skeleton once; reuse it (and its live <video> tiles) on subsequent renders.
  let dash = container.querySelector('.dash');
  if (!dash){
    container.innerHTML = '';
    dash = el('div','dash');
    const feeds = el('div','feeds'); feeds.appendChild(el('div','label-caps','LIVE ФІДИ')); feeds.appendChild(el('div','grid'));
    dash.appendChild(feeds);
    dash.appendChild(el('div','threats'));
    dash.appendChild(el('div','node-strip'));
    container.appendChild(dash);
  }
  const grid = dash.querySelector('.feeds .grid');
  const threats = dash.querySelector('.threats');
  const strip = dash.querySelector('.node-strip');
  const store = ctx.scanStore();

  // --- feeds: reconcile tiles by id (reuse; never destroy a live <video>) ---
  const cams = ctx.cameras();
  const wantIds = new Set(cams.map(d => d.id));
  for (const child of [...grid.children]){
    if (child.classList && child.classList.contains('tile')){
      const id = child.id.replace('tile-','');
      if (!wantIds.has(id)){ ctx.handlers.closeTile(id); child.remove(); }
    }
  }
  const empty = grid.querySelector('.feeds-empty'); if (empty) empty.remove();
  for (const d of cams){
    let tile = document.getElementById(`tile-${d.id}`);
    if (!tile){ tile = buildTile(d, ctx); grid.appendChild(tile); }
    tile.__d = d;
    tile.classList.toggle('offline', !d.online);
    tile.querySelector('.tile-badge').innerHTML = pip(d.online);
    tile.querySelector('.tile-meta strong').textContent = d.name;
    tile.querySelector('.tile-meta small').textContent = d.location || '';
    tile.querySelector('.tile-stats').textContent = d.online
      ? `${fmtBitrate(d.bitrateKbps)} · ${fmtUptime(d.uptimeSec)} · 👁 ${d.readers ?? 0}` : '';
    const video = tile.querySelector('video');
    if (d.online) ctx.handlers.startTile(d, video);   // idempotent
    else ctx.handlers.closeTile(d.id);                // close when a camera goes offline
  }
  if (!cams.length) grid.appendChild(el('p','muted feeds-empty','Немає камер. Додай вузол.'));

  // --- active detections: compact summary only (rebuilt each render; no live video here).
  // The full merged/sorted list with history lives on the FPV Viewer screen — here we just want
  // an at-a-glance count plus the strongest few, so the panel stays small next to the feeds grid.
  threats.innerHTML = '';
  threats.appendChild(el('div','label-caps','АКТИВНІ ДЕТЕКЦІЇ'));
  const newKeys = ctx.newDetKeys();
  const dets = ctx.scanners().flatMap(s => (store[s.id]?.detection?.detections||[]).map(x => ({ ...x, _sid: s.id })))
    .sort((a,b) => (b.power_dbm ?? -999) - (a.power_dbm ?? -999));
  threats.appendChild(el('p','mono', `Активних цілей: <strong>${dets.length}</strong>`));
  if (!dets.length){
    threats.appendChild(el('p','muted','Немає активних передавачів.'));
  } else {
    for (const d of dets.slice(0, TOP_DETECTIONS)){
      const card = detectionCard(d, newKeys.has(detectionKey(d)));
      const v = store[d._sid]?.video;
      if (v && v.frame_png_b64){ card.style.cursor='pointer';
        card.addEventListener('click', () => ctx.handlers.openImage(`data:image/png;base64,${v.frame_png_b64}`, frameCaption(v))); }
      threats.appendChild(card);
    }
    if (dets.length > TOP_DETECTIONS){
      threats.appendChild(el('p','muted', `+${dets.length - TOP_DETECTIONS} ще — <a href="#/viewer">FPV Viewer →</a>`));
    }
  }

  // --- node telemetry strip (rebuilt each render) ---
  strip.innerHTML = '';
  for (const d of ctx.devices()){
    const live = store[d.id]; const isScanner = d.kind === 'scanner';
    const online = isScanner ? !!(live && live.online) : d.online;
    const card = cornerCard(`<div class="nc-head"><span class="nc-title">${escapeHtml(d.name)}</span>${pip(online)}</div>
      <div class="nc-grid"><div><span class="k">TEMP</span>${tempSlot(null)}</div>
        <div><span class="k">UPTIME</span><span class="mono">${fmtUptime(d.uptimeSec)}</span></div></div>`);
    if (isScanner && live) card.appendChild(occupancyStrip(live.bands, live.detection?.occupancy||{}));
    strip.appendChild(card);
  }
}
