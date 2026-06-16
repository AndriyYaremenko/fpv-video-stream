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
