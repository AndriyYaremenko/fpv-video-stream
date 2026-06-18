// dashboard/public/mqtt-scan.js — MQTT scan subscriber.
// Pure store reducer (unit-tested) + a browser-only WSS client (MqttScanClient).

const DEFAULT_DEPTH = 60;

export function emptyStore() {
  return {};
}

function ensure(store, id) {
  if (!store[id]) {
    store[id] = { online: false, status_ts: 0, detection: null, bands: {}, latestPsd: {}, waterfalls: {} };
  }
  return store[id];
}

// Apply one MQTT message to the store and return it. topic must be `fpv/<id>/<kind>`.
// payload may be a JSON string or an already-parsed object. Pure + safe on bad input.
export function reduce(store, topic, payload, opts = {}) {
  const depth = opts.depth || DEFAULT_DEPTH;
  const m = /^fpv\/([^/]+)\/(spectrum|detection|status)$/.exec(topic || '');
  if (!m) return store;
  const [, id, kind] = m;
  let data;
  try { data = typeof payload === 'string' ? JSON.parse(payload) : payload; } catch { return store; }
  if (!data || typeof data !== 'object') return store;
  const s = ensure(store, id);
  if (kind === 'status') {
    s.online = !!data.online;
    s.status_ts = data.ts || 0;
  } else if (kind === 'detection') {
    s.detection = { ts: data.ts || 0, detections: data.detections || [], occupancy: data.occupancy || {} };
  } else if (kind === 'spectrum') {
    for (const b of (data.bands || [])) {
      if (!b || b.id == null) continue;
      s.bands[b.id] = { low_mhz: b.low_mhz, high_mhz: b.high_mhz };
      s.latestPsd[b.id] = b.psd || [];
      const buf = s.waterfalls[b.id] || (s.waterfalls[b.id] = []);
      buf.push({ ts: data.ts || 0, psd: b.psd || [] });
      if (buf.length > depth) buf.splice(0, buf.length - depth);
    }
  }
  return store;
}
