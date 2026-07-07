// dashboard/public/viewer.js — «FPV Viewer»: merged multiband detection list.
// Pure state/list helpers (unit-tested) + list HTML builder + DOM render (browser only).
import { detectionKey } from './alert.js';

export const RECENT_TTL_S = 300;      // dimmed-but-clickable window after a signal disappears
export const LIVE_STALE_S = 120;      // a scanner claim older than this no longer counts as live

export function emptyViewer() {
  return { entries: {}, seenTs: {} };  // entries: key -> entry; seenTs: scannerId -> last applied payload ts
}

function entryLive(e, nowS) {
  return Object.values(e.scanners).some((ts) => nowS - ts <= LIVE_STALE_S);
}

function prune(vs, nowS) {
  for (const [k, e] of Object.entries(vs.entries)) {
    if (!entryLive(e, nowS) && nowS - e.last_seen > RECENT_TTL_S) delete vs.entries[k];
  }
}

// Apply one scanner's fpv/<id>/detection payload ({ts, detections:[...]}) — idempotent per ts.
export function applyDetections(vs, scannerId, det, nowS) {
  if (!det || !Array.isArray(det.detections)) return vs;
  if (vs.seenTs[scannerId] === det.ts) return vs;
  vs.seenTs[scannerId] = det.ts;
  const ts = Number(det.ts) || nowS;
  for (const d of det.detections) {
    const key = detectionKey(d);
    const e = vs.entries[key] || (vs.entries[key] = { key, scanners: {}, seen_by: {}, last_seen: 0 });
    e.scanners[scannerId] = ts;
    e.seen_by[scannerId] = true;
    e.last_seen = Math.max(e.last_seen, ts);
    // freshest report wins the display fields
    e.band = d.band;
    e.center_mhz = d.center_mhz;
    e.channel = d.channel || null;
    e.class = d.class;
    e.snr_db = d.snr_db == null ? null : d.snr_db;
    e.power_dbm = d.power_dbm == null ? null : d.power_dbm;
  }
  // whatever this scanner did NOT report this cycle, it no longer sees
  for (const e of Object.values(vs.entries)) {
    if (e.scanners[scannerId] !== undefined && e.scanners[scannerId] !== ts) delete e.scanners[scannerId];
  }
  prune(vs, nowS);
  return vs;
}

// Seed «recent» rows from the detection journal (GET /api/detections, newest-first)
// so they survive a page reload. Live rows re-arrive via retained MQTT anyway.
export function seedFromJournal(vs, events, nowS) {
  for (const ev of events || []) {
    if (ev.event !== 'gone' || nowS - ev.ts > RECENT_TTL_S) continue;
    const key = detectionKey(ev);
    if (vs.entries[key]) continue;                    // never overwrite live/newer state
    vs.entries[key] = {
      key, scanners: {}, seen_by: { [ev.scanner_id]: true }, last_seen: ev.ts,
      band: ev.band, center_mhz: ev.center_mhz, channel: ev.channel || null,
      class: ev.class, snr_db: ev.snr_db == null ? null : ev.snr_db,
      power_dbm: ev.power_dbm == null ? null : ev.power_dbm,
    };
  }
  return vs;
}

// Rows for rendering: live first (strongest on top), then recent (freshest on top).
export function viewerRows(vs, nowS) {
  prune(vs, nowS);
  const rows = Object.values(vs.entries).map((e) => ({ ...e, live: entryLive(e, nowS) }));
  rows.sort((a, b) => {
    if (a.live !== b.live) return a.live ? -1 : 1;
    if (a.live) return (b.power_dbm ?? -999) - (a.power_dbm ?? -999);
    return b.last_seen - a.last_seen;
  });
  return rows;
}

// The scanner to send view commands to: online + announced view capability; idle preferred.
export function pickViewer(store) {
  const ids = Object.keys(store || {}).filter((id) => store[id] && store[id].online && store[id].view);
  if (!ids.length) return null;
  return ids.find((id) => !store[id].view.active) || ids[0];
}

// The scanner driving a physical RX5808 (for the 5.8G dual action).
export function pickRxScanner(store) {
  const ids = Object.keys(store || {}).filter((id) => store[id] && store[id].online && store[id].rxtune);
  return ids.length ? ids[0] : null;
}

export function viewStream(store, id) {
  const v = store && store[id] && store[id].view;
  return (v && v.stream) || `${id}-view`;
}

export function ageLabel(nowS, ts) {
  const s = Math.max(0, nowS - ts);
  if (s < 60) return 'щойно';
  return `${Math.round(s / 60)} хв тому`;
}
