// dashboard/public/views/viewer.js — «FPV Viewer» screen: merged detection list (left) +
// shared gen-token WHEP player + mini-spectrum + manual view controls (right).
// Reconcile-based like dashboard.js: the `.viewer` skeleton (incl. #viewer-video, the player's
// mount point) is built ONCE and reused across renders, so app.js's WHEP player never churns.
import { el, escapeHtml } from '/views/components.js';
import { viewerRows, activeViewer, pickViewer, ageLabel } from '/viewer.js';
// Note: viewStream() is not imported — app.js's syncViewerPlayer() (called below, after every
// DOM update) owns stream-name resolution and the WHEP player lifecycle for #viewer-video.
import { renderMiniSpectrum, classColor, fmtFreq, viewCaption, frameCaption } from '/spectrum.js';
import { nearestRxChannel } from '/rx5808-channels.js';

export function render(container, ctx) {
  container.className = 'screen';
  let root = container.querySelector('.viewer');
  if (!root) {
    container.innerHTML = '';
    root = buildSkeleton(ctx);
    container.appendChild(root);
  }
  updateSkeleton(root, ctx);
}

// ---- one-time DOM + listeners (never rebuilt; keeps #viewer-video alive across renders) ----
function buildSkeleton(ctx) {
  const root = el('div', 'viewer');

  const list = el('div', 'viewer-list');
  // Delegated click: the list body is rebuilt every render, the listener is not.
  list.addEventListener('click', (e) => {
    const row = e.target.closest('.viewer-row');
    if (!row) return;
    const freq = Number(row.dataset.vwfreq);
    if (!Number.isFinite(freq)) return;
    ctx.handlers.viewerRowClick(freq, row.dataset.vwband || '', row.dataset.sid || '');
  });

  const stage = el('div', 'viewer-stage', `
    <video id="viewer-video" autoplay playsinline muted></video>
    <div class="view-controls">
      <input id="viewer-freq" type="number" min="100" max="6000" step="1" placeholder="МГц" />
      <button type="button" id="viewer-play" class="btn">▶ дивитись</button>
      <button type="button" id="viewer-stop" class="btn" hidden>■ свіп</button>
      <span id="viewer-badge" class="view-badge"></span>
      <span id="viewer-err" class="view-err"></span>
    </div>
    <canvas class="mini-spectrum" width="300" height="60"></canvas>
    <img id="viewer-thumb" alt="відновлений кадр"
      style="max-width:100%;border:1px solid var(--line);cursor:pointer;display:block;" hidden />
  `);

  const freqInput = stage.querySelector('#viewer-freq');
  stage.querySelector('#viewer-play').addEventListener('click', () => {
    const vid = pickViewer(ctx.scanStore());
    const f = Number(freqInput.value);
    if (vid && Number.isFinite(f) && f >= 100 && f <= 6000) ctx.onViewStart(vid, f);
  });
  stage.querySelector('#viewer-stop').addEventListener('click', () => {
    const aid = activeViewer(ctx.scanStore());
    if (aid) ctx.onViewStop(aid);
  });

  // Canvas freq-pick: click fills the manual freq field; on the 5.8G band it also nudges the
  // RX5808 (same scanner the mini-spectrum is drawn for) to the nearest hardware channel.
  const canvas = stage.querySelector('canvas.mini-spectrum');
  canvas.addEventListener('click', (e) => {
    const lo = Number(canvas.dataset.lowMhz);
    const hi = Number(canvas.dataset.highMhz);
    if (!Number.isFinite(lo) || !Number.isFinite(hi)) return;
    const rect = canvas.getBoundingClientRect();
    const x = Math.min(rect.width, Math.max(0, e.clientX - rect.left));
    const freq = Math.round(lo + (x / (rect.width || 1)) * (hi - lo));
    freqInput.value = String(freq);
    if (canvas.classList.contains('tunable')) {
      const sid = canvas.dataset.sid;
      const ch = nearestRxChannel(freq);
      if (sid && ch) ctx.onScanCmd(sid, { mode: 'manual', channel: ch.name });
    }
  });

  const thumb = stage.querySelector('#viewer-thumb');
  thumb.addEventListener('click', () => {
    if (!thumb.hidden && thumb.src) ctx.handlers.openImage(thumb.src, thumb.dataset.cap || '');
  });

  root.appendChild(list);
  root.appendChild(stage);
  return root;
}

// ---- per-tick refresh: rows, badge, mini-spectrum, thumbnail, then hand off to app.js's player ----
function updateSkeleton(root, ctx) {
  const nowS = Math.floor(Date.now() / 1000);
  const store = ctx.scanStore();

  const routeId = pickViewer(store);       // where a NEW ▶ start goes
  const activeId = activeViewer(store);    // whose session the panel is CURRENTLY showing
  const displayId = activeId || routeId;   // scanner behind the stage (spectrum/thumbnail)
  const view = activeId ? store[activeId].view : null;

  renderList(root.querySelector('.viewer-list'), ctx, nowS, view);
  renderStage(root.querySelector('.viewer-stage'), store, displayId, view);

  // Mounted (or re-confirmed) #viewer-video above; let app.js's gen-token player attach/resync.
  ctx.handlers.syncViewerPlayer();
}

