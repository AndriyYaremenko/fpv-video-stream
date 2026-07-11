// dashboard/public/views/nodes.js — node management: all devices as cards, CRUD, RX5808 controls,
// per-scanner SDR view controls (manual start/stop of the shared FPV Viewer session from a specific
// scanner's card).
//
// RECONCILE-BASED (build-once skeleton + in-place live-field updates, like views/dashboard.js). The
// screen is re-mounted on every SSE/MQTT data tick (route `live:true`), so it must NEVER tear down and
// rebuild the interactive controls: a full `innerHTML=''` every ~2s would wipe the operator's typed
// `.view-freq` value (Contract #5) and close an open RX5808 channel <select> mid-selection. Instead
// each card is built once (keyed by device id) and only its live text/class fields are updated.
//
// d.telemetry is a dead field (never populated upstream) — not rendered; the camera card's 4th cell
// shows live MediaMTX reader count instead (lib/status.js: d.readers).
import { el, pip, occupancyStrip, fmtUptime, fmtBitrate, tempSlot, escapeHtml } from '/views/components.js';
import { RX5808_CHANNELS } from '/rx5808-channels.js';
import { viewCaption } from '/spectrum.js';

// RX5808 mode buttons + channel <select>, built once. The active-mode highlight is applied later by
// updateRx() so re-renders don't rebuild (and thus close) the <select>.
function rx5808Controls(scannerId, ctx) {
  const row = el('div', 'rx5808-ctl');
  for (const m of ['auto', 'scan', 'random', 'manual']) {
    const b = el('button', 'rx-mode', m);
    b.type = 'button'; b.dataset.mode = m;
    b.addEventListener('click', () => ctx.onScanCmd(scannerId, { mode: m }));
    row.appendChild(b);
  }
  const sel = el('select', 'rx5808-ch');
  for (const ch of RX5808_CHANNELS) { const o = document.createElement('option'); o.value = ch.name; o.textContent = `${ch.name} · ${ch.freq}`; sel.appendChild(o); }
  sel.addEventListener('change', () => ctx.onScanCmd(scannerId, { mode: 'manual', channel: sel.value }));
  row.appendChild(sel);
  return row;
}
function updateRx(row, activeMode) {
  for (const b of row.querySelectorAll('.rx-mode')) b.classList.toggle('active', b.dataset.mode === activeMode);
}

// Per-scanner SDR view controls: manually start/stop the shared FPV Viewer session at a given
// frequency, from this scanner's own card. Built once; the freq input is owned by the operator and is
// only ever pre-filled at build time (buildCard) — updateView() touches only the badge/error/stop state.
function viewControls(scannerId, ctx) {
  const row = el('div', 'view-controls');

  const freqInput = el('input', 'view-freq');
  freqInput.type = 'number'; freqInput.min = '100'; freqInput.max = '6000'; freqInput.step = '1';
  freqInput.placeholder = 'МГц';

  const startBtn = el('button', 'btn', '▶ дивитись');
  startBtn.type = 'button';
  startBtn.addEventListener('click', () => {
    const f = Number(freqInput.value);
    if (Number.isFinite(f) && f >= 100 && f <= 6000) ctx.onViewStart(scannerId, f);
  });

  const stopBtn = el('button', 'btn', '■ свіп');
  stopBtn.type = 'button'; stopBtn.dataset.role = 'stop';
  stopBtn.addEventListener('click', () => ctx.onViewStop(scannerId));

  const badge = el('span', 'view-badge');
  const errEl = el('span', 'view-err');

  row.append(freqInput, startBtn, stopBtn, badge, errEl);
  return row;
}
function updateView(row, view) {
  const active = !!(view && view.active);
  row.querySelector('[data-role=stop]').disabled = !active;
  row.querySelector('.view-badge').textContent = viewCaption(view) || '';
  row.querySelector('.view-err').textContent = (view && view.error) || '';
}

