import express from 'express';
import cookieSession from 'cookie-session';
import { writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { mergeStatus, computeBitrateKbps } from '../lib/status.js';
import { addDevice, removeDevice, nextDeviceId, saveRegistry, loadRegistry, ensureReadUser } from '../lib/registry.js';
import { renderConfig } from '../lib/render-config.js';
import { buildRtspPush, buildSrtPush } from '../lib/push-command.js';
import { fetchPaths } from '../lib/mtx-api.js';

const __dirname = dirname(fileURLToPath(import.meta.url));

export function createApp({ registry, getPaths, config }) {
  const app = express();
  app.use(express.json());
  app.use(express.urlencoded({ extended: false }));
  app.use(cookieSession({
    name: 'fpv', secret: config.sessionSecret,
    httpOnly: true, sameSite: 'lax', maxAge: 12 * 60 * 60 * 1000,
  }));

  // In-memory state: last telemetry payload + last byte sample per device (for bitrate).
  const telemetry = new Map();
  const samples = new Map();

  const requireAuth = (req, res, next) => {
    if (req.session?.authed) return next();
    return res.status(401).json({ error: 'auth required' });
  };

  // ---- auth ----
  app.post('/login', (req, res) => {
    const { user, pass } = req.body || {};
    if (user === config.dashUser && pass === config.dashPass) {
      req.session.authed = true;
      if ((req.get('accept') || '').includes('text/html')) return res.redirect('/');
      return res.json({ ok: true });
    }
    if ((req.get('accept') || '').includes('text/html')) {
      return res.status(401).redirect('/login.html?error=1');
    }
    return res.status(401).json({ error: 'invalid credentials' });
  });
  app.post('/logout', (req, res) => { req.session = null; res.json({ ok: true }); });

  // ---- telemetry hook (called by Pi over WG; optional bearer token) ----
  app.post('/api/telemetry/:id', (req, res) => {
    if (config.telemetryToken && req.get('authorization') !== `Bearer ${config.telemetryToken}`) {
      return res.status(401).json({ error: 'bad token' });
    }
    // Only accept telemetry for known devices — bounds the Map and rejects typos/spam.
    if (!(registry.devices || []).some((d) => d.id === req.params.id)) {
      return res.status(404).json({ error: 'unknown device' });
    }
    telemetry.set(req.params.id, { ...req.body, _ts: Date.now() });
    res.json({ ok: true });
  });

  // ---- status snapshot ----
  async function snapshot() {
    const paths = await getPaths();
    const now = Date.now();
    const merged = mergeStatus(registry, paths, now);
    for (const d of merged) {
      const prev = samples.get(d.id);
      d.bitrateKbps = d.online ? computeBitrateKbps(prev?.bytes, prev?.ts, d.bytesReceived, now) : null;
      if (d.online) samples.set(d.id, { bytes: d.bytesReceived, ts: now });
      d.telemetry = telemetry.get(d.id) || null;
    }
    return merged;
  }

  app.get('/api/config', requireAuth, (req, res) => {
    res.json({ webrtcBase: config.webrtcBase, readUser: config.readUser, readPass: config.readPass });
  });

  app.get('/api/devices', requireAuth, async (req, res) => {
    try {
      res.json(await snapshot());
    } catch (err) {
      res.status(500).json({ error: 'snapshot failed' });
    }
  });

  // ---- device management: create / fetch creds / delete ----
  // config.persistRegistry(reg) writes devices.yml AND regenerates mediamtx.yml;
  // MediaMTX hot-reloads the config file, so no restart is needed.
  const pushFor = (device) => ({
    rtsp: buildRtspPush(device, config.pushOpts),
    srt: buildSrtPush(device, config.pushOpts),
  });

  app.post('/api/devices', requireAuth, (req, res) => {
    const { id, name, location } = req.body || {};
    try {
      const finalId = (id && String(id).trim()) ? String(id).trim() : nextDeviceId(registry);
      const device = addDevice(registry, { id: finalId, name, location });
      config.persistRegistry(registry);
      res.status(201).json({ device, push: pushFor(device) });
    } catch (e) {
      const code = /already exists/.test(e.message) ? 409 : 400;
      res.status(code).json({ error: e.message });
    }
  });

  app.get('/api/devices/:id/push', requireAuth, (req, res) => {
    const device = (registry.devices || []).find((d) => d.id === req.params.id);
    if (!device) return res.status(404).json({ error: 'unknown device' });
    res.json({ device, push: pushFor(device) });
  });

  app.delete('/api/devices/:id', requireAuth, (req, res) => {
    try {
      removeDevice(registry, req.params.id);
      telemetry.delete(req.params.id);
      samples.delete(req.params.id);
      config.persistRegistry(registry);
      res.json({ ok: true });
    } catch (e) {
      res.status(404).json({ error: e.message });
    }
  });

  // ---- SSE stream of status diffs ----
  app.get('/api/stream', requireAuth, (req, res) => {
    res.set({ 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', Connection: 'keep-alive' });
    res.flushHeaders?.();
    let alive = true;
    const tick = async () => {
      if (!alive) return;
      try { res.write(`data: ${JSON.stringify(await snapshot())}\n\n`); } catch { alive = false; }
    };
    tick();
    const timer = setInterval(tick, config.pollIntervalMs || 2000);
    req.on('close', () => { alive = false; clearInterval(timer); });
  });

  // ---- static + gated index ----
  app.use(express.static(join(__dirname, 'public')));
  app.get('/', (req, res) => {
    if (!req.session?.authed) return res.redirect('/login.html');
    res.sendFile(join(__dirname, 'public', 'index.html'));
  });

  return app;
}

// ---- production entrypoint ----
export async function start() {
  const env = process.env;
  const devicesFile = env.DEVICES_FILE || 'devices.yml';
  const mediamtxConfig = env.MEDIAMTX_CONFIG || 'mediamtx.yml';
  const registry = loadRegistry(devicesFile);
  ensureReadUser(registry);
  const apiBase = env.MTX_API_BASE || 'http://127.0.0.1:9997';
  const renderOpts = {
    wgIp: env.WG_IP || '10.8.0.1',
    rtspPort: Number(env.RTSP_PORT || 8554),
    srtPort: Number(env.SRT_PORT || 8890),
    webrtcPort: Number(env.WEBRTC_PORT || 8889),
    iceUdpPort: Number(env.ICE_UDP_PORT || 8189),
    apiHost: env.API_HOST || '127.0.0.1',
    apiPort: Number(env.API_PORT || 9997),
  };
  const config = {
    dashUser: env.DASH_USER || 'operator',
    dashPass: env.DASH_PASS || 'change-me-now',
    sessionSecret: env.SESSION_SECRET || 'insecure-dev-secret',
    webrtcBase: `http://${env.WG_IP || '10.8.0.1'}:${env.WEBRTC_PORT || 8889}`,
    readUser: registry.read_user,
    readPass: registry.read_pass,
    telemetryToken: env.TELEMETRY_TOKEN || '',
    pollIntervalMs: Number(env.POLL_INTERVAL_MS || 2000),
    pushOpts: {
      wgIp: renderOpts.wgIp,
      rtspPort: renderOpts.rtspPort,
      srtPort: renderOpts.srtPort,
      videoDevice: env.PI_VIDEO_DEVICE || '/dev/video0',
      framerate: Number(env.PI_FRAMERATE || 30),
      videoSize: env.PI_VIDEO_SIZE || '720x576',
      bitrate: env.PI_BITRATE || '2M',
    },
    // Persist registry + regenerate MediaMTX config (MediaMTX hot-reloads the file).
    persistRegistry: (reg) => {
      saveRegistry(devicesFile, reg);
      writeFileSync(mediamtxConfig, renderConfig(reg, renderOpts), 'utf8');
    },
  };
  const app = createApp({ registry, getPaths: () => fetchPaths(apiBase), config });
  const host = env.DASH_HOST || '10.8.0.1';
  const port = Number(env.DASH_PORT || 8080);
  app.listen(port, host, () => console.log(`Dashboard on http://${host}:${port}`));
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) start();
