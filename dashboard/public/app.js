// dashboard/public/app.js — shell bootstrap: builds ctx + data stores, wires router/modals/topbar,
// owns the camera WHEP players and the FPV Viewer generation-token player.
import { startWhep } from '/whep.js';
import { SoundAlerter, diffNewKeys } from '/alert.js';
import { MqttScanClient } from '/mqtt-scan.js';
import { nearestRxChannel } from '/rx5808-channels.js';
import { createRouter } from '/router.js';
import { createModals } from '/modals.js';
import {
  emptyViewer, applyDetections, seedFromJournal,
  pickViewer, pickRxScanner, viewStream, activeViewer, playerKey, whepRetryDelay,
} from '/viewer.js';
import { render as renderDashboard } from '/views/dashboard.js';
import { render as renderViewer } from '/views/viewer.js';
import { render as renderNodes } from '/views/nodes.js';
import { render as renderDetections } from '/views/detections.js';
import { render as renderFrames } from '/views/frames.js';

const PREVIEW = new URLSearchParams(location.search).has('preview');

// ---- data stores (single source of truth; views read these only via ctx accessors) ----
const alerter = new SoundAlerter();
const scanClient = new MqttScanClient();
const players = new Map();          // camera id -> { player } | { player:null, starting:true }
const viewerState = emptyViewer();  // viewer.js state: { entries, seenTs } — mutated in place
let cfg = null;
let devices = [];
let newDetKeys = new Set();
let prevScanKeys = null;
let fx = null;                      // FIXTURES, preview only

// ==== FPV Viewer generation-token player ====================================
// Relocated verbatim (behavior-preserving) from the current main branch's
// dashboard/public/app.js (renderViewer's player wiring + syncViewerPlayer +
// startViewerWhep, app.js:327-402). Only the trigger changed: instead of
// living inside a renderViewer() that ALSO drew the panel HTML, the DOM/list
// rendering now belongs to views/viewer.js (Task 4); this module keeps just
// the player lifecycle and exposes it as ctx.handlers.syncViewerPlayer(),
// called by that view right after it mounts #viewer-video.
let viewerPlayer = null;      // {player}|null
let viewerStreamKey = '';     // stream name of the running/starting player
let viewerRetry = { timer: null, inflight: false };

// Keep the in-panel WHEP player in sync. The persistent engine keeps one path
// alive across start/stop/retune, so the key is the stream name: connect once
// when the panel appears, re-kick only if the connection actually died.
function syncViewerPlayer() {
  const video = document.getElementById('viewer-video');
  if (!video) return;                      // view hasn't mounted a player element (e.g. stub screen)
  const store = scanClient.store;
  const hasScanners = ctx.scanners().length > 0;
  const routeId = hasScanners ? pickViewer(store) : null;          // where NEW starts go
  const displayId = hasScanners ? (activeViewer(store) || routeId) : null; // whose session the panel shows
  const view = displayId ? store[displayId].view : null;
  const want = PREVIEW ? '' : playerKey(view, displayId ? viewStream(store, displayId) : '');
  if (want !== viewerStreamKey) {
    if (viewerPlayer && viewerPlayer.player) viewerPlayer.player.close();
    viewerPlayer = null;
    viewerStreamKey = want;
    if (viewerRetry.timer) clearTimeout(viewerRetry.timer);
    viewerRetry = { timer: null, inflight: false };   // new generation: stale chains go inert
    if (!want) { video.srcObject = null; return; }
    startViewerWhep(video, viewStream(store, displayId), viewerRetry, 0);
    return;
  }
  // Same key, but the player died (encoder respawn, server restart) and the
  // retries gave up — any resync tick re-kicks the connection.
  if (want && !viewerPlayer && !viewerRetry.timer && !viewerRetry.inflight) {
    startViewerWhep(video, viewStream(store, displayId), viewerRetry, 0);
  }
}

// `retry` is this attempt-chain's generation token: minted by syncViewerPlayer,
// mutated ONLY by its own chain, dead the moment a new generation replaces it.
async function startViewerWhep(video, stream, retry, attempt) {
  if (PREVIEW || viewerRetry !== retry || attempt > 40) return;
  retry.inflight = true;
  try {
    const p = await startWhep(video, `${cfg.webrtcBase}/${stream}/whep`, cfg.readUser, cfg.readPass,
      () => { if (viewerPlayer && viewerPlayer.player === p) { p.close(); viewerPlayer = null; } });
    retry.inflight = false;
    if (viewerRetry !== retry || viewerPlayer) { p.close(); return; }  // superseded or a sibling won
    viewerPlayer = { player: p };
  } catch {
    retry.inflight = false;
    if (viewerRetry !== retry) return;                 // superseded: stay inert
    retry.timer = setTimeout(() => {
      retry.timer = null;
      startViewerWhep(video, stream, retry, attempt + 1);
    }, whepRetryDelay(attempt));
  }
}

