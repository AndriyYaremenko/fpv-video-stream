// dashboard/public/mqtt-scan.js — MQTT scan subscriber.
// Pure store reducer (unit-tested) + a browser-only WSS client (MqttScanClient).

const DEFAULT_DEPTH = 60;

export function emptyStore() {
  return {};
}

// Build an RX5808 command payload for fpv/<id>/rxcmd.
export function buildCommand(mode, channel) {
  return { mode, channel: channel || null };
}

// Build an SDR view command for fpv/<id>/rxcmd ({view:'start',freq_mhz,bandwidth_mhz?} | {view:'stop'}).
export function buildViewCommand(action, freqMhz, bwMhz) {
  const cmd = { view: action === 'stop' ? 'stop' : 'start' };
  if (cmd.view === 'start') {
    cmd.freq_mhz = Number(freqMhz);
    if (bwMhz != null && bwMhz !== '' && Number.isFinite(Number(bwMhz))) cmd.bandwidth_mhz = Number(bwMhz);
  }
  return cmd;
}

function ensure(store, id) {
  if (!store[id]) {
    store[id] = { online: false, status_ts: 0, detection: null, video: null, rxtune: null, view: null, telemetry: null, bands: {}, latestPsd: {}, waterfalls: {} };
  }
  return store[id];
}

// Apply one MQTT message to the store and return it. topic must be `fpv/<id>/<kind>`.
// payload may be a JSON string or an already-parsed object. Pure + safe on bad input.
export function reduce(store, topic, payload, opts = {}) {
  const depth = opts.depth || DEFAULT_DEPTH;
  const m = /^fpv\/([^/]+)\/(spectrum|detection|status|video|rxtune|view|telemetry)$/.exec(topic || '');
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
  } else if (kind === 'video') {
    s.video = {
      ts: data.ts || 0,
      center_mhz: data.center_mhz,
      standard: data.standard,
      line_hz: data.line_hz,
      sync_snr_db: data.sync_snr_db,
      frame_png_b64: data.frame_png_b64 || '',
    };
  } else if (kind === 'rxtune') {
    s.rxtune = {
      ts: data.ts || 0,
      freq_mhz: data.freq_mhz,
      channel: data.channel,
      mode: data.mode,
      targets: data.targets || [],
    };
  } else if (kind === 'view') {
    s.view = {
      ts: data.ts || 0,
      active: !!data.active,
      freq_mhz: data.freq_mhz == null ? null : Number(data.freq_mhz),
      until_ts: data.until_ts == null ? null : Number(data.until_ts),
      error: data.error || null,
      stream: data.stream || null,
      bandwidth_mhz: data.bandwidth_mhz == null ? null : Number(data.bandwidth_mhz),
    };
  } else if (kind === 'telemetry') {
    s.telemetry = {
      ts: data.ts || 0,
      cpu_temp_c: data.cpu_temp_c ?? null,
      cpu_load_pct: data.cpu_load_pct ?? null,
      mem_used_mb: data.mem_used_mb ?? null,
      mem_total_mb: data.mem_total_mb ?? null,
      mem_used_pct: data.mem_used_pct ?? null,
      disk_used_pct: data.disk_used_pct ?? null,
      uptime_s: data.uptime_s ?? null,
      throttled: data.throttled ?? null,
      throttled_ever: data.throttled_ever ?? null,
      throttle_flags: data.throttle_flags ?? null,
    };
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

// ---- browser-only WSS client (not unit-tested; validated with node --check + manual) ----
// Loads the vendored `mqtt` global (window.mqtt from vendor/mqtt.min.js). Reduces each message
// into the store and notifies on an animation frame. Reconnect handled by mqtt.js.
export class MqttScanClient {
  constructor(depth = DEFAULT_DEPTH) {
    this.store = emptyStore();
    this.depth = depth;
    this.client = null;
  }

  connect({ url, user, pass }, onChange) {
    if (!url || typeof window === 'undefined' || !window.mqtt) return;
    const client = window.mqtt.connect(url, { username: user, password: pass, reconnectPeriod: 4000 });
    let raf = 0;
    const notify = () => { raf = 0; onChange(this.store); };
    client.on('connect', () => client.subscribe(['fpv/+/spectrum', 'fpv/+/detection', 'fpv/+/status', 'fpv/+/video', 'fpv/+/rxtune', 'fpv/+/view', 'fpv/+/telemetry']));
    client.on('message', (topic, buf) => {
      try { reduce(this.store, topic, buf.toString(), { depth: this.depth }); } catch { return; }
      if (!raf) raf = requestAnimationFrame(notify);
    });
    this.client = client;
  }

  // Publish an RX5808 command to fpv/<id>/rxcmd (retained) over the existing WSS connection.
  publishCommand(id, cmd) {
    if (!this.client || !id || !cmd || !cmd.mode) return;
    this.client.publish(
      `fpv/${id}/rxcmd`, JSON.stringify(buildCommand(cmd.mode, cmd.channel)),
      { qos: 1, retain: true },
    );
  }

  // SDR view command — same rxcmd topic (ACL already allows it), but NOT retained:
  // a retained start would replay and re-enter view mode on every Pi reconnect.
  publishView(id, action, freqMhz, bwMhz) {
    if (!this.client || !id) return;
    if (action === 'start' && !Number.isFinite(Number(freqMhz))) return;
    this.client.publish(
      `fpv/${id}/rxcmd`, JSON.stringify(buildViewCommand(action, freqMhz, bwMhz)),
      { qos: 1, retain: false },
    );
  }
}
