// dashboard/public/app.js — grid render, WHEP players, device management (add/delete/creds), tile sizing.
import { startWhep } from '/whep.js';
import { splitByKind, renderSpectrum } from '/spectrum.js';
import { diffNewKeys, SoundAlerter } from '/alert.js';

let cfg = null;
const players = new Map(); // id -> { player } | { player: null, starting: true }
const lastById = new Map(); // id -> latest device snapshot (for restart + edit prefill)
const grid = document.getElementById('grid');
const spectrumPanel = document.getElementById('spectrum-panel');
const alerter = new SoundAlerter();
let prevScanKeys = null;

spectrumPanel.addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-act]');
  if (!btn) return;
  const block = btn.closest('[data-scanner-id]');
  if (!block) return;
  const id = block.dataset.scannerId;
  const act = btn.dataset.act;
  if (act === 'edit') openEditForm(id);
  else if (act === 'del') deleteScanner(id);
  else if (act === 'info') scannerInfoModal(lastById.get(id) || { id, name: id, location: '' }, false);
});

// ---- sound-alert toggle ----
const soundBtn = document.getElementById('sound-toggle');
function setSoundUI() {
  soundBtn.textContent = alerter.armed ? '🔔' : '🔕';
  soundBtn.classList.toggle('armed', alerter.armed);
  soundBtn.title = alerter.armed ? 'Звук сповіщень: увімкнено' : 'Звук сповіщень: вимкнено';
}
function setArmed(on) {
  if (on) alerter.arm(); else alerter.disarm();
  localStorage.setItem('soundArmed', on ? '1' : '0');
  setSoundUI();
}
soundBtn.addEventListener('click', () => setArmed(!alerter.armed));
// Restore preference; autoplay needs a user gesture, so if previously armed, arm on first interaction.
if (localStorage.getItem('soundArmed') === '1') {
  const resume = () => { alerter.arm(); setSoundUI(); document.removeEventListener('pointerdown', resume); };
  document.addEventListener('pointerdown', resume, { once: true });
  soundBtn.textContent = '🔔';
  soundBtn.classList.add('armed');
} else {
  setSoundUI();
}

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
document.getElementById('restart-all').addEventListener('click', restartAll);

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
          <button class="tile-btn act-restart" title="Перезапустити перегляд">🔄</button>
          <button class="tile-btn act-creds" title="Креди / команда пушу">🔑</button>
          <button class="tile-btn act-edit" title="Редагувати">✏️</button>
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
  el.querySelector('.act-restart').addEventListener('click', (e) => { e.stopPropagation(); restartTile(d.id); });
  el.querySelector('.act-creds').addEventListener('click', (e) => { e.stopPropagation(); viewCreds(d.id); });
  el.querySelector('.act-edit').addEventListener('click', (e) => { e.stopPropagation(); openEditForm(d.id); });
  el.querySelector('.act-del').addEventListener('click', (e) => { e.stopPropagation(); deleteDevice(d.id, d.name); });
  grid.appendChild(el);
  return el;
}

