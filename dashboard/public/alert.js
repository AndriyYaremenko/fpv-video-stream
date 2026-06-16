// dashboard/public/alert.js — detection-alert helpers (pure) + Web Audio beep (browser only).

// Stable key for a detection so the same transmitter doesn't re-alert on small freq jitter.
export function detectionKey(d) {
  const band = d.band || '?';
  if (d.channel) return `${band}:${d.channel}`;
  const mhz = Math.round(Number(d.center_mhz) / 5) * 5;
  return `${band}:${mhz}`;
}

// Compare current detections to the previous key set.
// prevKeys null/undefined => baseline (no "new"); else newKeys = keys present now but not before.
export function diffNewKeys(prevKeys, detections) {
  const keys = new Set((detections || []).map(detectionKey));
  if (prevKeys === null || prevKeys === undefined) return { keys, newKeys: [] };
  const newKeys = [...keys].filter((k) => !prevKeys.has(k));
  return { keys, newKeys };
}
