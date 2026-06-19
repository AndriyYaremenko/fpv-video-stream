// dashboard/public/app.js — grid render, WHEP players, device management (add/delete/creds), tile sizing.
import { startWhep } from '/whep.js';
import { splitByKind, renderSpectrum, classColor, fmtFreq } from '/spectrum.js';
import { diffNewKeys, SoundAlerter } from '/alert.js';
import { MqttScanClient } from '/mqtt-scan.js';
import { nearestRxChannel } from '/rx5808-channels.js';

let cfg = null;
const players = new Map(); // id -> { player } | { player: null, starting: true }
const lastById = new Map(); // id -> latest device snapshot (for restart + edit prefill)
const grid = document.getElementById('grid');
const spectrumPanel = document.getElementById('spectrum-panel');
const alerter = new SoundAlerter();
let prevScanKeys = null;
const scanClient = new MqttScanClient();
let scannersFromRegistry = [];

spectrumPanel.addEventListener('click', (e) => {
  const scanBlock = e.target.closest('[data-scanner-id]');
  const sid = scanBlock ? scanBlock.dataset.scannerId : null;

  // RX5808 mode buttons
  const modeBtn = e.target.closest('[data-rxmode]');
  if (modeBtn && sid) {
    scanClient.publishCommand(sid, { mode: modeBtn.dataset.rxmode });
    return;
  }
  // Click the 5.8 spectrum -> tune the nearest channel (manual)
  const canvas = e.target.closest('canvas.tunable');
  if (canvas && sid) {
    const rect = canvas.getBoundingClientRect();
    const lo = Number(canvas.dataset.lowMhz);
    const hi = Number(canvas.dataset.highMhz);
    const x = Math.min(rect.width, Math.max(0, e.clientX - rect.left));
    const freq = lo + (x / rect.width) * (hi - lo);
    const ch = nearestRxChannel(freq);
    if (ch) scanClient.publishCommand(sid, { mode: 'manual', channel: ch.name });
    return;
  }

  const frame = e.target.closest('.scan-frame');
  if (frame) {
    const cap = frame.parentElement.querySelector('.scan-frame-cap');
    openImageModal(frame.src, cap ? cap.textContent : '');
    return;
  }
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

// RX5808 channel <select> -> tune that channel (manual)
spectrumPanel.addEventListener('change', (e) => {
  const sel = e.target.closest('select.rx5808-ch');
  if (!sel) return;
  const block = sel.closest('[data-scanner-id]');
  if (block) scanClient.publishCommand(block.dataset.scannerId, { mode: 'manual', channel: sel.value });
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
document.getElementById('journal-btn').addEventListener('click', openJournal);
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
  if (e.target.closest('#journal-refresh')) openJournal();
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
  scannersFromRegistry = scanners;
  for (const d of devices) lastById.set(d.id, d);

  document.getElementById('summary').textContent =
    `${cameras.filter((d) => d.online).length}/${cameras.length} онлайн`;

  renderScan();   // draw from the MQTT store (presence/data), using the latest registry metadata

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

function renderScan() {
  const scanners = scannersFromRegistry;
  if (!scanners.length) {
    spectrumPanel.classList.add('hidden');
    spectrumPanel.innerHTML = '';
    return;
  }
  const store = scanClient.store;
  const allDets = scanners.flatMap((s) => (store[s.id] && store[s.id].detection && store[s.id].detection.detections) || []);
  const { keys, newKeys } = diffNewKeys(prevScanKeys, allDets);
  if (prevScanKeys !== null && newKeys.length && alerter.armed) alerter.beep();
  prevScanKeys = Object.keys(store).length ? keys : null;
  spectrumPanel.classList.remove('hidden');
  renderSpectrum(spectrumPanel, scanners, store, new Set(newKeys));
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
  document.getElementById('modal-image').classList.add('hidden');
  video.classList.remove('hidden');
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

// Enlarge a recovered scan frame (a base64 PNG) in the shared modal — swaps the WHEP video for an img.
function openImageModal(src, caption) {
  const modal = document.getElementById('modal');
  const video = document.getElementById('modal-video');
  const img = document.getElementById('modal-image');
  if (modalPlayer) { modalPlayer.close(); modalPlayer = null; }
  video.classList.add('hidden');
  img.src = src;
  img.classList.remove('hidden');
  document.getElementById('modal-caption').textContent = caption || '';
  modal.classList.remove('hidden');
  const close = () => {
    img.classList.add('hidden');
    img.src = '';
    video.classList.remove('hidden');
    modal.classList.add('hidden');
  };
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
    <p class="muted small">Вузол-сканер (HackRF) — не камера, відео не публікує. Дані йдуть у MQTT-брокер.</p>
    ${credRow('SCAN_ID на Pi (= id топіка)', device.id)}
    ${credRow('MQTT-топіки', `fpv/${device.id}/{spectrum,detection,status,video}`)}
    <p class="muted small">На Pi задай SCAN_MQTT_HOST + MQTT_PUB_USER/PASS (див. deploy-доку). SCAN_ID має дорівнювати id вище.</p>
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

// ---- detection journal ----
function journalHtml(events) {
  const rows = (events || []).map((e) => {
    const t = new Date(Number(e.ts) * 1000);
    const p = (n) => String(n).padStart(2, '0');
    const when = `${t.getFullYear()}-${p(t.getMonth() + 1)}-${p(t.getDate())} ${p(t.getHours())}:${p(t.getMinutes())}:${p(t.getSeconds())}`;
    const freq = `${fmtFreq(e.center_mhz)}${e.channel ? ` (${escapeHtml(e.channel)})` : ''}`;
    const ev = e.event === 'gone'
      ? '<span class="jr-gone">зник</span>' : '<span class="jr-app">з\'явився</span>';
    return `<tr>
      <td>${when}</td>
      <td>${escapeHtml(e.scanner_id || '')}</td>
      <td>${escapeHtml(e.band || '')}</td>
      <td>${freq}</td>
      <td><span style="color:${classColor(e.class)}">${escapeHtml(e.class || '')}</span></td>
      <td>${e.snr_db == null ? '—' : escapeHtml(String(e.snr_db))} dB</td>
      <td>${ev}</td></tr>`;
  }).join('');
  const table = (events && events.length)
    ? `<table class="scan-table jr-table"><thead><tr><th>Час</th><th>Сканер</th><th>Бенд</th><th>Частота</th><th>Клас</th><th>SNR</th><th>Подія</th></tr></thead><tbody>${rows}</tbody></table>`
    : '<p class="muted">Журнал порожній.</p>';
  return `<h2>📜 Журнал детекцій <button type="button" id="journal-refresh" class="btn-ghost">оновити</button></h2>
    ${table}
    <div class="form-actions"><button type="button" data-close class="btn-primary">Закрити</button></div>`;
}

async function openJournal() {
  let events = [];
  try {
    const res = await fetch('/api/detections?limit=200');
    if (res.ok) events = await res.json();
  } catch { /* show empty on failure */ }
  showModal(journalHtml(events));
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
  try {
    const mq = await fetch('/api/mqtt').then((r) => (r.ok ? r.json() : null));
    if (mq && mq.url) scanClient.connect(mq, () => renderScan());
  } catch { /* no broker creds -> scan panel stays empty until available */ }
})();
