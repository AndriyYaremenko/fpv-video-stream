import { detectionKey } from './alert.js';
// dashboard/public/spectrum.js — spectrum panel: pure helpers (unit-tested) + DOM render (browser only).


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

// X pixel for a detection center frequency within an explicit band range (clamped to [0, width]).
export function detectionX(centerMhz, lowMhz, highMhz, width) {
  const frac = (centerMhz - lowMhz) / ((highMhz - lowMhz) || 1);
  return Math.max(0, Math.min(width, frac * width));
}

// Map a dBm value to a CSS color on the spectrum scale (noise = dark blue → strong = red).
export function psdColor(db, dbMin = -100, dbMax = -20) {
  const span = (dbMax - dbMin) || 1;
  const t = Math.max(0, Math.min(1, (db - dbMin) / span));
  const stops = [[2, 2, 17], [23, 118, 102], [42, 170, 102], [255, 221, 51], [255, 51, 51]];
  const seg = t * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(seg));
  const f = seg - i;
  const [r1, g1, b1] = stops[i];
  const [r2, g2, b2] = stops[i + 1];
  const r = Math.round(r1 + (r2 - r1) * f);
  const g = Math.round(g1 + (g2 - g1) * f);
  const b = Math.round(b1 + (b2 - b1) * f);
  return `rgb(${r}, ${g}, ${b})`;
}

// ---- DOM rendering (browser only; validated with `node --check` + manual) ----

// renderSpectrum(container, scanners, store): scanners = registry devices (kind=scanner, for
// name/location/management); store = MqttScanClient store keyed by scanner id (live data).
export function renderSpectrum(container, scanners, store = {}, highlightKeys = new Set()) {
  container.innerHTML = '';
  for (const s of scanners) container.appendChild(scannerBlock(s, store[s.id], highlightKeys));
}

function scannerBlock(s, live, highlightKeys) {
  const block = el('div', 'scan-block');
  block.dataset.scannerId = s.id;
  const online = !!(live && live.online);

  block.appendChild(el('div', 'scan-head', `
    <strong>${escapeHtml(s.name)}</strong> <small>${escapeHtml(s.location || '')}</small>
    <span class="badge ${online ? 'on' : 'off'}">${online ? 'ONLINE' : 'OFFLINE'}</span>
    <span class="scan-actions">
      <button class="tile-btn" data-act="info" title="Інфо">🔑</button>
      <button class="tile-btn" data-act="edit" title="Редагувати">✏️</button>
      <button class="tile-btn" data-act="del" title="Видалити">🗑</button>
    </span>`));

  const bandIds = live ? Object.keys(live.bands) : [];
  if (!online && !bandIds.length) {
    block.appendChild(el('p', 'scan-empty', 'немає даних'));
    return block;
  }

  const det = (live && live.detection) || { detections: [], occupancy: {} };

  // occupancy strip (data-driven over the bands we have)
  const occ = el('div', 'scan-occ');
  for (const band of bandIds) {
    const frac = (det.occupancy && det.occupancy[band]) || 0;
    occ.appendChild(el('div', 'occ-bar', `
      <span class="occ-label">${escapeHtml(band)}</span>
      <span class="occ-track"><span class="occ-fill" style="width:${Math.round(frac * 100)}%"></span></span>
      <span class="occ-val">${fmtPct(frac)}</span>`));
  }
  block.appendChild(occ);

  // 3 bands in a row: each = live PSD line + scrolling waterfall
  const charts = el('div', 'scan-charts');
  for (const band of bandIds) {
    const range = live.bands[band] || {};
    const psd = (live.latestPsd && live.latestPsd[band]) || [];
    const frames = (live.waterfalls && live.waterfalls[band]) || [];
    const dets = (det.detections || []).filter((d) => d.band === band);
    charts.appendChild(bandCell(band, range, psd, frames, dets));
  }
  block.appendChild(charts);

  block.appendChild(detectionTable(det.detections || [], highlightKeys));
  return block;
}

function bandCell(band, range, psd, frames, dets) {
  const wrap = el('div', 'band-cell');
  wrap.appendChild(el('div', 'band-label', escapeHtml(band)));
  const w = 240;

  // live PSD line + detection marks
  const lh = 44;
  const line = document.createElement('canvas');
  line.width = w; line.height = lh; line.className = 'chart-line';
  const lc = line.getContext('2d');
  const pts = psdToPoints(psd, w, lh);
  if (pts.length) {
    lc.strokeStyle = '#6ca0ff'; lc.lineWidth = 1; lc.beginPath();
    pts.forEach((p, i) => (i ? lc.lineTo(p.x, p.y) : lc.moveTo(p.x, p.y)));
    lc.stroke();
  }
  for (const d of dets) {
    const x = detectionX(d.center_mhz, range.low_mhz, range.high_mhz, w);
    lc.strokeStyle = classColor(d.class); lc.lineWidth = 2;
    lc.beginPath(); lc.moveTo(x, 0); lc.lineTo(x, lh); lc.stroke();
  }
  wrap.appendChild(line);

  // waterfall: one pixel row per frame, newest on top
  const rows = frames.length;
  const wf = document.createElement('canvas');
  wf.width = w; wf.height = Math.max(1, rows); wf.className = 'chart-wf';
  const wc = wf.getContext('2d');
  for (let r = 0; r < rows; r += 1) {
    const f = frames[rows - 1 - r];           // newest first
    const p = f.psd || [];
    const n = p.length;
    if (!n) continue;
    for (let x = 0; x < w; x += 1) {
      const idx = n === 1 ? 0 : Math.round((x / (w - 1)) * (n - 1));
      wc.fillStyle = psdColor(p[idx]);
      wc.fillRect(x, r, 1, 1);
    }
  }
  wrap.appendChild(wf);
  return wrap;
}

function el(tag, cls, html) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html != null) e.innerHTML = html;
  return e;
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
