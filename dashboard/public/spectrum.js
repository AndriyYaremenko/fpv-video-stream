import { detectionKey } from '/alert.js';
// dashboard/public/spectrum.js — spectrum panel: pure helpers (unit-tested) + DOM render (browser only).

export const BAND_RANGES = {
  '1.2G': [1080, 1360],
  '2.4G': [2370, 2510],
  '5.8G': [5645, 5945],
};

export function splitByKind(devices) {
  const cameras = [];
  const scanners = [];
  for (const d of devices) {
    if (d.kind === 'scanner') scanners.push(d);
    else cameras.push(d);
  }
  return { cameras, scanners };
}

export function classColor(cls) {
  if (cls === 'analog') return '#3ddc84';
  if (cls === 'digital') return '#f4b740';
  return '#9aa0a6'; // unknown / anything else
}

export function fmtFreq(mhz) {
  return `${Number(mhz).toFixed(0)} МГц`;
}

export function fmtPct(fraction) {
  return `${Math.round((Number(fraction) || 0) * 100)}%`;
}

// Map a PSD array (dBm) to polyline points in a w×h box. Higher power = higher on screen (smaller y).
export function psdToPoints(psd, width, height, dbMin = -100, dbMax = -20) {
  const n = psd.length;
  if (n === 0) return [];
  const span = (dbMax - dbMin) || 1;
  return psd.map((db, i) => {
    const x = n === 1 ? 0 : (i / (n - 1)) * width;
    const clamped = Math.max(dbMin, Math.min(dbMax, db));
    const y = height - ((clamped - dbMin) / span) * height;
    return { x, y };
  });
}

// X pixel for a detection center frequency within a band's range (clamped to [0, width]).
export function detectionX(centerMhz, band, width) {
  const range = BAND_RANGES[band];
  if (!range) return 0;
  const [lo, hi] = range;
  const frac = (centerMhz - lo) / ((hi - lo) || 1);
  return Math.max(0, Math.min(width, frac * width));
}

// ---- DOM rendering (browser only; not unit-tested, validated with `node --check` + manual) ----

export function renderSpectrum(container, scanners, highlightKeys = new Set()) {
  container.innerHTML = '';
  for (const s of scanners) container.appendChild(scannerBlock(s, highlightKeys));
}

function el(tag, cls, html) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html != null) e.innerHTML = html;
  return e;
}

function scannerBlock(s, highlightKeys) {
  const tel = s.telemetry || {};
  const block = el('div', 'scan-block');
  block.dataset.scannerId = s.id;

  const head = el('div', 'scan-head', `
    <strong>${escapeHtml(s.name)}</strong> <small>${escapeHtml(s.location || '')}</small>
    <span class="badge ${s.online ? 'on' : 'off'}">${s.online ? 'ONLINE' : 'OFFLINE'}</span>
    <span class="scan-actions">
      <button class="tile-btn" data-act="info" title="Інфо телеметрії">🔑</button>
      <button class="tile-btn" data-act="edit" title="Редагувати">✏️</button>
      <button class="tile-btn" data-act="del" title="Видалити">🗑</button>
    </span>`);
  block.appendChild(head);

  if (!s.online || !tel.detections) {
    block.appendChild(el('p', 'scan-empty', 'немає даних'));
    return block;
  }

  const occ = el('div', 'scan-occ');
  for (const band of Object.keys(BAND_RANGES)) {
    const frac = (tel.occupancy && tel.occupancy[band]) || 0;
    occ.appendChild(el('div', 'occ-bar', `
      <span class="occ-label">${band}</span>
      <span class="occ-track"><span class="occ-fill" style="width:${Math.round(frac * 100)}%"></span></span>
      <span class="occ-val">${fmtPct(frac)}</span>`));
  }
  block.appendChild(occ);

  const charts = el('div', 'scan-charts');
  for (const band of Object.keys(BAND_RANGES)) {
    const psd = (tel.spectrum && tel.spectrum[band]) || [];
    const dets = (tel.detections || []).filter((d) => d.band === band);
    charts.appendChild(bandChart(band, psd, dets));
  }
  block.appendChild(charts);

  block.appendChild(detectionTable(tel.detections || [], highlightKeys));
  return block;
}

function bandChart(band, psd, dets) {
  const wrap = el('div', 'band-chart');
  wrap.appendChild(el('div', 'band-label', band));
  const w = 240;
  const h = 60;
  const canvas = document.createElement('canvas');
  canvas.width = w;
  canvas.height = h;
  canvas.className = 'chart-canvas';
  const ctx = canvas.getContext('2d');
  const pts = psdToPoints(psd, w, h);
  if (pts.length) {
    ctx.strokeStyle = '#6ca0ff';
    ctx.lineWidth = 1;
    ctx.beginPath();
    pts.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
    ctx.stroke();
  }
  for (const d of dets) {
    const x = detectionX(d.center_mhz, band, w);
    ctx.strokeStyle = classColor(d.class);
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, h);
    ctx.stroke();
  }
  wrap.appendChild(canvas);
  return wrap;
}

function detectionTable(dets, highlightKeys = new Set()) {
  if (!dets.length) return el('p', 'scan-empty', 'немає активних передавачів');
  const sorted = [...dets].sort((a, b) => (b.power_dbm ?? -999) - (a.power_dbm ?? -999));
  const table = el('table', 'scan-table',
    '<thead><tr><th></th><th>Бенд</th><th>Частота</th><th>Клас</th><th>RSSI</th><th>Смуга</th><th>Впевн.</th></tr></thead>');
  const tb = el('tbody');
  for (const d of sorted) {
    const isNew = highlightKeys.has(detectionKey(d));
    const tr = el('tr', isNew ? 'is-new' : null);
    const freq = `${fmtFreq(d.center_mhz)}${d.channel ? ` (${escapeHtml(d.channel)})` : ''}`;
    tr.innerHTML = `
      <td>${isNew ? '⚠' : ''}</td>
      <td>${escapeHtml(d.band)}</td>
      <td>${freq}</td>
      <td><span class="cls" style="color:${classColor(d.class)}">${escapeHtml(d.class)}</span></td>
      <td>${d.power_dbm == null ? '—' : escapeHtml(String(d.power_dbm))} dBm</td>
      <td>${d.bandwidth_mhz == null ? '—' : escapeHtml(String(d.bandwidth_mhz))} МГц</td>
      <td>${fmtPct(d.confidence)}</td>`;
    tb.appendChild(tr);
  }
  table.appendChild(tb);
  return table;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