// Fold every scanner's latest detection payload into viewerState — ts-idempotent
// per scanner (applyDetections no-ops on a repeat ts), runs on every data tick
// regardless of which screen is active so "recent" rows survive a screen switch.
function foldDetections() {
  const nowS = Math.floor(Date.now() / 1000);
  for (const [sid, live] of Object.entries(scanClient.store)) {
    if (live.detection) applyDetections(viewerState, sid, live.detection, nowS);
  }
}

// ==== camera WHEP players (reuse-aware; semantics copied from v1) ===========
const ctx = {
  get cfg() { return cfg; },
  isPreview: PREVIEW,
  devices: () => devices,
  scanStore: () => scanClient.store,
  scanners: () => devices.filter((d) => d.kind === 'scanner'),
  cameras: () => devices.filter((d) => d.kind !== 'scanner'),
  viewerState: () => viewerState,
  newDetKeys: () => newDetKeys,
  getDetections: async () => {
    if (PREVIEW) return fx.detections;
    try { const r = await fetch('/api/detections?limit=200'); return r.ok ? r.json() : []; } catch { return []; }
  },
  fetchFrames: async (query) => {
    if (PREVIEW) return fx.frames;
    try {
      const r = await fetch(`/api/frames?${query || ''}`);
      return { frames: r.ok ? await r.json() : [] };
    } catch { return { frames: [] }; }
  },
  onScanCmd: (id, cmd) => { if (!PREVIEW) scanClient.publishCommand(id, cmd); },
  onViewStart: (id, freq) => { if (!PREVIEW) scanClient.publishView(id, 'start', freq); },
  onViewStop: (id) => { if (!PREVIEW) scanClient.publishView(id, 'stop'); },
  requestRender: () => router.renderActive(),
  handlers: {},
};

const routes = [
  { hash: '#/dashboard', label: 'Панель', icon: '▤', section: 'screen-dashboard', mount: renderDashboard },
  { hash: '#/viewer', label: 'FPV Viewer', icon: '🎯', section: 'screen-viewer', mount: renderViewer },
  { hash: '#/nodes', label: 'Вузли', icon: '▦', section: 'screen-nodes', mount: renderNodes },
  { hash: '#/detections', label: 'Детекції', icon: '≣', section: 'screen-detections', mount: renderDetections },
  { hash: '#/frames', label: 'Кадри', icon: '🖼️', section: 'screen-frames', mount: renderFrames },
];
const router = createRouter({ routes, ctx });
const modals = createModals(ctx);

// Dual-action FPV Viewer row click: start the SDR view session at `freq`, and
// on 5.8G also nudge the RX5808 to the nearest hardware channel (kept from
// main's spectrumPanel/viewerPanel click handlers). `scannerId` (the reporting
// scanner, per the ctx contract) is accepted but — matching main's exact
// behavior — NOT used to pick the target: the target is always whichever
// view-capable scanner pickViewer() resolves, same as upstream.
function viewerRowClick(freq, band, scannerId) {
  if (PREVIEW) return;
  const store = scanClient.store;
  const vid = pickViewer(store);
  if (!vid || !Number.isFinite(freq) || freq < 100 || freq > 6000) return;
  scanClient.publishView(vid, 'start', freq);
  if (band === '5.8G') {
    const rxId = pickRxScanner(store);
    const ch = nearestRxChannel(freq);
    if (rxId && ch) scanClient.publishCommand(rxId, { mode: 'manual', channel: ch.name });
  }
}

ctx.handlers = {
  openVideo: modals.openVideo,
  openImage: modals.openImage,
  openAddForm: modals.openAddForm,
  openEditForm: modals.openEditForm,
  viewCreds: modals.viewCreds,
  scannerInfo: modals.scannerInfo,
  deleteDevice: modals.deleteDevice,
  // Start WHEP for a camera tile, reusing any existing player. Idempotent: no-op if already playing/starting.
  startTile: (d, videoEl) => {
    if (PREVIEW || !d.online) return;
    const st = players.get(d.id);
    if (st && (st.player || st.starting)) return;
    players.set(d.id, { player: null, starting: true });
    startWhep(videoEl, `${cfg.webrtcBase}/${d.id}/whep`, cfg.readUser, cfg.readPass)
      .then((p) => players.set(d.id, { player: p }))
      .catch(() => players.set(d.id, { player: null }));   // clear starting; a later reconcile retries
  },
  // Tear down a tile's player (on offline / removal). Safe to call when none exists.
  closeTile: (id) => { const st = players.get(id); if (st && st.player) st.player.close(); players.delete(id); },
  // Restart a tile's live playback: close, clear the <video>, let the next reconcile re-establish it.
  restartTile: (id) => {
    const st = players.get(id); if (st && st.player) st.player.close(); players.delete(id);
    const v = document.querySelector(`#tile-${id} video`); if (v) v.srcObject = null;
    router.renderActive();
  },
  viewerRowClick,
  syncViewerPlayer,
};

