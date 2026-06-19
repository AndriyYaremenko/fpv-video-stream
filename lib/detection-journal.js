import { readFileSync, writeFileSync } from 'node:fs';

// Stable detection key — mirror of dashboard/public/alert.js detectionKey.
function detectionKey(d) {
  const band = d.band || '?';
  if (d.channel) return `${band}:${d.channel}`;
  const mhz = Math.round(Number(d.center_mhz) / 5) * 5;
  return `${band}:${mhz}`;
}

function makeEvent(ts, scannerId, event, d) {
  return {
    ts, scanner_id: scannerId, event,
    band: d.band, center_mhz: d.center_mhz, channel: d.channel || null,
    class: d.class, snr_db: d.snr_db, power_dbm: d.power_dbm,
  };
}

// Pure: diff a detection payload against the previous key->detection map for one scanner.
// isBaseline (first message) yields no events. Returns { events, current }.
export function diffDetections(prevByKey, scannerId, payload, isBaseline) {
  const dets = (payload && payload.detections) || [];
  const ts = (payload && payload.ts) || 0;
  const current = new Map();
  for (const d of dets) current.set(detectionKey(d), d);
  const events = [];
  if (!isBaseline) {
    for (const [k, d] of current) {
      if (!prevByKey.has(k)) events.push(makeEvent(ts, scannerId, 'appeared', d));
    }
    for (const [k, d] of prevByKey) {
      if (!current.has(k)) events.push(makeEvent(ts, scannerId, 'gone', d));
    }
  }
  return { events, current };
}

export class DetectionJournal {
  constructor({ file = '', max = 2000 } = {}) {
    this._file = file;
    this._max = max;
    this._events = [];                 // oldest..newest
    this._byScanner = new Map();       // scannerId -> Map(key -> detection)
    this._seen = new Set();            // scanners that have a baseline
    this._load();
  }

  ingest(scannerId, payload) {
    const isBaseline = !this._seen.has(scannerId);
    this._seen.add(scannerId);
    const prev = this._byScanner.get(scannerId) || new Map();
    const { events, current } = diffDetections(prev, scannerId, payload, isBaseline);
    this._byScanner.set(scannerId, current);
    if (events.length) {
      this._events.push(...events);
      if (this._events.length > this._max) this._events.splice(0, this._events.length - this._max);
      this._persist();
    }
    return events;
  }

  events(limit = 200) {
    const n = this._events.length;
    return this._events.slice(Math.max(0, n - limit)).reverse();   // newest first
  }

  _load() {
    if (!this._file) return;
    try {
      const arr = JSON.parse(readFileSync(this._file, 'utf8'));
      if (Array.isArray(arr)) this._events = arr.slice(-this._max);
    } catch { /* no file yet — start empty */ }
  }

  _persist() {
    if (!this._file) return;
    try { writeFileSync(this._file, JSON.stringify(this._events), 'utf8'); } catch { /* best-effort */ }
  }
}
