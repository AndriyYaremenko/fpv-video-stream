// dashboard/public/app.js — shell bootstrap: builds ctx + data stores, wires router/modals/topbar.
import { SoundAlerter, diffNewKeys } from '/alert.js';
import { MqttScanClient } from '/mqtt-scan.js';
import { startWhep } from '/whep.js';
import { createRouter } from '/router.js';
import { createModals } from '/modals.js';
import { render as renderDashboard } from '/views/dashboard.js';
import { render as renderNodes } from '/views/nodes.js';
import { render as renderLogs } from '/views/logs.js';

const PREVIEW = new URLSearchParams(location.search).has('preview');
const alerter = new SoundAlerter();
const scanClient = new MqttScanClient();
const players = new Map();          // id -> { player } | { player:null, starting:true }
let cfg = null, devices = [], newDetKeys = new Set(), prevScanKeys = null, fx = null;

const ctx = {
  get cfg(){ return cfg; }, isPreview: PREVIEW,
  devices: () => devices,
  scanStore: () => scanClient.store,
  scanners: () => devices.filter(d => d.kind === 'scanner'),
  cameras:  () => devices.filter(d => d.kind !== 'scanner'),
  newDetKeys: () => newDetKeys,
  getDetections: async () => {
    if (PREVIEW) return fx.detections;
    try { const r = await fetch('/api/detections?limit=200'); return r.ok ? r.json() : []; } catch { return []; }
  },
  onScanClick: (id, cmd) => { if (!PREVIEW) scanClient.publishCommand(id, cmd); },
  requestRender: () => router.renderActive(),
  handlers: {},
};

const routes = [
  { hash:'#/dashboard', label:'Панель',   icon:'▤', section:'screen-dashboard', mount:renderDashboard },
  { hash:'#/nodes',     label:'Вузли',     icon:'▦', section:'screen-nodes',     mount:renderNodes },
  { hash:'#/logs',      label:'Детекції',  icon:'≣', section:'screen-logs',      mount:renderLogs },
];
const router = createRouter({ routes, ctx });
const modals = createModals(ctx);
ctx.handlers = {
  openVideo: modals.openVideo, openImage: modals.openImage,
  openAddForm: modals.openAddForm, openEditForm: modals.openEditForm,
  viewCreds: modals.viewCreds, scannerInfo: modals.scannerInfo, deleteDevice: modals.deleteDevice,
  // Start WHEP for a camera tile, reusing any existing player. Idempotent: no-op if already playing/starting.
  startTile: (d, videoEl) => {
    if (PREVIEW || !d.online) return;
    const st = players.get(d.id);
    if (st && (st.player || st.starting)) return;
    players.set(d.id, { player: null, starting: true });
    startWhep(videoEl, `${cfg.webrtcBase}/${d.id}/whep`, cfg.readUser, cfg.readPass)
      .then(p => players.set(d.id, { player: p }))
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
};

// ---- topbar + sidebar wiring ----
const soundBtn = document.getElementById('sound-toggle');
function setSoundUI(){ soundBtn.textContent = alerter.armed ? '🔔' : '🔕'; soundBtn.classList.toggle('btn-ghost', alerter.armed); }
soundBtn.addEventListener('click', () => { alerter.armed ? alerter.disarm() : alerter.arm(); localStorage.setItem('soundArmed', alerter.armed?'1':'0'); setSoundUI(); });
if (localStorage.getItem('soundArmed')==='1'){ document.addEventListener('pointerdown', ()=>{alerter.arm();setSoundUI();}, {once:true}); soundBtn.textContent='🔔'; } else setSoundUI();
document.getElementById('add-device').addEventListener('click', () => modals.openAddForm());
document.getElementById('logout').addEventListener('click', async () => { if(!PREVIEW) await fetch('/logout',{method:'POST'}); location.href='/login.html'; });
const sizeInput = document.getElementById('tile-size');
sizeInput.value = localStorage.getItem('tileMin') || '320';
document.documentElement.style.setProperty('--tile-min', `${sizeInput.value}px`);
sizeInput.addEventListener('input', () => { document.documentElement.style.setProperty('--tile-min', `${sizeInput.value}px`); localStorage.setItem('tileMin', sizeInput.value); });

function computeNewDetKeys(){
  const all = ctx.scanners().flatMap(s => (scanClient.store[s.id]?.detection?.detections)||[]);
  const { keys, newKeys } = diffNewKeys(prevScanKeys, all);
  newDetKeys = new Set(newKeys);
  if (prevScanKeys!==null && newKeys.length && alerter.armed) alerter.beep();
  prevScanKeys = Object.keys(scanClient.store).length ? keys : null;
}
function updateStatus(){
  const cams = ctx.cameras(); const online = cams.filter(d=>d.online).length;
  const pill = document.getElementById('status-pill');
  pill.textContent = `ОПЕРАЦІЙНИЙ · ${online}/${cams.length}`;
  pill.classList.toggle('warn', online < cams.length);
  const dets = ctx.scanners().flatMap(s => (scanClient.store[s.id]?.detection?.detections)||[]).length;
  document.getElementById('global-status').textContent = `${dets} активних детекцій`;
}
// Close players for cameras that no longer exist (deleted/converted), even off the dashboard screen.
function prunePlayers(){
  const ids = new Set(ctx.cameras().map(d => d.id));
  for (const id of [...players.keys()]) if (!ids.has(id)) ctx.handlers.closeTile(id);
}
function onData(newDevices){
  if (newDevices) devices = newDevices;
  computeNewDetKeys(); updateStatus(); prunePlayers(); router.renderActive();
}
function connectSSE(){
  const es = new EventSource('/api/stream');
  es.onmessage = (e) => onData(JSON.parse(e.data));
  es.onerror = () => { es.close(); setTimeout(connectSSE, 3000); };   // EventSource won't auto-retry after close()
}

async function boot(){
  if (PREVIEW){
    fx = (await import('/fixtures.js')).FIXTURES;
    cfg = fx.config; devices = fx.devices; scanClient.store = structuredClone(fx.scanStore);
    document.getElementById('operator-name').textContent = fx.operator;
    computeNewDetKeys(); updateStatus(); router.start();
    window.__rerender = () => router.renderActive();   // dev-only seam to test view/tile reuse
    return;
  }
  const c = await fetch('/api/config'); if (c.status===401){ location.href='/login.html'; return; } cfg = await c.json();
  devices = await fetch('/api/devices').then(r=>r.json());
  computeNewDetKeys(); updateStatus(); router.start();
  connectSSE();
  try { const mq = await fetch('/api/mqtt').then(r=>r.ok?r.json():null);
    if (mq && mq.url) scanClient.connect(mq, () => onData()); } catch {}
}
boot();