function render(devices) {
  const { cameras, scanners } = splitByKind(devices);
  for (const d of devices) lastById.set(d.id, d);

  document.getElementById('summary').textContent =
    `${cameras.filter((d) => d.online).length}/${cameras.length} онлайн`;

  const allDets = scanners.flatMap((s) => (s.telemetry && s.telemetry.detections) || []);
  const { keys, newKeys } = diffNewKeys(prevScanKeys, allDets);
  if (prevScanKeys !== null && newKeys.length && alerter.armed) alerter.beep();
  prevScanKeys = keys;
  renderSpectrumPanel(scanners, new Set(newKeys));

  for (const d of cameras) {
    const el = tileEl(d);
    el.querySelector('.tile-meta strong').textContent = d.name;     // reflect edits
    el.querySelector('.tile-meta small').textContent = d.location;
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

  // Drop tiles for cameras that no longer exist (e.g. deleted or converted).
  const ids = new Set(cameras.map((d) => d.id));
  for (const el of grid.querySelectorAll('.tile')) {
    const id = el.id.replace('tile-', '');
    if (!ids.has(id)) {
      const st = players.get(id);
      if (st && st.player) st.player.close();
      players.delete(id);
      lastById.delete(id);
      el.remove();
    }
  }
}

function renderSpectrumPanel(scanners, highlightKeys = new Set()) {
  if (!scanners.length) {
    spectrumPanel.classList.add('hidden');
    spectrumPanel.innerHTML = '';
    return;
  }
  spectrumPanel.classList.remove('hidden');
  renderSpectrum(spectrumPanel, scanners, highlightKeys);
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

// Restart the live WebRTC playback for a tile (tear down + re-establish). Does not touch ingest.
function restartTile(id) {
  const st = players.get(id);
  if (st && st.player) st.player.close();
  players.delete(id);
  const video = document.getElementById(`vid-${id}`);
  if (video) video.srcObject = null;
  const d = lastById.get(id);
  if (d && d.online && d.kind !== 'scanner') startPlayer(d); // re-establish now; otherwise the next tick will
}

function restartAll() {
  for (const id of [...lastById.keys()]) restartTile(id);
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
      <label>Тип
        <select name="kind">
          <option value="camera">Камера</option>
          <option value="scanner">Сканер (HackRF)</option>
        </select>
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
    kind: fd.get('kind') || 'camera',
  };
  const errEl = document.getElementById('add-err');
  errEl.textContent = '';
  const res = await fetch('/api/devices', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) { errEl.textContent = body.error || `Помилка ${res.status}`; return; }
  if (body.scanner) scannerInfoModal(body.device, true);
  else showCreds(body.device, body.push, true);
}

function openEditForm(id) {
  const d = lastById.get(id) || { id, name: '', location: '' };
  showModal(`
    <h2>Редагувати вузол: ${escapeHtml(id)}</h2>
    <form id="edit-form" class="form">
      <label>Назва
        <input name="name" value="${escapeHtml(d.name || '')}" required />
      </label>
      <label>Локація
        <input name="location" value="${escapeHtml(d.location || '')}" />
      </label>
      <p class="muted small">ID та пароль не змінюються. Щоб змінити ID — видали вузол і створи новий.</p>
      <p class="form-err" id="edit-err"></p>
      <div class="form-actions">
        <button type="button" data-close class="btn-ghost">Скасувати</button>
        <button type="submit" class="btn-primary">Зберегти</button>
      </div>
    </form>`);
  document.getElementById('edit-form').addEventListener('submit', (e) => submitEdit(e, id));
}

async function submitEdit(e, id) {
  e.preventDefault();
  const fd = new FormData(e.target);
  const payload = { name: (fd.get('name') || '').trim(), location: (fd.get('location') || '').trim() };
  const errEl = document.getElementById('edit-err');
  errEl.textContent = '';
  const res = await fetch(`/api/devices/${encodeURIComponent(id)}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  });
  if (!res.ok) { const b = await res.json().catch(() => ({})); errEl.textContent = b.error || `Помилка ${res.status}`; return; }
  const body = await res.json();
  const el = document.getElementById(`tile-${id}`);
  if (el) {
    el.querySelector('.tile-meta strong').textContent = body.device.name;
    el.querySelector('.tile-meta small').textContent = body.device.location;
  }
  const cur = lastById.get(id);
  if (cur) { cur.name = body.device.name; cur.location = body.device.location; }
  hideModal();
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

function scannerInfoModal(device, isNew) {
  showModal(`
    <h2>${isNew ? '✅ Сканер створено' : '📡 Сканер'}: ${escapeHtml(device.id)}</h2>
    <p class="muted">${escapeHtml(device.name || '')}${device.location ? ` · ${escapeHtml(device.location)}` : ''}</p>
    <p class="muted small">Вузол-сканер (HackRF) — не камера, відео не публікує.</p>
    ${credRow('SCAN_ID на Pi', device.id)}
    ${credRow('Ендпойнт телеметрії', `/api/telemetry/${device.id}`)}
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

async function deleteScanner(id) {
  const d = lastById.get(id) || { name: id };
  if (!confirm(`Видалити сканер «${d.name || id}»?`)) return;
  const res = await fetch(`/api/devices/${encodeURIComponent(id)}`, { method: 'DELETE' });
  if (!res.ok) { alert('Помилка видалення'); return; }
  lastById.delete(id);
  // panel re-renders without this scanner on the next SSE tick
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
