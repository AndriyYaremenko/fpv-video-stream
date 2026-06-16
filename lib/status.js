export function mergeStatus(reg, pathsList, nowMs) {
  const byName = new Map();
  for (const item of (pathsList?.items || [])) byName.set(item.name, item);

  return (reg.devices || []).map((d) => {
    const item = byName.get(d.id);
    const online = !!(item && item.ready);
    let uptimeSec = null;
    if (online && item.readyTime) {
      const t = Date.parse(item.readyTime);
      if (!Number.isNaN(t)) uptimeSec = Math.max(0, Math.round((nowMs - t) / 1000));
    }
    return {
      id: d.id,
      name: d.name || d.id,
      location: d.location || '',
      kind: d.kind || 'camera',
      online,
      readers: online ? (item.readers?.length ?? 0) : 0,
      bytesReceived: online ? (item.bytesReceived ?? 0) : 0,
      uptimeSec,
    };
  });
}

export function computeBitrateKbps(prevBytes, prevMs, curBytes, curMs) {
  if (prevBytes == null || prevMs == null) return null;
  const dt = curMs - prevMs;
  if (dt <= 0) return null;
  const dBytes = curBytes - prevBytes;
  if (dBytes < 0) return null;
  return Math.round((dBytes * 8) / dt);
}
