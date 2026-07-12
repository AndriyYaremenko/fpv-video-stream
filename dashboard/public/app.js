// dashboard/public/app.js — shell bootstrap: builds ctx + data stores, wires router/modals/topbar,
// owns the camera WHEP players and the FPV Viewer per-viewer WHEP players.
import { startWhep } from '/whep.js';
import { SoundAlerter, diffNewKeys } from '/alert.js';
import { MqttScanClient } from '/mqtt-scan.js';
import { nearestRxChannel } from '/rx5808-channels.js';
import { createRouter } from '/router.js';
import { createModals } from '/modals.js';
import {
  emptyViewer, applyDetections, seedFromJournal,
  pickViewer, pickRxScanner, whepRetryDelay, viewerCards,
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

// ==== FPV Viewer per-viewer WHEP players (Map keyed by scanner id) ==========
// One player per online view-capable SDR. Generalizes the single gen-token player:
// each id owns its own {player, streamKey, retry}; retry-object identity is the
// generation token, replaced whenever that id's stream changes or the card leaves.
const viewerPlayers = new Map();   // id -> { player, streamKey, retry:{timer,inflight} }

function syncViewerPlayers() {
  const store = scanClient.store;
  const cards = ctx.scanners().length ? viewerCards(store) : [];
  const wantIds = new Set(cards.map((c) => c.id));
  for (const [id, st] of viewerPlayers) {          // tear down players whose card is gone
    if (!wantIds.has(id)) {
      if (st.player) st.player.close();
      if (st.retry.timer) clearTimeout(st.retry.timer);
      viewerPlayers.delete(id);
    }
  }
  for (const c of cards) {
    const video = document.getElementById(`viewer-video-${c.id}`);
    if (!video) continue;                          // card not mounted yet
    let st = viewerPlayers.get(c.id);
    if (!st) { st = { player: null, streamKey: '', retry: { timer: null, inflight: false } }; viewerPlayers.set(c.id, st); }
    const want = PREVIEW ? '' : c.stream;
    if (want !== st.streamKey) {
      if (st.player) st.player.close();
      st.player = null;
      st.streamKey = want;
      if (st.retry.timer) clearTimeout(st.retry.timer);
      st.retry = { timer: null, inflight: false };  // new generation for this id
      if (!want) { video.srcObject = null; continue; }
      startViewerWhep(c.id, video, c.stream, st.retry, 0);
      continue;
    }
    if (want && !st.player && !st.retry.timer && !st.retry.inflight) {
      startViewerWhep(c.id, video, c.stream, st.retry, 0);   // same key, died, retries gave up -> re-kick
    }
  }
}

async function startViewerWhep(id, video, stream, retry, attempt) {
  const st0 = viewerPlayers.get(id);
  if (PREVIEW || !st0 || st0.retry !== retry || attempt > 40) return;
  retry.inflight = true;
  try {
    const p = await startWhep(video, `${cfg.webrtcBase}/${stream}/whep`, cfg.readUser, cfg.readPass,
      () => { const s = viewerPlayers.get(id); if (s && s.player === p) { p.close(); s.player = null; } });
    retry.inflight = false;
    const s = viewerPlayers.get(id);
    if (!s || s.retry !== retry || s.player) { p.close(); return; }   // superseded or a sibling won
    s.player = p;
  } catch {
    retry.inflight = false;
    const s = viewerPlayers.get(id);
    if (!s || s.retry !== retry) return;                              // superseded: stay inert
    retry.timer = setTimeout(() => { retry.timer = null; startViewerWhep(id, video, stream, retry, attempt + 1); },
      whepRetryDelay(attempt));
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
  onViewStart: (id, freq, bw) => { if (!PREVIEW) scanClient.publishView(id, 'start', freq, bw); },
  onViewStop: (id) => { if (!PREVIEW) scanClient.publishView(id, 'stop'); },
  requestRender: () => router.renderActive(),
  handlers: {},
};

// `live:true` = re-mount on every SSE/MQTT data tick (its render() reads the live store and is
// reconcile-safe — never wipes a live <video> or a user-typed input). detections/frames omit it:
// they fetch on mount + explicit refresh/apply/pagination, so ticking them only wipes half-typed
// filters and rebuilds cached tables (see router.renderLive).
const routes = [
  { hash: '#/dashboard', label: 'Панель', icon: '▤', section: 'screen-dashboard', mount: renderDashboard, live: true },
  { hash: '#/viewer', label: 'FPV Viewer', icon: '🎯', section: 'screen-viewer', mount: renderViewer, live: true },
  { hash: '#/nodes', label: 'Вузли', icon: '▦', section: 'screen-nodes', mount: renderNodes, live: true },
  { hash: '#/detections', label: 'Детекції', icon: '≣', section: 'screen-detections', mount: renderDetections },
  { hash: '#/frames', label: 'Кадри', icon: '🖼️', section: 'screen-frames', mount: renderFrames },
];
const router = createRouter({ routes, ctx });
const modals = createModals(ctx);

// FPV Viewer row/button click: start the view on the explicitly-chosen viewerId (the row
// renders a button per online viewer); fall back to pickViewer if that id isn't a live viewer.
// 5.8G also nudges the RX5808 to the nearest hardware channel (unchanged).
function viewerRowClick(freq, band, viewerId, bw) {
  if (PREVIEW) return;
  const store = scanClient.store;
  const chosen = (viewerId && store[viewerId] && store[viewerId].online && store[viewerId].view) ? viewerId : null;
  const vid = chosen || pickViewer(store);
  if (!vid || !Number.isFinite(freq) || freq < 100 || freq > 6000) return;
  scanClient.publishView(vid, 'start', freq, bw);
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
  // renderLive (not renderActive) so restart-all from a non-live screen (e.g. Кадри with un-applied
  // filter typing) doesn't force a full re-mount that would wipe it; the tile only re-establishes on
  // the Панель render anyway (dashboard.js startTile), and Панель is live:true.
  restartTile: (id) => {
    const st = players.get(id); if (st && st.player) st.player.close(); players.delete(id);
    const v = document.querySelector(`#tile-${id} video`); if (v) v.srcObject = null;
    router.renderLive();
  },
  viewerRowClick,
  syncViewerPlayers,
  onThresholds: (id, obj) => { if (!PREVIEW) scanClient.publishThresholds(id, obj); },
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
// Close players for cameras that are gone (deleted/converted) OR offline, even when the operator is
// on another screen. dashboard.js only starts/reuses players for ONLINE cameras and only while the
// Панель screen is mounted, so without this an offline camera would leak a dead RTCPeerConnection
// until the dashboard is next visited. Runs on every data tick (all screens); startTile re-establishes
// a returning camera on the next dashboard render.
function prunePlayers() {
  const onlineIds = new Set(ctx.cameras().filter((d) => d.online).map((d) => d.id));
  for (const id of [...players.keys()]) if (!onlineIds.has(id)) ctx.handlers.closeTile(id);
}
function onData(newDevices) {
  if (newDevices) devices = newDevices;
  foldDetections();
  computeNewDetKeys();
  updateStatus();
  prunePlayers();
  router.renderLive();
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
    window.__store = scanClient.store;   // dev-only seam: visual gate mutates online/view then __rerender()
    document.getElementById('operator-name').textContent = fx.operator;
    seedFromJournal(viewerState, fx.detections, Math.floor(Date.now() / 1000));
    foldDetections();
    computeNewDetKeys();
    updateStatus();
    router.start();
    window.__rerender = () => router.renderActive();   // dev-only seam to test view/tile reuse
    setInterval(() => router.renderLive(), 30000);
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
  setInterval(() => router.renderLive(), 30000);
}
boot();
