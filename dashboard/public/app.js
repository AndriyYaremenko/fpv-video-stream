// dashboard/public/app.js — render the grid, manage WHEP players, handle online/offline diffs.
import { startWhep } from '/whep.js';

let cfg = null;
const players = new Map(); // id -> { player } | { player: null, starting: true }
const grid = document.getElementById('grid');

document.getElementById('logout').addEventListener('click', async () => {
  await fetch('/logout', { method: 'POST' });
  location.href = '/login.html';
});

async function loadConfig() {
  const res = await fetch('/api/config');
  if (res.status === 401) { location.href = '/login.html'; return null; }
  return res.json();
}

function tileEl(d) {
  let el = document.getElementById(`tile-${d.id}`);
  if (el) return el;
  el = document.createElement('section');
  el.id = `tile-${d.id}`;
  el.className = 'tile';
  el.innerHTML = `
    <video id="vid-${d.id}" autoplay playsinline muted></video>
    <div class="tile-overlay">
      <span class="badge" id="badge-${d.id}"></span>
      <div class="tile-meta">
        <strong>${escapeHtml(d.name)}</strong>
        <small>${escapeHtml(d.location)}</small>
      </div>
      <div class="tile-stats" id="stats-${d.id}"></div>
      <div class="tile-telemetry" id="tel-${d.id}"></div>
    </div>`;
  el.addEventListener('click', () => openModal(d));
  grid.appendChild(el);
  return el;
}

function render(devices) {
  document.getElementById('summary').textContent =
    `${devices.filter((d) => d.online).length}/${devices.length} онлайн`;

  for (const d of devices) {
    const el = tileEl(d);
    el.classList.toggle('offline', !d.online);
    const badge = el.querySelector(`#badge-${d.id}`);
    badge.textContent = d.online ? 'ONLINE' : 'OFFLINE';
    badge.className = `badge ${d.online ? 'on' : 'off'}`;

    el.querySelector(`#stats-${d.id}`).textContent = d.online
      ? `${fmtBitrate(d.bitrateKbps)} · ${fmtUptime(d.uptimeSec)} · 👁 ${d.readers}` : '';

    el.querySelector(`#tel-${d.id}`).textContent = d.telemetry
      ? telemetryLine(d.telemetry) : '';

    const state = players.get(d.id) || {};
    if (d.online && !state.player && !state.starting) {
      startPlayer(d);
    } else if (!d.online && state.player) {
      state.player.close();
      players.set(d.id, { player: null });
    }
  }
}

async function startPlayer(d) {
  const video = document.getElementById(`vid-${d.id}`);
  players.set(d.id, { player: null, starting: true });
  try {
    const player = await startWhep(video, `${cfg.webrtcBase}/${d.id}/whep`, cfg.readUser, cfg.readPass);
    players.set(d.id, { player });
  } catch {
    players.set(d.id, { player: null }); // clear `starting` so a later tick retries
  }
}

let modalPlayer = null;
function openModal(d) {
  const modal = document.getElementById('modal');
  const video = document.getElementById('modal-video');
  if (modalPlayer) { modalPlayer.close(); modalPlayer = null; } // tear down any previous modal stream
  document.getElementById('modal-caption').textContent = `${d.name} — ${d.location}`;
  modal.classList.remove('hidden');
  if (d.online) startWhep(video, `${cfg.webrtcBase}/${d.id}/whep`, cfg.readUser, cfg.readPass)
    .then((p) => { modalPlayer = p; }).catch(() => {});
  const close = () => { if (modalPlayer) { modalPlayer.close(); modalPlayer = null; } modal.classList.add('hidden'); };
  document.getElementById('modal-close').onclick = close;
}

function connectSSE() {
  const es = new EventSource('/api/stream');
  es.onmessage = (e) => render(JSON.parse(e.data));
  es.onerror = () => { es.close(); setTimeout(connectSSE, 3000); }; // reconnect
}

function fmtBitrate(kbps) { return kbps == null ? '—' : kbps >= 1000 ? `${(kbps / 1000).toFixed(1)} Mbps` : `${kbps} kbps`; }
function fmtUptime(s) { if (s == null) return '—'; const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60); return h ? `${h}год ${m}хв` : `${m}хв`; }
function telemetryLine(t) { const parts = []; if (t.rssi != null) parts.push(`RSSI ${t.rssi}`); if (t.freq != null) parts.push(`${t.freq}`); if (t.alarm) parts.push('⚠ ALARM'); return parts.join(' · '); }
function escapeHtml(s) { return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

(async function init() {
  cfg = await loadConfig();
  if (!cfg) return;
  const first = await fetch('/api/devices').then((r) => r.json());
  render(first);
  connectSSE();
})();