function renderList(list, ctx, nowS, view) {
  const rows = viewerRows(ctx.viewerState(), nowS, ctx.scanStore());
  const activeFreq = view && view.active ? view.freq_mhz : null;
  const canView = !!pickViewer(ctx.scanStore());
  list.innerHTML = '';
  if (!canView) list.appendChild(el('p', 'muted', 'SDR view недоступний (view-сканер офлайн)'));
  if (!rows.length) {
    list.appendChild(el('p', 'muted', 'детекцій немає — чекаємо на скан'));
    return;
  }
  for (const e of rows) list.appendChild(rowEl(e, nowS, activeFreq));
}

function rowEl(entry, nowS, activeFreq) {
  const viewing = activeFreq != null && Math.abs(entry.center_mhz - activeFreq) < 3;
  const clsName = entry.class === 'analog' ? 'analog' : entry.class === 'digital' ? 'digital' : '';
  const row = el('div', `viewer-row${clsName ? ` ${clsName}` : ''}${entry.live ? '' : ' recent'}${viewing ? ' is-viewing' : ''}`);
  row.dataset.vwfreq = String(entry.center_mhz);
  row.dataset.vwband = entry.band || '';
  row.dataset.sid = Object.keys(entry.seen_by || {})[0] || '';
  const freqTxt = `${fmtFreq(entry.center_mhz)}${entry.channel ? ` (${escapeHtml(entry.channel)})` : ''}`;
  const snr = entry.snr_db == null ? '—' : `${Number(entry.snr_db).toFixed(1)} dB`;
  const src = Object.keys(entry.seen_by || {}).map(escapeHtml).join(', ') || '—';
  const age = entry.live ? 'зараз' : ageLabel(nowS, entry.last_seen);
  row.innerHTML = `<div class="vr-top"><span class="vr-freq mono">${freqTxt}</span>` +
    `<span class="mono" style="color:${classColor(entry.class)}">${escapeHtml(entry.class || '')}</span></div>` +
    `<div class="vr-meta">${escapeHtml(entry.band || '')} · SNR ${snr} · ${src} · ${age}</div>`;
  return row;
}

// The band behind the mini-spectrum/thumbnail: whichever band range contains the active view's
// frequency; otherwise 5.8G (RX5808's band), else whatever band data the scanner reports.
function pickBand(live, view) {
  const bands = (live && live.bands) || {};
  if (view && view.active && view.freq_mhz != null) {
    for (const [name, range] of Object.entries(bands)) {
      if (range && view.freq_mhz >= range.low_mhz && view.freq_mhz <= range.high_mhz) return name;
    }
  }
  if (bands['5.8G']) return '5.8G';
  const keys = Object.keys(bands);
  return keys.length ? keys[0] : null;
}

function renderStage(stage, store, displayId, view) {
  stage.querySelector('#viewer-badge').textContent = view ? viewCaption(view) : '';
  stage.querySelector('#viewer-err').textContent = (view && view.error) || '';
  stage.querySelector('#viewer-stop').hidden = !(view && view.active);

  const live = displayId ? store[displayId] : null;
  const band = live ? pickBand(live, view) : null;
  const range = (live && band && live.bands && live.bands[band]) || {};
  const psd = (live && band && live.latestPsd && live.latestPsd[band]) || [];
  const dets = live ? ((live.detection && live.detection.detections) || []).filter((d) => d.band === band) : [];
  const rxFreq = live && live.rxtune ? live.rxtune.freq_mhz : null;

  const canvas = stage.querySelector('canvas.mini-spectrum');
  canvas.classList.remove('tunable');   // renderMiniSpectrum only ever adds it back
  if (range.low_mhz != null) {
    canvas.dataset.lowMhz = range.low_mhz;
    canvas.dataset.highMhz = range.high_mhz;
    canvas.dataset.sid = displayId;
  } else {
    delete canvas.dataset.lowMhz;
    delete canvas.dataset.highMhz;
    delete canvas.dataset.sid;
  }
  renderMiniSpectrum(canvas, { psd, range, dets, rxFreq, tunable: band === '5.8G' });

  const thumb = stage.querySelector('#viewer-thumb');
  const video = live && live.video;
  if (video && video.frame_png_b64) {
    thumb.src = `data:image/png;base64,${video.frame_png_b64}`;
    thumb.dataset.cap = frameCaption(video);
    thumb.hidden = false;
  } else {
    thumb.hidden = true;
    thumb.removeAttribute('src');
  }
}
