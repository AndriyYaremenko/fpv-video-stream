// dashboard/public/thresholds.js — pure helpers for the detection-sensitivity panel.
export const THRESHOLD_FIELDS = [
  { key: 'snr_threshold_db',   label: 'SNR',        lo: 3,   hi: 60, step: 1 },
  { key: 'min_bandwidth_mhz',  label: 'мін BW',     lo: 0.1, hi: 30, step: 0.5 },
  { key: 'occupancy_snr_db',   label: 'occ SNR',    lo: 3,   hi: 40, step: 1 },
  { key: 'carrier_snr_db',     label: 'carrier SNR', lo: 3,  hi: 60, step: 1 },
  { key: 'carrier_min_bw_mhz', label: 'carrier BW', lo: 0.1, hi: 10, step: 0.5 },
];
const _BY = Object.fromEntries(THRESHOLD_FIELDS.map((f) => [f.key, f]));

export function clampThreshold(key, v) {
  const f = _BY[key];
  const n = Number(v);
  if (!f || !Number.isFinite(n)) return null;
  return Math.max(f.lo, Math.min(n, f.hi));
}

// Online scanners that have announced a scancfg — one threshold card each, sorted by id.
export function scannerThresholdCards(store) {
  return Object.keys(store || {})
    .filter((id) => store[id] && store[id].online && store[id].scancfg)
    .sort()
    .map((id) => ({ id, scancfg: store[id].scancfg }));
}
