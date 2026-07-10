import { detectionKey } from './alert.js';
import { RX5808_CHANNELS } from './rx5808-channels.js';
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

// Caption for a recovered video frame: "PAL · 5800 МГц · SNR 18.3 dB · 12:03:20".
export function frameCaption(v) {
  if (!v) return '';
  const parts = [];
  if (v.standard) parts.push(String(v.standard));
  if (v.center_mhz != null) parts.push(fmtFreq(v.center_mhz));
  if (v.sync_snr_db != null) parts.push(`SNR ${Number(v.sync_snr_db).toFixed(1)} dB`);
  if (v.ts) {
    const d = new Date(Number(v.ts) * 1000);
    const p = (n) => String(n).padStart(2, '0');
    parts.push(`${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`);
  }
  return parts.join(' · ');
}

// Caption for the current RX5808 tune: "RX5808 → 5865 МГц (A1) · scan".
export function rxtuneCaption(rx) {
  if (!rx || rx.freq_mhz == null) return '';
  const ch = rx.channel ? ` (${rx.channel})` : '';
  const mode = rx.mode ? ` · ${rx.mode}` : '';
  return `RX5808 → ${fmtFreq(rx.freq_mhz)}${ch}${mode}`;
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

// ---- mini live spectrum (PSD line + marks only; NO waterfall) ----
export function renderMiniSpectrum(canvas, { psd = [], range = {}, dets = [], rxFreq = null, tunable = false }) {
  const w = canvas.width, h = canvas.height, c = canvas.getContext('2d');
  c.clearRect(0, 0, w, h);
  const pts = psdToPoints(psd, w, h);
  if (pts.length) { c.strokeStyle = '#00e5ff'; c.lineWidth = 1; c.beginPath();
    pts.forEach((p, i) => (i ? c.lineTo(p.x, p.y) : c.moveTo(p.x, p.y))); c.stroke(); }
  for (const d of dets) { const x = detectionX(d.center_mhz, range.low_mhz, range.high_mhz, w);
    c.strokeStyle = classColor(d.class); c.lineWidth = 2; c.beginPath(); c.moveTo(x, 0); c.lineTo(x, h); c.stroke(); }
  if (rxFreq != null && range.low_mhz != null && rxFreq >= range.low_mhz && rxFreq <= range.high_mhz) {
    const x = detectionX(rxFreq, range.low_mhz, range.high_mhz, w);
    c.strokeStyle = '#39d0ff'; c.lineWidth = 2; c.beginPath(); c.moveTo(x, 0); c.lineTo(x, h); c.stroke(); }
  if (tunable) canvas.classList.add('tunable');
}
