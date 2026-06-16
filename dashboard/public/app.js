// dashboard/public/app.js — grid render, WHEP players, device management (add/delete/creds), tile sizing.
import { startWhep } from '/whep.js';

let cfg = null;
const players = new Map(); // id -> { player } | { player: null, starting: true }
const grid = document.getElementById('grid');

// ---- tile size (persisted) ----
const sizeInput = document.getElementById('tile-size');
const savedSize = localStorage.getItem('tileMin') || '320';
sizeInput.value = savedSize;
grid.style.setProperty('--tile-min', `${savedSize}px`);
sizeInput.addEventListener('input', () => {
  grid.style.setProperty('--tile-min', `${sizeInput.value}px`);
  localStorage.setItem('tileMin', sizeInput.value);
});

// ---- top bar actions ----
document.getElementById('logout').addEventListener('click', async () => {
  await fetch('/logout', { method: 'POST' });
  location.href = '/login.html';
});
document.getElementById('add-device').addEventListener('click', openAddForm);

// ---- reusable form/creds modal ----
const formModal = document.getElementById('form-modal');
const formBody = document.getElementById('form-modal-body');
function showModal(html) { formBody.innerHTML = html; formModal.classList.remove('hidden'); }
function hideModal() { formModal.classList.add('hidden'); formBody.innerHTML = ''; }
formModal.addEventListener('click', (e) => {
  if (e.target === formModal || e.target.hasAttribute('data-close')) hideModal();
  const copyBtn = e.target.closest('.copy');
  if (copyBtn) {
    const pre = copyBtn.closest('.cred-row').querySelector('pre');
    copyText(pre.textContent, copyBtn);
  }
});

async function copyText(text, btn) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    // navigator.clipboard needs a secure context; fall back for plain-HTTP-over-WG.
    const ta = document.createElement('textarea');
    ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); } catch { /* ignore */ }
    document.body.removeChild(ta);
  }
  if (btn) { const t = btn.textContent; btn.textContent = 'скопійовано ✓'; setTimeout(() => { btn.textContent = t; }, 1200); }
}

async function loadConfig() {
  const res = await fetch('/api/config');
  if (res.status === 401) { location.href = '/login.html'; return null; }
  return res.json();
}