// ---- topbar + sidebar wiring ----
const soundBtn = document.getElementById('sound-toggle');
function setSoundUI() { soundBtn.textContent = alerter.armed ? '🔔' : '🔕'; soundBtn.classList.toggle('btn-ghost', alerter.armed); }
soundBtn.addEventListener('click', () => { alerter.armed ? alerter.disarm() : alerter.arm(); localStorage.setItem('soundArmed', alerter.armed ? '1' : '0'); setSoundUI(); });
if (localStorage.getItem('soundArmed') === '1') {
  document.addEventListener('pointerdown', () => { alerter.arm(); setSoundUI(); }, { once: true });
  soundBtn.textContent = '🔔';
} else setSoundUI();

document.getElementById('add-device').addEventListener('click', () => modals.openAddForm());
document.getElementById('logout').addEventListener('click', async () => { if (!PREVIEW) await fetch('/logout', { method: 'POST' }); location.href = '/login.html'; });
document.getElementById('restart-all').addEventListener('click', () => { for (const d of ctx.cameras()) ctx.handlers.restartTile(d.id); });

const sizeInput = document.getElementById('tile-size');
sizeInput.value = localStorage.getItem('tileMin') || '320';
document.documentElement.style.setProperty('--tile-min', `${sizeInput.value}px`);
sizeInput.addEventListener('input', () => { document.documentElement.style.setProperty('--tile-min', `${sizeInput.value}px`); localStorage.setItem('tileMin', sizeInput.value); });

function computeNewDetKeys() {
  const all = ctx.scanners().flatMap((s) => (scanClient.store[s.id] && scanClient.store[s.id].detection && scanClient.store[s.id].detection.detections) || []);
  const { keys, newKeys } = diffNewKeys(prevScanKeys, all);
  newDetKeys = new Set(newKeys);
  if (prevScanKeys !== null && newKeys.length && alerter.armed) alerter.beep();
  prevScanKeys = Object.keys(scanClient.store).length ? keys : null;
}
function updateStatus() {
  const cams = ctx.cameras();
  const online = cams.filter((d) => d.online).length;
  const pill = document.getElementById('status-pill');
  pill.textContent = `ОПЕРАЦІЙНИЙ · ${online}/${cams.length}`;
  pill.classList.toggle('warn', online < cams.length);
  const dets = ctx.scanners().flatMap((s) => (scanClient.store[s.id] && scanClient.store[s.id].detection && scanClient.store[s.id].detection.detections) || []).length;
  document.getElementById('global-status').textContent = `${dets} активних детекцій`;
}
// Close players for cameras that no longer exist (deleted/converted), even off the dashboard screen.
function prunePlayers() {
  const ids = new Set(ctx.cameras().map((d) => d.id));
  for (const id of [...players.keys()]) if (!ids.has(id)) ctx.handlers.closeTile(id);
}
function onData(newDevices) {
  if (newDevices) devices = newDevices;
  foldDetections();
  computeNewDetKeys();
  updateStatus();
  prunePlayers();
  router.renderActive();
}
function connectSSE() {
  const es = new EventSource('/api/stream');
  es.onmessage = (e) => onData(JSON.parse(e.data));
  es.onerror = () => { es.close(); setTimeout(connectSSE, 3000); };   // EventSource won't auto-retry after close()
}

async function boot() {
  if (PREVIEW) {
    fx = (await import('/fixtures.js')).FIXTURES;
    cfg = fx.config;
    devices = fx.devices;
    scanClient.store = structuredClone(fx.scanStore);
    document.getElementById('operator-name').textContent = fx.operator;
    seedFromJournal(viewerState, fx.detections, Math.floor(Date.now() / 1000));
    foldDetections();
    computeNewDetKeys();
    updateStatus();
    router.start();
    window.__rerender = () => router.renderActive();   // dev-only seam to test view/tile reuse
    setInterval(() => router.renderActive(), 30000);
    return;
  }
  const c = await fetch('/api/config');
  if (c.status === 401) { location.href = '/login.html'; return; }
  cfg = await c.json();
  devices = await fetch('/api/devices').then((r) => r.json());
  // No per-operator identity in /api/config today; keep the HTML's static default label.
  computeNewDetKeys();
  updateStatus();
  router.start();
  connectSSE();
  try {
    const res = await fetch('/api/detections?limit=500');
    if (res.ok) seedFromJournal(viewerState, await res.json(), Math.floor(Date.now() / 1000));
  } catch { /* live-only if the journal is unavailable */ }
  try {
    const mq = await fetch('/api/mqtt').then((r) => (r.ok ? r.json() : null));
    if (mq && mq.url) scanClient.connect(mq, () => onData());
  } catch { /* no broker creds -> scan panel stays empty until available */ }
  // Age labels/TTL expiry must advance even when MQTT goes quiet.
  setInterval(() => router.renderActive(), 30000);
}
boot();