// Build a node card's static skeleton + interactive controls (wired once). Live fields carry
// data-role markers so updateCard() can refresh them in place without rebuilding the card.
function buildCard(d, ctx, store) {
  const isScanner = d.kind === 'scanner';
  const card = el('div', 'node-card');
  card.dataset.id = d.id; card.dataset.kind = d.kind;
  card.innerHTML = `<div class="nc-head"><div><div class="nc-title">${escapeHtml(d.name)}</div>
      <div class="nc-sub">${escapeHtml(d.id)} · ${isScanner ? 'SCANNER' : 'CAMERA'}</div></div><span data-role="pip"></span></div>
    <div class="nc-grid">
      <div><span class="k">TEMP</span>${tempSlot(null)}</div>
      <div><span class="k">UPTIME</span><span class="mono" data-role="uptime">—</span></div>
      <div><span class="k">${isScanner ? 'ЛОКАЦІЯ' : 'BITRATE'}</span><span class="mono" data-role="col3">—</span></div>
      <div><span class="k">${isScanner ? 'ДЕТЕКЦІЙ' : 'READERS'}</span><span class="mono" data-role="col4">0</span></div>
    </div>`;

  if (isScanner) {
    const occSlot = el('div', 'occ-slot'); occSlot.dataset.role = 'occ'; card.appendChild(occSlot);
    const rx = rx5808Controls(d.id, ctx); rx.dataset.role = 'rx'; card.appendChild(rx);
    const vc = viewControls(d.id, ctx); vc.dataset.role = 'view'; card.appendChild(vc);
    // One-time freq pre-fill from the currently-active view (never overwritten again, so live ticks
    // can't clobber the operator's own typing).
    const live = store[d.id];
    if (live && live.view && live.view.active && live.view.freq_mhz != null) vc.querySelector('.view-freq').value = String(live.view.freq_mhz);
  }

  const actions = el('div', 'nc-actions',
    `<button class="btn" data-act="edit">✏️ Редагувати</button>
     <button class="btn" data-act="${isScanner ? 'info' : 'creds'}">🔑 ${isScanner ? 'Інфо' : 'Креди'}</button>
     ${isScanner ? '' : '<button class="btn" data-act="restart">🔄 Перезапуск</button>'}
     <button class="btn" data-act="del">🗑 Видалити</button>`);
  actions.querySelector('[data-act=edit]').addEventListener('click', () => ctx.handlers.openEditForm(d.id));
  actions.querySelector('[data-act=del]').addEventListener('click', () => ctx.handlers.deleteDevice(d.id, d.name));
  const infoBtn = actions.querySelector('[data-act=info]'); if (infoBtn) infoBtn.addEventListener('click', () => ctx.handlers.scannerInfo(d.id));
  const credBtn = actions.querySelector('[data-act=creds]'); if (credBtn) credBtn.addEventListener('click', () => ctx.handlers.viewCreds(d.id));
  const reBtn = actions.querySelector('[data-act=restart]'); if (reBtn) reBtn.addEventListener('click', () => ctx.handlers.restartTile(d.id));
  card.appendChild(actions);
  return card;
}

// Refresh only the live fields; leaves the freq input and channel <select> DOM untouched.
function updateCard(card, d, ctx, store) {
  const isScanner = d.kind === 'scanner';
  const live = store[d.id];
  const online = isScanner ? !!(live && live.online) : d.online;
  card.querySelector('.nc-title').textContent = d.name;              // reflect a renamed device
  card.querySelector('[data-role=pip]').innerHTML = pip(online);
  card.querySelector('[data-role=uptime]').textContent = fmtUptime(d.uptimeSec);
  card.querySelector('[data-role=col3]').textContent = isScanner ? (d.location || '—') : fmtBitrate(d.bitrateKbps);
  card.querySelector('[data-role=col4]').textContent = isScanner ? String((live?.detection?.detections?.length) || 0) : String(d.readers ?? 0);
  if (isScanner) {
    const occSlot = card.querySelector('[data-role=occ]');
    occSlot.innerHTML = '';
    if (live) occSlot.appendChild(occupancyStrip(live.bands, live.detection?.occupancy || {}));
    updateRx(card.querySelector('[data-role=rx]'), live?.rxtune?.mode || null);
    updateView(card.querySelector('[data-role=view]'), live?.view || null);
  }
}

export function render(container, ctx) {
  container.className = 'screen screen-pad';
  // Build the header + grid once; reuse the grid (and its cards' live inputs) on subsequent renders.
  let grid = container.querySelector('.node-strip');
  if (!grid) {
    container.innerHTML = '';
    container.appendChild(el('div', 'label-caps', 'КЕРУВАННЯ ВУЗЛАМИ'));
    grid = el('div', 'node-strip');
    container.appendChild(grid);
  }

  const store = ctx.scanStore();
  const devices = ctx.devices();
  const wantIds = new Set(devices.map((d) => d.id));

  // Index existing cards by id; drop cards for devices that no longer exist.
  const existing = new Map();
  for (const child of [...grid.children]) {
    const id = child.dataset && child.dataset.id;
    if (!id) continue;
    if (!wantIds.has(id)) child.remove();
    else existing.set(id, child);
  }

  // Reuse or build each card, then update its live fields in place.
  for (const d of devices) {
    let card = existing.get(d.id);
    if (!card || card.dataset.kind !== d.kind) {   // rebuild if a device changed kind (rare)
      if (card) card.remove();
      card = buildCard(d, ctx, store);
      grid.appendChild(card);
    }
    updateCard(card, d, ctx, store);
  }
}