// ---- grid tiles ----
function tileEl(d) {
  let el = document.getElementById(`tile-${d.id}`);
  if (el) return el;
  el = document.createElement('section');
  el.id = `tile-${d.id}`;
  el.className = 'tile';
  el.innerHTML = `
    <video id="vid-${d.id}" autoplay playsinline muted></video>
    <div class="tile-overlay">
      <div class="tile-top">
        <span class="badge" id="badge-${d.id}"></span>
        <div class="tile-actions">
          <button class="tile-btn act-creds" title="Креди / команда пушу">🔑</button>
          <button class="tile-btn act-del" title="Видалити вузол">🗑</button>
        </div>
      </div>
      <div class="tile-bottom">
        <div class="tile-meta">
          <strong>${escapeHtml(d.name)}</strong>
          <small>${escapeHtml(d.location)}</small>
        </div>
        <div class="tile-stats" id="stats-${d.id}"></div>
        <div class="tile-telemetry" id="tel-${d.id}"></div>
      </div>
    </div>`;
  el.addEventListener('click', () => openModal(d));
  el.querySelector('.act-creds').addEventListener('click', (e) => { e.stopPropagation(); viewCreds(d.id); });
  el.querySelector('.act-del').addEventListener('click', (e) => { e.stopPropagation(); deleteDevice(d.id, d.name); });
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
    el.querySelector(`#tel-${d.id}`).textContent = d.telemetry ? telemetryLine(d.telemetry) : '';

    const state = players.get(d.id) || {};
    if (d.online && !state.player && !state.starting) {
      startPlayer(d);
    } else if (!d.online && state.player) {
      state.player.close();
      players.set(d.id, { player: null });
    }
  }

  // Drop tiles for devices that no longer exist (e.g. deleted).
  const ids = new Set(devices.map((d) => d.id));
  for (const el of grid.querySelectorAll('.tile')) {
    const id = el.id.replace('tile-', '');
    if (!ids.has(id)) {
      const st = players.get(id);
      if (st && st.player) st.player.close();
      players.delete(id);
      el.remove();
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

// ---- fullscreen viewer ----
let modalPlayer = null;
function openModal(d) {
  const modal = document.getElementById('modal');
  const video = document.getElementById('modal-video');
  if (modalPlayer) { modalPlayer.close(); modalPlayer = null; }
  document.getElementById('modal-caption').textContent = `${d.name} — ${d.location}`;
  modal.classList.remove('hidden');
  if (d.online) {
    startWhep(video, `${cfg.webrtcBase}/${d.id}/whep`, cfg.readUser, cfg.readPass)
      .then((p) => { modalPlayer = p; }).catch(() => {});
  }
  const close = () => { if (modalPlayer) { modalPlayer.close(); modalPlayer = null; } modal.classList.add('hidden'); };
  document.getElementById('modal-close').onclick = close;
}

// ---- device management ----
function openAddForm() {
  showModal(`
    <h2>Додати вузол</h2>
    <form id="add-form" class="form">
      <label>Device ID <small>(лишіть порожнім для автогенерації, напр. pi-07)</small>
        <input name="id" placeholder="напр. cam-entrance або порожньо" autocomplete="off" />
      </label>
      <label>Назва
        <input name="name" placeholder="напр. Вхідні ворота" required />
      </label>
      <label>Локація
        <input name="location" placeholder="напр. Периметр — Північ" />
      </label>
      <p class="form-err" id="add-err"></p>
      <div class="form-actions">
        <button type="button" data-close class="btn-ghost">Скасувати</button>
        <button type="submit" class="btn-primary">Створити</button>
      </div>
    </form>`);
  document.getElementById('add-form').addEventListener('submit', submitAdd);
}

async function submitAdd(e) {
  e.preventDefault();
  const fd = new FormData(e.target);
  const payload = {
    id: (fd.get('id') || '').trim(),
    name: (fd.get('name') || '').trim(),
    location: (fd.get('location') || '').trim(),
  };
  const errEl = document.getElementById('add-err');
  errEl.textContent = '';
  const res = await fetch('/api/devices', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) { errEl.textContent = body.error || `Помилка ${res.status}`; return; }
  showCreds(body.device, body.push, true);
}

async function viewCreds(id) {
  const res = await fetch(`/api/devices/${encodeURIComponent(id)}/push`);
  if (!res.ok) { alert('Не вдалося отримати креди'); return; }
  const body = await res.json();
  showCreds(body.device, body.push, false);
}

function credRow(label, value) {
  return `<div class="cred-row">
    <div class="cred-label"><span>${label}</span><button type="button" class="copy">копіювати</button></div>
    <pre>${escapeHtml(value)}</pre>
  </div>`;
}

function showCreds(device, push, isNew) {
  showModal(`
    <h2>${isNew ? '✅ Вузол створено' : '🔑 Креди вузла'}: ${escapeHtml(device.id)}</h2>
    <p class="muted">${escapeHtml(device.name || '')}${device.location ? ` · ${escapeHtml(device.location)}` : ''}</p>
    ${credRow('Publish пароль', device.publish_pass)}
    ${credRow('Команда пушу — RTSP', push.rtsp)}
    ${credRow('Команда пушу — SRT', push.srt)}
    <p class="muted small">Налаштуй WireGuard на Pi вручну (як раніше), потім встав цю команду пушу.</p>
    <div class="form-actions"><button type="button" data-close class="btn-primary">Готово</button></div>`);
}

async function deleteDevice(id, name) {
  if (!confirm(`Видалити вузол «${name || id}»? Його потік зупиниться, креди стануть недійсними.`)) return;
  const res = await fetch(`/api/devices/${encodeURIComponent(id)}`, { method: 'DELETE' });
  if (!res.ok) { alert('Помилка видалення'); return; }
  const st = players.get(id);
  if (st && st.player) st.player.close();
  players.delete(id);
  const el = document.getElementById(`tile-${id}`);
  if (el) el.remove();
}

// ---- live updates ----
function connectSSE() {
  const es = new EventSource('/api/stream');
  es.onmessage = (e) => render(JSON.parse(e.data));
  es.onerror = () => { es.close(); setTimeout(connectSSE, 3000); }; // reconnect
}

function fmtBitrate(kbps) { return kbps == null ? '—' : kbps >= 1000 ? `${(kbps / 1000).toFixed(1)} Mbps` : `${kbps} kbps`; }
function fmtUptime(s) { if (s == null) return '—'; const h = Math.floor(s / 3600); const m = Math.floor((s % 3600) / 60); return h ? `${h}год ${m}хв` : `${m}хв`; }
function telemetryLine(t) { const p = []; if (t.rssi != null) p.push(`RSSI ${t.rssi}`); if (t.freq != null) p.push(`${t.freq}`); if (t.alarm) p.push('⚠ ALARM'); return p.join(' · '); }
function escapeHtml(s) { return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

(async function init() {
  cfg = await loadConfig();
  if (!cfg) return;
  const first = await fetch('/api/devices').then((r) => r.json());
  render(first);
  connectSSE();
})();
