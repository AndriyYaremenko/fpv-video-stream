// dashboard/public/telemetry-format.js — pure telemetry formatters + staleness (no DOM; unit-tested).
export const TELEM_STALE_S = 45;

export function isFresh(ts, nowS) { return ts != null && (nowS - ts) < TELEM_STALE_S; }

export function fmtTemp(c) { return c == null ? '—' : `${Number(c).toFixed(1)}°C`; }

export function fmtPctVal(pct) { return pct == null ? '—' : `${pct}%`; }

export function fmtMem(t) {
  if (!t || t.mem_used_pct == null) return '—';
  const gb = t.mem_total_mb != null ? ` (${(t.mem_total_mb / 1024).toFixed(1)}G)` : '';
  return `${t.mem_used_pct}%${gb}`;
}

export function fmtUptimeShort(s) {
  if (s == null) return '—';
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600);
  return d ? `${d}д ${h}г` : `${h}г ${Math.floor((s % 3600) / 60)}хв`;
}

export function throttleState(t) {
  if (!t) return null;
  if (t.throttled) return { text: '🔥 THROTTLED', warn: true };
  if (t.throttled_ever) return { text: '⚠ був throttle', warn: true };
  return null;
}
