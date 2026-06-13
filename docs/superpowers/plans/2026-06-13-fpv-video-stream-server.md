# FPV Video Stream Server — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy the server side of a multi-node video surveillance system: a MediaMTX media server ingesting H.264 from Raspberry Pi nodes over WireGuard, plus a Node/Express dashboard showing all streams in a live WebRTC grid with online/offline status.

**Architecture:** MediaMTX runs as a systemd service, bound to the WG interface for ingest/playback and to localhost for its control API. A device registry (`devices.yml`) is the single source of truth; a Node generator renders `mediamtx.yml` (per-device publish auth via `authInternalUsers`) from it, so adding a node never requires hand-editing config. The Express dashboard serves a static grid, polls the MediaMTX control API read-only to derive status, and the browser pulls video directly from MediaMTX over WHEP. Everything is reachable only over WireGuard.

**Tech Stack:** MediaMTX (latest), Node 18+ (developed on Node 22), Express, js-yaml, cookie-session, vanilla JS + native WebRTC/WHEP, systemd, bash. Tests use the built-in `node --test` runner with `node:assert/strict`.

---

## File Structure

```
package.json                 # root Node package (ESM), deps + test/start scripts
.gitignore                   # already exists; extend
.env.example                 # all tunable params + dashboard login (no real secrets)
devices.example.yml          # 2 placeholder devices + read login (committed)
lib/
  registry.js                # load/save devices.yml, addDevice, genSecret, validation
  render-config.js           # buildConfigObject + renderConfig(registry, opts) -> YAML
  status.js                  # mergeStatus(registry, pathsList, now), computeBitrateKbps
  mtx-api.js                 # fetchPaths(apiBase, fetchImpl) -> {items:[]}
  push-command.js            # buildRtspPush / buildSrtPush -> exact ffmpeg command
bin/
  gen-mediamtx.js            # CLI: devices.yml + env -> mediamtx.yml
  add-device.js              # CLI: add device, regen config, reload, print push cmd
dashboard/
  server.js                  # createApp({registry,getPaths,config}) + start()
  public/
    index.html               # grid shell
    login.html               # login form
    app.js                   # status polling (SSE), tile rendering, fullscreen
    whep.js                  # WHEP WebRTC client (non-trickle)
    styles.css               # responsive grid styling
test/
  registry.test.js
  render-config.test.js
  status.test.js
  mtx-api.test.js
  push-command.test.js
  server.test.js
systemd/
  mediamtx.service
  fpv-dashboard.service
install.sh                   # idempotent, parameterized installer (Ubuntu)
add-device.sh                # thin wrapper -> node bin/add-device.js
README.md
```

**Conventions for every code step:** files are ESM (`package.json` has `"type": "module"`). Tests run with `node --test`. Commit after each task with the shown message.

---

## Task 1: Project scaffold

**Files:**
- Create: `package.json`
- Create: `.env.example`
- Create: `devices.example.yml`
- Modify: `.gitignore`

- [ ] **Step 1: Write `package.json`**

```json
{
  "name": "fpv-video-stream",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "description": "Server side for multi-node FPV video surveillance (MediaMTX + dashboard)",
  "scripts": {
    "test": "node --test",
    "gen-config": "node bin/gen-mediamtx.js",
    "add-device": "node bin/add-device.js",
    "start": "node dashboard/server.js"
  },
  "engines": { "node": ">=18" },
  "dependencies": {
    "cookie-session": "^2.1.0",
    "express": "^4.19.2",
    "js-yaml": "^4.1.0"
  }
}
```

- [ ] **Step 2: Write `.env.example`** (copied to `.env` by install.sh; `.env` is git-ignored)

```bash
# ---- WireGuard / network ----
WG_IP=10.8.0.1
WG_IFACE=wg0

# ---- MediaMTX ports (bound to WG_IP except API which is localhost) ----
RTSP_PORT=8554
SRT_PORT=8890
WEBRTC_PORT=8889
ICE_UDP_PORT=8189
API_HOST=127.0.0.1
API_PORT=9997
MTX_API_BASE=http://127.0.0.1:9997

# ---- Dashboard ----
DASH_HOST=10.8.0.1
DASH_PORT=8080
DASH_USER=operator
DASH_PASS=change-me-now
SESSION_SECRET=replace-with-long-random-string
POLL_INTERVAL_MS=2000

# ---- Telemetry hook (optional; leave token empty to accept unauthenticated POSTs over WG) ----
TELEMETRY_TOKEN=

# ---- Paths ----
DEVICES_FILE=devices.yml
MEDIAMTX_CONFIG=/usr/local/etc/mediamtx.yml

# ---- Default Pi capture params (used when printing push commands) ----
PI_VIDEO_DEVICE=/dev/video0
PI_FRAMERATE=30
PI_VIDEO_SIZE=720x576
PI_BITRATE=2M
```

- [ ] **Step 3: Write `devices.example.yml`**

```yaml
# Device registry — single source of truth.
# Copy to devices.yml (git-ignored) for real secrets. `id` is the RTSP path AND publish username.
read_user: viewer
read_pass: CHANGE_ME_READ_PASS
devices:
  - id: pi-01
    name: "Front Gate"
    location: "Perimeter — North"
    publish_pass: CHANGE_ME_PI01
  - id: pi-02
    name: "Yard"
    location: "Perimeter — East"
    publish_pass: CHANGE_ME_PI02
```

- [ ] **Step 4: Extend `.gitignore`** (replace file contents)

```gitignore
# secrets / runtime
devices.yml
.env
mediamtx.yml
node_modules/
*.log
```

- [ ] **Step 5: Install deps and verify the test runner works**

Run:
```bash
npm install
node --test
```
Expected: `npm install` completes; `node --test` reports `tests 0` (no test files yet) and exits 0.

- [ ] **Step 6: Commit**

```bash
git add package.json package-lock.json .env.example devices.example.yml .gitignore
git commit -m "chore: project scaffold, env example, device registry seed"
```

---

## Task 2: Device registry module (`lib/registry.js`)

**Files:**
- Create: `lib/registry.js`
- Test: `test/registry.test.js`

- [ ] **Step 1: Write the failing test**

```js
// test/registry.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { genSecret, addDevice, ensureReadUser, validateId } from '../lib/registry.js';

test('genSecret returns a long url-safe token', () => {
  const s = genSecret();
  assert.ok(s.length >= 24);
  assert.match(s, /^[A-Za-z0-9_-]+$/);
  assert.notEqual(genSecret(), genSecret());
});

test('validateId accepts valid ids and rejects bad ones', () => {
  assert.equal(validateId('pi-01'), true);
  assert.equal(validateId('cam_2'), true);
  assert.equal(validateId('UPPER'), false);     // no uppercase
  assert.equal(validateId('has space'), false);
  assert.equal(validateId('-leading'), false);
  assert.equal(validateId(''), false);
});

test('addDevice appends a device with a generated publish_pass', () => {
  const reg = { read_user: 'viewer', read_pass: 'x', devices: [] };
  const d = addDevice(reg, { id: 'pi-09', name: 'Shed', location: 'West' });
  assert.equal(d.id, 'pi-09');
  assert.equal(d.name, 'Shed');
  assert.ok(d.publish_pass.length >= 24);
  assert.equal(reg.devices.length, 1);
});

test('addDevice rejects duplicate id', () => {
  const reg = { devices: [{ id: 'pi-01', name: 'a', location: 'b', publish_pass: 'p' }] };
  assert.throws(() => addDevice(reg, { id: 'pi-01', name: 'x', location: 'y' }), /exists/);
});

test('addDevice rejects invalid id', () => {
  const reg = { devices: [] };
  assert.throws(() => addDevice(reg, { id: 'Bad Id', name: 'x', location: 'y' }), /invalid/i);
});

test('ensureReadUser fills missing read credentials', () => {
  const reg = { devices: [] };
  ensureReadUser(reg);
  assert.ok(reg.read_user);
  assert.ok(reg.read_pass.length >= 24);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test test/registry.test.js`
Expected: FAIL — `Cannot find module '../lib/registry.js'`.

- [ ] **Step 3: Write `lib/registry.js`**

```js
// lib/registry.js
import { randomBytes } from 'node:crypto';
import { readFileSync, writeFileSync, chmodSync } from 'node:fs';
import yaml from 'js-yaml';

const ID_RE = /^[a-z0-9][a-z0-9_-]{1,30}$/;

export function validateId(id) {
  return typeof id === 'string' && ID_RE.test(id);
}

export function genSecret(bytes = 24) {
  return randomBytes(bytes).toString('base64url');
}

export function loadRegistry(path) {
  const raw = readFileSync(path, 'utf8');
  const reg = yaml.load(raw) || {};
  reg.devices = Array.isArray(reg.devices) ? reg.devices : [];
  return reg;
}

export function saveRegistry(path, reg) {
  const out = yaml.dump(reg, { lineWidth: 120, quotingType: '"' });
  writeFileSync(path, out, 'utf8');
  try { chmodSync(path, 0o600); } catch { /* chmod unsupported (e.g. Windows dev) */ }
}

export function ensureReadUser(reg) {
  if (!reg.read_user) reg.read_user = 'viewer';
  if (!reg.read_pass || reg.read_pass === '' || /^CHANGE_ME/.test(reg.read_pass)) {
    reg.read_pass = genSecret();
  }
  return reg;
}

export function addDevice(reg, { id, name, location }) {
  if (!validateId(id)) {
    throw new Error(`invalid device id "${id}" (use lowercase a-z, 0-9, -, _; start alphanumeric)`);
  }
  reg.devices = reg.devices || [];
  if (reg.devices.some((d) => d.id === id)) {
    throw new Error(`device "${id}" already exists`);
  }
  const device = { id, name: name || id, location: location || '', publish_pass: genSecret() };
  reg.devices.push(device);
  return device;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test test/registry.test.js`
Expected: PASS — all 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add lib/registry.js test/registry.test.js
git commit -m "feat: device registry module (load/save/addDevice/genSecret)"
```

---

## Task 3: MediaMTX config renderer (`lib/render-config.js`)

**Files:**
- Create: `lib/render-config.js`
- Test: `test/render-config.test.js`

- [ ] **Step 1: Write the failing test**

```js
// test/render-config.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import yaml from 'js-yaml';
import { buildConfigObject, renderConfig } from '../lib/render-config.js';

const reg = {
  read_user: 'viewer',
  read_pass: 'readsecret',
  devices: [
    { id: 'pi-01', name: 'A', location: 'x', publish_pass: 'p1' },
    { id: 'pi-02', name: 'B', location: 'y', publish_pass: 'p2' },
  ],
};
const opts = { wgIp: '10.8.0.1', rtspPort: 8554, srtPort: 8890, webrtcPort: 8889, iceUdpPort: 8189, apiHost: '127.0.0.1', apiPort: 9997 };

test('addresses are bound to the WG IP and API to localhost', () => {
  const c = buildConfigObject(reg, opts);
  assert.equal(c.rtspAddress, '10.8.0.1:8554');
  assert.equal(c.srtAddress, '10.8.0.1:8890');
  assert.equal(c.webrtcAddress, '10.8.0.1:8889');
  assert.equal(c.webrtcLocalUDPAddress, ':8189');
  assert.equal(c.apiAddress, '127.0.0.1:9997');
});

test('webrtc advertises only the WG IP as host candidate', () => {
  const c = buildConfigObject(reg, opts);
  assert.equal(c.webrtcIPsFromInterfaces, false);
  assert.deepEqual(c.webrtcAdditionalHosts, ['10.8.0.1']);
});

test('each device gets a publish-only user scoped to its own path', () => {
  const c = buildConfigObject(reg, opts);
  const pi01 = c.authInternalUsers.find((u) => u.user === 'pi-01');
  assert.equal(pi01.pass, 'p1');
  assert.deepEqual(pi01.permissions, [{ action: 'publish', path: 'pi-01' }]);
  // pi-01 cannot publish to pi-02
  const canCrossPublish = pi01.permissions.some((p) => p.path === 'pi-02');
  assert.equal(canCrossPublish, false);
});

test('a single read user can read all paths', () => {
  const c = buildConfigObject(reg, opts);
  const reader = c.authInternalUsers.find((u) => u.user === 'viewer');
  assert.equal(reader.pass, 'readsecret');
  assert.deepEqual(reader.permissions, [{ action: 'read' }]);
});

test('the control API is only usable from localhost', () => {
  const c = buildConfigObject(reg, opts);
  const apiUser = c.authInternalUsers.find((u) => u.permissions.some((p) => p.action === 'api'));
  assert.ok(apiUser.ips.includes('127.0.0.1'));
});

test('uses a single catch-all path (no per-device path entries)', () => {
  const c = buildConfigObject(reg, opts);
  assert.deepEqual(Object.keys(c.paths), ['all_others']);
});

test('renderConfig produces parseable YAML matching the object', () => {
  const text = renderConfig(reg, opts);
  const parsed = yaml.load(text);
  assert.equal(parsed.rtspAddress, '10.8.0.1:8554');
  assert.equal(parsed.authInternalUsers.length, 4); // api + reader + 2 devices
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test test/render-config.test.js`
Expected: FAIL — `Cannot find module '../lib/render-config.js'`.

- [ ] **Step 3: Write `lib/render-config.js`**

```js
// lib/render-config.js
import yaml from 'js-yaml';

const DEFAULTS = {
  wgIp: '10.8.0.1',
  rtspPort: 8554,
  srtPort: 8890,
  webrtcPort: 8889,
  iceUdpPort: 8189,
  apiHost: '127.0.0.1',
  apiPort: 9997,
};

export function buildConfigObject(reg, opts = {}) {
  const o = { ...DEFAULTS, ...opts };
  const devices = reg.devices || [];

  const authInternalUsers = [
    // Control API + pprof reachable only from localhost (the dashboard runs on the same host).
    { user: 'any', pass: '', ips: ['127.0.0.1', '::1'], permissions: [{ action: 'api' }, { action: 'pprof' }] },
    // Single reader login used by browser WHEP players (read on all paths).
    { user: reg.read_user, pass: reg.read_pass, ips: [], permissions: [{ action: 'read' }] },
    // One publish-only user per device, scoped to that device's path => per-device isolation.
    ...devices.map((d) => ({
      user: d.id,
      pass: d.publish_pass,
      ips: [],
      permissions: [{ action: 'publish', path: d.id }],
    })),
  ];

  return {
    logLevel: 'info',
    logDestinations: ['stdout'], // journald captures stdout under systemd
    readTimeout: '10s',
    writeTimeout: '10s',

    api: true,
    apiAddress: `${o.apiHost}:${o.apiPort}`,
    metrics: false,
    pprof: false,
    playback: false,

    rtsp: true,
    rtspAddress: `${o.wgIp}:${o.rtspPort}`,
    rtspTransports: ['tcp', 'udp'],
    rtspEncryption: 'no',

    rtmp: false,
    hls: false,

    webrtc: true,
    webrtcAddress: `${o.wgIp}:${o.webrtcPort}`,
    webrtcEncryption: 'no',
    webrtcLocalUDPAddress: `:${o.iceUdpPort}`,
    webrtcLocalTCPAddress: '',
    webrtcIPsFromInterfaces: false,
    webrtcAdditionalHosts: [o.wgIp],
    webrtcICEServers2: [], // readers share the tunnel subnet; no STUN/TURN needed

    srt: true,
    srtAddress: `${o.wgIp}:${o.srtPort}`,

    authMethod: 'internal',
    authInternalUsers,

    pathDefaults: {
      source: 'publisher',
    },
    paths: {
      all_others: {}, // catch-all; auth scoping above provides per-device isolation
    },
  };
}

export function renderConfig(reg, opts = {}) {
  const header = '# GENERATED FILE — do not edit by hand.\n'
    + '# Source of truth: devices.yml. Regenerate with: node bin/gen-mediamtx.js\n';
  return header + yaml.dump(buildConfigObject(reg, opts), { lineWidth: 120, quotingType: '"' });
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test test/render-config.test.js`
Expected: PASS — all 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add lib/render-config.js test/render-config.test.js
git commit -m "feat: render mediamtx.yml from registry with per-device publish auth"
```

---

## Task 4: Status merge + bitrate (`lib/status.js`)

**Files:**
- Create: `lib/status.js`
- Test: `test/status.test.js`

The MediaMTX control API `GET /v3/paths/list` returns `{ items: [ { name, ready, readyTime, bytesReceived, readers:[...] } ] }`. `mergeStatus` joins that with the registry so the dashboard shows expected-but-offline devices too.

- [ ] **Step 1: Write the failing test**

```js
// test/status.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mergeStatus, computeBitrateKbps } from '../lib/status.js';

const reg = {
  devices: [
    { id: 'pi-01', name: 'A', location: 'x' },
    { id: 'pi-02', name: 'B', location: 'y' }, // expected but offline
  ],
};
const now = Date.parse('2026-06-13T12:00:10Z');
const pathsList = {
  items: [
    { name: 'pi-01', ready: true, readyTime: '2026-06-13T12:00:00Z', bytesReceived: 1000, readers: [{}, {}] },
    { name: 'ghost', ready: true, readyTime: '2026-06-13T12:00:00Z', bytesReceived: 5, readers: [] }, // not in registry
  ],
};

test('online device is marked online with readers and uptime', () => {
  const out = mergeStatus(reg, pathsList, now);
  const pi01 = out.find((d) => d.id === 'pi-01');
  assert.equal(pi01.online, true);
  assert.equal(pi01.readers, 2);
  assert.equal(pi01.bytesReceived, 1000);
  assert.equal(pi01.uptimeSec, 10);
});

test('expected device with no path is offline', () => {
  const out = mergeStatus(reg, pathsList, now);
  const pi02 = out.find((d) => d.id === 'pi-02');
  assert.equal(pi02.online, false);
  assert.equal(pi02.uptimeSec, null);
});

test('result preserves registry order and excludes unknown paths', () => {
  const out = mergeStatus(reg, pathsList, now);
  assert.deepEqual(out.map((d) => d.id), ['pi-01', 'pi-02']);
});

test('computeBitrateKbps divides byte delta by time delta', () => {
  // 125000 bytes over 1000 ms = 1000 kbps
  assert.equal(computeBitrateKbps(0, 0, 125000, 1000), 1000);
  assert.equal(computeBitrateKbps(null, null, 100, 1000), null); // no previous sample
  assert.equal(computeBitrateKbps(100, 1000, 100, 1000), null);  // no time delta
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test test/status.test.js`
Expected: FAIL — `Cannot find module '../lib/status.js'`.

- [ ] **Step 3: Write `lib/status.js`**

```js
// lib/status.js

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
  if (dBytes < 0) return null; // counter reset
  return Math.round((dBytes * 8) / dt); // bits per ms == kbps
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test test/status.test.js`
Expected: PASS — all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add lib/status.js test/status.test.js
git commit -m "feat: merge registry with MediaMTX paths for online/offline + bitrate"
```

---

## Task 5: MediaMTX API client (`lib/mtx-api.js`)

**Files:**
- Create: `lib/mtx-api.js`
- Test: `test/mtx-api.test.js`

`fetchPaths` is injected with a `fetchImpl` so it is testable without a live server and degrades to `{ items: [] }` on any error (dashboard must survive MediaMTX being down).

- [ ] **Step 1: Write the failing test**

```js
// test/mtx-api.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fetchPaths } from '../lib/mtx-api.js';

test('fetchPaths returns parsed items on success', async () => {
  const fakeFetch = async (url) => {
    assert.equal(url, 'http://127.0.0.1:9997/v3/paths/list');
    return { ok: true, json: async () => ({ items: [{ name: 'pi-01' }] }) };
  };
  const res = await fetchPaths('http://127.0.0.1:9997', fakeFetch);
  assert.equal(res.items[0].name, 'pi-01');
});

test('fetchPaths returns empty items when the API errors', async () => {
  const fakeFetch = async () => { throw new Error('ECONNREFUSED'); };
  const res = await fetchPaths('http://127.0.0.1:9997', fakeFetch);
  assert.deepEqual(res, { items: [] });
});

test('fetchPaths returns empty items on non-ok response', async () => {
  const fakeFetch = async () => ({ ok: false, status: 500, json: async () => ({}) });
  const res = await fetchPaths('http://127.0.0.1:9997', fakeFetch);
  assert.deepEqual(res, { items: [] });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test test/mtx-api.test.js`
Expected: FAIL — `Cannot find module '../lib/mtx-api.js'`.

- [ ] **Step 3: Write `lib/mtx-api.js`**

```js
// lib/mtx-api.js

export async function fetchPaths(apiBase, fetchImpl = globalThis.fetch) {
  try {
    const res = await fetchImpl(`${apiBase}/v3/paths/list`);
    if (!res.ok) return { items: [] };
    const data = await res.json();
    return { items: Array.isArray(data.items) ? data.items : [] };
  } catch {
    return { items: [] };
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test test/mtx-api.test.js`
Expected: PASS — all 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add lib/mtx-api.js test/mtx-api.test.js
git commit -m "feat: resilient MediaMTX control-API client"
```

---

## Task 6: Pi push-command builder (`lib/push-command.js`)

**Files:**
- Create: `lib/push-command.js`
- Test: `test/push-command.test.js`

Pi 5 has no hardware H.264 encoder, so software x264 (`libx264 -preset ultrafast -tune zerolatency`).

- [ ] **Step 1: Write the failing test**

```js
// test/push-command.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildRtspPush, buildSrtPush } from '../lib/push-command.js';

const device = { id: 'pi-01', publish_pass: 's3cr3t' };
const opts = { wgIp: '10.8.0.1', rtspPort: 8554, srtPort: 8890, videoDevice: '/dev/video0', framerate: 30, videoSize: '720x576', bitrate: '2M' };

test('rtsp push targets the device path with its credentials', () => {
  const cmd = buildRtspPush(device, opts);
  assert.match(cmd, /rtsp:\/\/pi-01:s3cr3t@10\.8\.0\.1:8554\/pi-01/);
  assert.match(cmd, /-c:v libx264/);
  assert.match(cmd, /-tune zerolatency/);
  assert.match(cmd, /-rtsp_transport tcp/);
  assert.match(cmd, /-i \/dev\/video0/);
});

test('srt push uses the standard streamid with s= for the password', () => {
  const cmd = buildSrtPush(device, opts);
  assert.match(cmd, /streamid=#!::m=publish,r=pi-01,u=pi-01,s=s3cr3t/);
  assert.match(cmd, /-f mpegts/);
  assert.match(cmd, /srt:\/\/10\.8\.0\.1:8890/);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test test/push-command.test.js`
Expected: FAIL — `Cannot find module '../lib/push-command.js'`.

- [ ] **Step 3: Write `lib/push-command.js`**

```js
// lib/push-command.js

function encoderArgs(o) {
  return [
    '-c:v libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p',
    `-g ${o.framerate} -b:v ${o.bitrate} -maxrate ${o.bitrate} -bufsize ${o.bitrate}`,
  ].join(' ');
}

function inputArgs(o) {
  return `-f v4l2 -framerate ${o.framerate} -video_size ${o.videoSize} -i ${o.videoDevice}`;
}

export function buildRtspPush(device, o) {
  const url = `rtsp://${device.id}:${device.publish_pass}@${o.wgIp}:${o.rtspPort}/${device.id}`;
  return [
    'ffmpeg', inputArgs(o), encoderArgs(o),
    '-f rtsp -rtsp_transport tcp', `"${url}"`,
  ].join(' ');
}

export function buildSrtPush(device, o) {
  const streamid = `#!::m=publish,r=${device.id},u=${device.id},s=${device.publish_pass}`;
  const url = `srt://${o.wgIp}:${o.srtPort}?streamid=${streamid}&latency=200000&pkt_size=1316`;
  return [
    'ffmpeg', inputArgs(o), encoderArgs(o),
    '-f mpegts', `"${url}"`,
  ].join(' ');
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test test/push-command.test.js`
Expected: PASS — both tests pass.

- [ ] **Step 5: Commit**

```bash
git add lib/push-command.js test/push-command.test.js
git commit -m "feat: ffmpeg RTSP/SRT push-command builder for Pi 5"
```

---

## Task 7: Config generator CLI (`bin/gen-mediamtx.js`)

**Files:**
- Create: `bin/gen-mediamtx.js`

This wires `loadRegistry` + `renderConfig` and writes the output file. It reads tunables from environment variables (loaded from `.env` by the caller / systemd).

- [ ] **Step 1: Write `bin/gen-mediamtx.js`**

```js
#!/usr/bin/env node
// bin/gen-mediamtx.js — render mediamtx.yml from the device registry.
import { writeFileSync } from 'node:fs';
import { loadRegistry, ensureReadUser, saveRegistry } from '../lib/registry.js';
import { renderConfig } from '../lib/render-config.js';

const env = process.env;
const devicesFile = env.DEVICES_FILE || 'devices.yml';
const outFile = env.MEDIAMTX_CONFIG || 'mediamtx.yml';

const reg = loadRegistry(devicesFile);
// Backfill read creds if the operator left placeholders; persist so they're stable.
const before = reg.read_pass;
ensureReadUser(reg);
if (reg.read_pass !== before) saveRegistry(devicesFile, reg);

const opts = {
  wgIp: env.WG_IP || '10.8.0.1',
  rtspPort: Number(env.RTSP_PORT || 8554),
  srtPort: Number(env.SRT_PORT || 8890),
  webrtcPort: Number(env.WEBRTC_PORT || 8889),
  iceUdpPort: Number(env.ICE_UDP_PORT || 8189),
  apiHost: env.API_HOST || '127.0.0.1',
  apiPort: Number(env.API_PORT || 9997),
};

writeFileSync(outFile, renderConfig(reg, opts), 'utf8');
console.log(`Wrote ${outFile} (${reg.devices.length} device(s)) bound to ${opts.wgIp}`);
```

- [ ] **Step 2: Smoke-test the generator against the example registry**

Run:
```bash
DEVICES_FILE=devices.example.yml MEDIAMTX_CONFIG=/tmp/mtx-test.yml node bin/gen-mediamtx.js
node -e "import('js-yaml').then(async ({default:y})=>{const fs=await import('node:fs');const c=y.load(fs.readFileSync('/tmp/mtx-test.yml','utf8'));console.log('users',c.authInternalUsers.length,'rtsp',c.rtspAddress)})"
```
Expected: prints `Wrote /tmp/mtx-test.yml (2 device(s)) bound to 10.8.0.1` then `users 4 rtsp 10.8.0.1:8554`.

(Windows dev note: use `$env:DEVICES_FILE='devices.example.yml'; $env:MEDIAMTX_CONFIG="$env:TEMP\mtx-test.yml"; node bin/gen-mediamtx.js` in PowerShell.)

- [ ] **Step 3: Commit**

```bash
git add bin/gen-mediamtx.js
git commit -m "feat: gen-mediamtx CLI renders config from registry"
```

---

## Task 8: Dashboard server (`dashboard/server.js`)

**Files:**
- Create: `dashboard/server.js`
- Test: `test/server.test.js`

`createApp({ registry, getPaths, config })` builds the Express app with injected dependencies (so tests pass a fake `getPaths`). `start()` wires real deps and listens. Auth is a signed cookie session for a single operator login.

- [ ] **Step 1: Write the failing integration test**

```js
// test/server.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createApp } from '../dashboard/server.js';

const registry = {
  read_user: 'viewer', read_pass: 'rpw',
  devices: [{ id: 'pi-01', name: 'A', location: 'x', publish_pass: 'p1' }],
};
const config = {
  dashUser: 'op', dashPass: 'pw', sessionSecret: 'test-secret',
  webrtcBase: 'http://10.8.0.1:8889', readUser: 'viewer', readPass: 'rpw',
  telemetryToken: '',
};
const fakePaths = async () => ({ items: [{ name: 'pi-01', ready: true, readyTime: '2026-06-13T12:00:00Z', bytesReceived: 10, readers: [] }] });

async function startServer() {
  const app = createApp({ registry, getPaths: fakePaths, config });
  const server = await new Promise((res) => { const s = app.listen(0, '127.0.0.1', () => res(s)); });
  const base = `http://127.0.0.1:${server.address().port}`;
  return { server, base };
}

test('GET /api/devices requires auth', async () => {
  const { server, base } = await startServer();
  const res = await fetch(`${base}/api/devices`, { redirect: 'manual' });
  assert.equal(res.status, 401);
  server.close();
});

test('login then fetch devices returns merged status', async () => {
  const { server, base } = await startServer();
  const login = await fetch(`${base}/login`, {
    method: 'POST', redirect: 'manual',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: 'user=op&pass=pw',
  });
  // cookie-session sets two cookies (value + signature); send both back.
  const cookie = login.headers.getSetCookie().map((c) => c.split(';')[0]).join('; ');
  const res = await fetch(`${base}/api/devices`, { headers: { cookie } });
  const body = await res.json();
  assert.equal(res.status, 200);
  assert.equal(body[0].id, 'pi-01');
  assert.equal(body[0].online, true);
  server.close();
});

test('bad login is rejected', async () => {
  const { server, base } = await startServer();
  const login = await fetch(`${base}/login`, {
    method: 'POST', redirect: 'manual',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: 'user=op&pass=WRONG',
  });
  assert.equal(login.status, 401);
  server.close();
});

test('authed /api/config exposes the read creds for WHEP', async () => {
  const { server, base } = await startServer();
  const login = await fetch(`${base}/login`, {
    method: 'POST', redirect: 'manual',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: 'user=op&pass=pw',
  });
  const cookie = login.headers.getSetCookie().map((c) => c.split(';')[0]).join('; ');
  const res = await fetch(`${base}/api/config`, { headers: { cookie } });
  const body = await res.json();
  assert.equal(body.webrtcBase, 'http://10.8.0.1:8889');
  assert.equal(body.readUser, 'viewer');
  assert.equal(body.readPass, 'rpw');
  server.close();
});

test('telemetry hook stores last value and surfaces it on devices', async () => {
  const { server, base } = await startServer();
  await fetch(`${base}/api/telemetry/pi-01`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rssi: -60, alarm: false }),
  });
  const login = await fetch(`${base}/login`, {
    method: 'POST', redirect: 'manual',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: 'user=op&pass=pw',
  });
  const cookie = login.headers.getSetCookie().map((c) => c.split(';')[0]).join('; ');
  const res = await fetch(`${base}/api/devices`, { headers: { cookie } });
  const body = await res.json();
  assert.equal(body[0].telemetry.rssi, -60);
  server.close();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test test/server.test.js`
Expected: FAIL — `Cannot find module '../dashboard/server.js'`.

- [ ] **Step 3: Write `dashboard/server.js`**

```js
// dashboard/server.js
import express from 'express';
import cookieSession from 'cookie-session';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { mergeStatus, computeBitrateKbps } from '../lib/status.js';

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
      // Browser form posts land here; API/test clients read the JSON/cookie.
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
    res.json(await snapshot());
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
  const { loadRegistry, ensureReadUser } = await import('../lib/registry.js');
  const { fetchPaths } = await import('../lib/mtx-api.js');
  const env = process.env;
  const registry = loadRegistry(env.DEVICES_FILE || 'devices.yml');
  ensureReadUser(registry);
  const apiBase = env.MTX_API_BASE || 'http://127.0.0.1:9997';
  const config = {
    dashUser: env.DASH_USER || 'operator',
    dashPass: env.DASH_PASS || 'change-me-now',
    sessionSecret: env.SESSION_SECRET || 'insecure-dev-secret',
    webrtcBase: `http://${env.WG_IP || '10.8.0.1'}:${env.WEBRTC_PORT || 8889}`,
    readUser: registry.read_user,
    readPass: registry.read_pass,
    telemetryToken: env.TELEMETRY_TOKEN || '',
    pollIntervalMs: Number(env.POLL_INTERVAL_MS || 2000),
  };
  const app = createApp({ registry, getPaths: () => fetchPaths(apiBase), config });
  const host = env.DASH_HOST || '10.8.0.1';
  const port = Number(env.DASH_PORT || 8080);
  app.listen(port, host, () => console.log(`Dashboard on http://${host}:${port}`));
}

if (import.meta.url === `file://${process.argv[1]}`) start();
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test test/server.test.js`
Expected: PASS — all 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add dashboard/server.js test/server.test.js
git commit -m "feat: dashboard server (login, /api/devices, SSE, telemetry stub)"
```

---

## Task 9: Frontend (`dashboard/public/`)

**Files:**
- Create: `dashboard/public/index.html`
- Create: `dashboard/public/login.html`
- Create: `dashboard/public/styles.css`
- Create: `dashboard/public/whep.js`
- Create: `dashboard/public/app.js`

No unit tests (browser/WebRTC); verified by `node --check` on the JS and manual browser testing on the server. Keep it dependency-free vanilla JS.

- [ ] **Step 1: Write `dashboard/public/login.html`**

```html
<!doctype html>
<html lang="uk">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>FPV — Вхід</title>
  <link rel="stylesheet" href="/styles.css" />
</head>
<body class="login-page">
  <form class="login-card" method="POST" action="/login">
    <h1>FPV Відеоспостереження</h1>
    <input name="user" placeholder="Логін" autocomplete="username" required />
    <input name="pass" type="password" placeholder="Пароль" autocomplete="current-password" required />
    <button type="submit">Увійти</button>
    <p class="err" id="err"></p>
  </form>
  <script>
    if (new URLSearchParams(location.search).has('error')) {
      document.getElementById('err').textContent = 'Невірний логін або пароль';
    }
  </script>
</body>
</html>
```

- [ ] **Step 2: Write `dashboard/public/index.html`**

```html
<!doctype html>
<html lang="uk">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>FPV — Дашборд</title>
  <link rel="stylesheet" href="/styles.css" />
</head>
<body>
  <header class="topbar">
    <h1>FPV Відеоспостереження</h1>
    <span id="summary" class="summary"></span>
    <button id="logout">Вийти</button>
  </header>
  <main id="grid" class="grid" aria-live="polite"></main>
  <div id="modal" class="modal hidden">
    <button id="modal-close" class="modal-close">✕</button>
    <video id="modal-video" autoplay playsinline muted></video>
    <div id="modal-caption" class="modal-caption"></div>
  </div>
  <script type="module" src="/whep.js"></script>
  <script type="module" src="/app.js"></script>
</body>
</html>
```

- [ ] **Step 3: Write `dashboard/public/whep.js`**

```js
// dashboard/public/whep.js — minimal non-trickle WHEP reader.
export async function startWhep(video, whepUrl, user, pass) {
  const pc = new RTCPeerConnection({ iceServers: [] });
  pc.addTransceiver('video', { direction: 'recvonly' });
  pc.addTransceiver('audio', { direction: 'recvonly' });
  const stream = new MediaStream();
  pc.ontrack = (e) => { stream.addTrack(e.track); video.srcObject = stream; };

  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);
  await iceGatheringComplete(pc, 2000);

  const res = await fetch(whepUrl, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/sdp',
      Authorization: 'Basic ' + btoa(`${user}:${pass}`),
    },
    body: pc.localDescription.sdp,
  });
  if (!res.ok) { pc.close(); throw new Error(`WHEP ${res.status}`); }
  const answer = await res.text();
  await pc.setRemoteDescription({ type: 'answer', sdp: answer });
  return { close: () => { pc.close(); video.srcObject = null; } };
}

function iceGatheringComplete(pc, timeoutMs) {
  if (pc.iceGatheringState === 'complete') return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => { pc.removeEventListener('icegatheringstatechange', check); resolve(); };
    const check = () => { if (pc.iceGatheringState === 'complete') done(); };
    pc.addEventListener('icegatheringstatechange', check);
    setTimeout(done, timeoutMs);
  });
}
```

- [ ] **Step 4: Write `dashboard/public/app.js`**

```js
// dashboard/public/app.js — render the grid, manage WHEP players, handle online/offline diffs.
import { startWhep } from '/whep.js';

let cfg = null;
const players = new Map(); // id -> { player, online }
const grid = document.getElementById('grid');

document.getElementById('logout').addEventListener('click', async () => {
  await fetch('/logout', { method: 'POST' });
  location.href = '/login.html';
});

async function loadConfig() {
  const res = await fetch('/api/config');
  if (res.status === 401) { location.href = '/login.html'; return null; }
  return res.json();
}

function tileEl(d) {
  let el = document.getElementById(`tile-${d.id}`);
  if (el) return el;
  el = document.createElement('section');
  el.id = `tile-${d.id}`;
  el.className = 'tile';
  el.innerHTML = `
    <video id="vid-${d.id}" autoplay playsinline muted></video>
    <div class="tile-overlay">
      <span class="badge" id="badge-${d.id}"></span>
      <div class="tile-meta">
        <strong>${escapeHtml(d.name)}</strong>
        <small>${escapeHtml(d.location)}</small>
      </div>
      <div class="tile-stats" id="stats-${d.id}"></div>
      <div class="tile-telemetry" id="tel-${d.id}"></div>
    </div>`;
  el.addEventListener('click', () => openModal(d));
  grid.appendChild(el);
  return el;
}

function render(devices) {
  document.getElementById('summary').textContent =
    `${devices.filter((d) => d.online).length}/${devices.length} онлайн`;

  for (const d of devices) {
    const el = tileEl(d);
    el.classList.toggle('offline', !d.online);
    const badge = el.querySelector(`#badge-${d.id}`);
    badge.textContent = d.online ? 'ONLINE' : 'OFFLINE';
    badge.className = `badge ${d.online ? 'on' : 'off'}`;

    el.querySelector(`#stats-${d.id}`).textContent = d.online
      ? `${fmtBitrate(d.bitrateKbps)} · ${fmtUptime(d.uptimeSec)} · 👁 ${d.readers}` : '';

    el.querySelector(`#tel-${d.id}`).textContent = d.telemetry
      ? telemetryLine(d.telemetry) : '';

    const state = players.get(d.id) || {};
    if (d.online && !state.player) {
      startPlayer(d).catch(() => {});
    } else if (!d.online && state.player) {
      state.player.close();
      players.set(d.id, { player: null });
    }
  }
}

async function startPlayer(d) {
  const video = document.getElementById(`vid-${d.id}`);
  players.set(d.id, { player: null, starting: true });
  const player = await startWhep(video, `${cfg.webrtcBase}/${d.id}/whep`, cfg.readUser, cfg.readPass);
  players.set(d.id, { player });
}

function openModal(d) {
  const modal = document.getElementById('modal');
  const video = document.getElementById('modal-video');
  document.getElementById('modal-caption').textContent = `${d.name} — ${d.location}`;
  modal.classList.remove('hidden');
  let modalPlayer = null;
  if (d.online) startWhep(video, `${cfg.webrtcBase}/${d.id}/whep`, cfg.readUser, cfg.readPass)
    .then((p) => { modalPlayer = p; }).catch(() => {});
  const close = () => { modalPlayer?.close(); modal.classList.add('hidden'); };
  document.getElementById('modal-close').onclick = close;
}

function connectSSE() {
  const es = new EventSource('/api/stream');
  es.onmessage = (e) => render(JSON.parse(e.data));
  es.onerror = () => { es.close(); setTimeout(connectSSE, 3000); }; // reconnect
}

function fmtBitrate(kbps) { return kbps == null ? '—' : kbps >= 1000 ? `${(kbps / 1000).toFixed(1)} Mbps` : `${kbps} kbps`; }
function fmtUptime(s) { if (s == null) return '—'; const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60); return h ? `${h}год ${m}хв` : `${m}хв`; }
function telemetryLine(t) { const parts = []; if (t.rssi != null) parts.push(`RSSI ${t.rssi}`); if (t.freq != null) parts.push(`${t.freq}`); if (t.alarm) parts.push('⚠ ALARM'); return parts.join(' · '); }
function escapeHtml(s) { return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

(async function init() {
  cfg = await loadConfig();
  if (!cfg) return;
  const first = await fetch('/api/devices').then((r) => r.json());
  render(first);
  connectSSE();
})();
```

- [ ] **Step 5: Write `dashboard/public/styles.css`**

```css
:root { --bg:#0e1116; --panel:#171c24; --line:#262d38; --on:#27c93f; --off:#6b7280; --text:#e6edf3; --accent:#3b82f6; }
* { box-sizing: border-box; }
body { margin:0; font-family: system-ui, sans-serif; background: var(--bg); color: var(--text); }
.topbar { display:flex; align-items:center; gap:1rem; padding:.6rem 1rem; background:var(--panel); border-bottom:1px solid var(--line); }
.topbar h1 { font-size:1rem; margin:0; flex:0 0 auto; }
.summary { color:#9aa4b2; font-size:.85rem; margin-left:auto; }
.topbar button { background:transparent; color:var(--text); border:1px solid var(--line); border-radius:6px; padding:.35rem .7rem; cursor:pointer; }
.grid { display:grid; gap:.6rem; padding:.6rem; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); }
.tile { position:relative; aspect-ratio:16/9; background:#000; border:1px solid var(--line); border-radius:10px; overflow:hidden; cursor:pointer; }
.tile.offline { opacity:.6; }
.tile video { width:100%; height:100%; object-fit:cover; background:#000; }
.tile-overlay { position:absolute; inset:0; display:flex; flex-direction:column; justify-content:space-between; padding:.5rem; pointer-events:none; background:linear-gradient(to top, rgba(0,0,0,.55), transparent 40%); }
.badge { align-self:flex-start; font-size:.7rem; font-weight:700; padding:.15rem .45rem; border-radius:999px; letter-spacing:.04em; }
.badge.on { background:rgba(39,201,63,.2); color:var(--on); }
.badge.off { background:rgba(107,114,128,.25); color:#cbd5e1; }
.tile-meta strong { display:block; font-size:.9rem; }
.tile-meta small { color:#cbd5e1; }
.tile-stats, .tile-telemetry { font-size:.75rem; color:#cbd5e1; }
.modal { position:fixed; inset:0; background:rgba(0,0,0,.9); display:flex; align-items:center; justify-content:center; flex-direction:column; z-index:50; }
.modal.hidden { display:none; }
.modal video { max-width:92vw; max-height:82vh; background:#000; }
.modal-caption { margin-top:.6rem; color:var(--text); }
.modal-close { position:absolute; top:1rem; right:1rem; font-size:1.2rem; background:transparent; color:#fff; border:none; cursor:pointer; }
.login-page { display:flex; align-items:center; justify-content:center; min-height:100vh; }
.login-card { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:1.5rem; display:flex; flex-direction:column; gap:.7rem; min-width:280px; }
.login-card input { padding:.6rem; border-radius:6px; border:1px solid var(--line); background:#0e1116; color:var(--text); }
.login-card button { padding:.6rem; border-radius:6px; border:none; background:var(--accent); color:#fff; cursor:pointer; }
.login-card .err { color:#f87171; font-size:.8rem; min-height:1em; margin:0; }
```

- [ ] **Step 6: Syntax-check the frontend JS**

Run:
```bash
node --check dashboard/public/app.js
node --check dashboard/public/whep.js
```
Expected: no output, exit 0 for both.

- [ ] **Step 7: Commit**

```bash
git add dashboard/public
git commit -m "feat: dashboard frontend (responsive grid, WHEP players, fullscreen, telemetry panel)"
```

---

## Task 10: add-device CLI + wrapper (`bin/add-device.js`, `add-device.sh`)

**Files:**
- Create: `bin/add-device.js`
- Create: `add-device.sh`

Adds a device to the registry, regenerates the config, reloads MediaMTX, and prints the ready-to-run Pi push commands. Reuses `lib/registry`, `lib/render-config`, `lib/push-command`.

- [ ] **Step 1: Write `bin/add-device.js`**

```js
#!/usr/bin/env node
// bin/add-device.js — add a device, regenerate config, reload MediaMTX, print push commands.
import { writeFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { loadRegistry, saveRegistry, ensureReadUser, addDevice } from '../lib/registry.js';
import { renderConfig } from '../lib/render-config.js';
import { buildRtspPush, buildSrtPush } from '../lib/push-command.js';

const [, , id, name, location] = process.argv;
if (!id) {
  console.error('Usage: node bin/add-device.js <device-id> "<friendly name>" "<location>"');
  process.exit(2);
}

const env = process.env;
const devicesFile = env.DEVICES_FILE || 'devices.yml';
const outFile = env.MEDIAMTX_CONFIG || 'mediamtx.yml';
const wgIp = env.WG_IP || '10.8.0.1';

const reg = loadRegistry(devicesFile);
ensureReadUser(reg);
let device;
try {
  device = addDevice(reg, { id, name, location });
} catch (e) {
  console.error(`Error: ${e.message}`);
  process.exit(1);
}
saveRegistry(devicesFile, reg);

const opts = {
  wgIp,
  rtspPort: Number(env.RTSP_PORT || 8554),
  srtPort: Number(env.SRT_PORT || 8890),
  webrtcPort: Number(env.WEBRTC_PORT || 8889),
  iceUdpPort: Number(env.ICE_UDP_PORT || 8189),
  apiHost: env.API_HOST || '127.0.0.1',
  apiPort: Number(env.API_PORT || 9997),
};
writeFileSync(outFile, renderConfig(reg, opts), 'utf8');

// Reload MediaMTX so the new publish user takes effect (no-op failure on dev machines).
let reloaded = false;
try { execFileSync('systemctl', ['reload-or-restart', 'mediamtx'], { stdio: 'ignore' }); reloaded = true; } catch { /* not on the server */ }

const pushOpts = {
  wgIp,
  rtspPort: opts.rtspPort,
  srtPort: opts.srtPort,
  videoDevice: env.PI_VIDEO_DEVICE || '/dev/video0',
  framerate: Number(env.PI_FRAMERATE || 30),
  videoSize: env.PI_VIDEO_SIZE || '720x576',
  bitrate: env.PI_BITRATE || '2M',
};

console.log(`\n✅ Added device "${device.id}" (${device.name}).`);
console.log(`   Registry: ${devicesFile}   Config: ${outFile}   MediaMTX reload: ${reloaded ? 'done' : 'SKIPPED (run on server)'}`);
console.log(`\n🔑 Publish password: ${device.publish_pass}`);
console.log(`\n▶ Pi 5 push — RTSP (software x264):\n${buildRtspPush(device, pushOpts)}`);
console.log(`\n▶ Pi 5 push — SRT (alternative):\n${buildSrtPush(device, pushOpts)}\n`);
```

- [ ] **Step 2: Write `add-device.sh`** (thin wrapper that loads `.env` then calls node)

```bash
#!/usr/bin/env bash
# add-device.sh — wrapper around bin/add-device.js; loads .env then runs node.
set -euo pipefail
cd "$(dirname "$0")"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
exec node bin/add-device.js "$@"
```

- [ ] **Step 3: Smoke-test add-device against a temp registry**

Run (bash):
```bash
cp devices.example.yml /tmp/dev.yml
DEVICES_FILE=/tmp/dev.yml MEDIAMTX_CONFIG=/tmp/mtx.yml node bin/add-device.js pi-03 "Garage" "South"
grep -q "pi-03" /tmp/dev.yml && echo "registry updated"
grep -q "user: pi-03" /tmp/mtx.yml && echo "config regenerated"
```
Expected: prints the added-device summary with RTSP + SRT push commands, then `registry updated` and `config regenerated`. (MediaMTX reload is SKIPPED off-server.)

- [ ] **Step 4: Commit**

```bash
git add bin/add-device.js add-device.sh
git commit -m "feat: add-device CLI generates creds, regenerates config, prints Pi push command"
```

---

## Task 11: systemd units (`systemd/`)

**Files:**
- Create: `systemd/mediamtx.service`
- Create: `systemd/fpv-dashboard.service`

- [ ] **Step 1: Write `systemd/mediamtx.service`**

```ini
[Unit]
Description=MediaMTX media server (FPV ingest + WebRTC)
After=network-online.target wg-quick@wg0.service
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/mediamtx /usr/local/etc/mediamtx.yml
Restart=always
RestartSec=2
User=mediamtx
Group=mediamtx
# Config is generated from devices.yml; reload re-reads it.
ExecReload=/bin/kill -HUP $MAINPID
AmbientCapabilities=
NoNewPrivileges=true
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Write `systemd/fpv-dashboard.service`**

```ini
[Unit]
Description=FPV dashboard (Express + WHEP grid)
After=network-online.target mediamtx.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/fpv-video-stream
EnvironmentFile=/opt/fpv-video-stream/.env
ExecStart=/usr/bin/node dashboard/server.js
Restart=always
RestartSec=2
User=fpv
Group=fpv
NoNewPrivileges=true
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 3: Sanity-check the unit files parse** (Linux only; on Windows dev just verify they were written)

Run (on server or any Linux): `systemd-analyze verify systemd/mediamtx.service systemd/fpv-dashboard.service || true`
Expected: no fatal "Failed to parse" errors (warnings about absolute paths/users not yet existing are fine pre-install).

- [ ] **Step 4: Commit**

```bash
git add systemd/
git commit -m "chore: systemd units for mediamtx and dashboard"
```

---

## Task 12: Installer (`install.sh`)

**Files:**
- Create: `install.sh`

Idempotent, parameterized, additive to networking (never touches wg-easy). Installs MediaMTX, Node deps, renders config, installs systemd units, creates service users, generates `.env` and `devices.yml` from examples if missing.

- [ ] **Step 1: Write `install.sh`**

```bash
#!/usr/bin/env bash
# install.sh — idempotent server installer for the FPV video-stream server side.
# Does NOT modify WireGuard / wg-easy. Only adds MediaMTX + dashboard.
set -euo pipefail

# ---- parameters (override via env or flags) ----
WG_IP="${WG_IP:-10.8.0.1}"
WG_IFACE="${WG_IFACE:-wg0}"
APP_DIR="${APP_DIR:-/opt/fpv-video-stream}"
MEDIAMTX_VERSION="${MEDIAMTX_VERSION:-latest}"
MEDIAMTX_CONFIG="/usr/local/etc/mediamtx.yml"

usage() { echo "Usage: sudo WG_IP=10.8.0.1 WG_IFACE=wg0 ./install.sh"; }
[ "${1:-}" = "-h" ] && { usage; exit 0; }

if [ "$(id -u)" -ne 0 ]; then echo "Run as root (sudo)."; exit 1; fi

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
ARCH="$(dpkg --print-architecture)"  # amd64 / arm64
case "$ARCH" in amd64) MTX_ARCH=linux_amd64;; arm64) MTX_ARCH=linux_arm64v8;; *) echo "Unsupported arch $ARCH"; exit 1;; esac

echo "==> [1/8] Base packages (node, jq, curl, tar)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq curl tar jq ca-certificates >/dev/null
if ! command -v node >/dev/null || [ "$(node -v | cut -dv -f2 | cut -d. -f1)" -lt 18 ]; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >/dev/null
  apt-get install -y -qq nodejs >/dev/null
fi
echo "    node $(node -v)"

echo "==> [2/8] Service users"
id mediamtx >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin mediamtx
id fpv      >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin fpv

echo "==> [3/8] Install MediaMTX ($MEDIAMTX_VERSION, $MTX_ARCH)"
if [ "$MEDIAMTX_VERSION" = "latest" ]; then
  MEDIAMTX_VERSION="$(curl -fsSL https://api.github.com/repos/bluenviron/mediamtx/releases/latest | jq -r .tag_name)"
fi
if [ ! -x /usr/local/bin/mediamtx ] || ! /usr/local/bin/mediamtx --version 2>/dev/null | grep -q "${MEDIAMTX_VERSION#v}"; then
  TMP="$(mktemp -d)"
  curl -fsSL "https://github.com/bluenviron/mediamtx/releases/download/${MEDIAMTX_VERSION}/mediamtx_${MEDIAMTX_VERSION}_${MTX_ARCH}.tar.gz" -o "$TMP/m.tar.gz"
  tar -xzf "$TMP/m.tar.gz" -C "$TMP"
  install -m 0755 "$TMP/mediamtx" /usr/local/bin/mediamtx
  rm -rf "$TMP"
fi
echo "    $(/usr/local/bin/mediamtx --version 2>&1 | head -1)"
mkdir -p /usr/local/etc

echo "==> [4/8] App files -> $APP_DIR"
mkdir -p "$APP_DIR"
cp -r "$SRC_DIR/." "$APP_DIR/"
cd "$APP_DIR"

echo "==> [5/8] .env and devices.yml (created from examples if missing)"
if [ ! -f .env ]; then
  cp .env.example .env
  sed -i "s/^WG_IP=.*/WG_IP=${WG_IP}/" .env
  sed -i "s/^WG_IFACE=.*/WG_IFACE=${WG_IFACE}/" .env
  sed -i "s#^MEDIAMTX_CONFIG=.*#MEDIAMTX_CONFIG=${MEDIAMTX_CONFIG}#" .env
  sed -i "s/^DASH_HOST=.*/DASH_HOST=${WG_IP}/" .env
  sed -i "s/^SESSION_SECRET=.*/SESSION_SECRET=$(openssl rand -hex 24)/" .env
  sed -i "s/^DASH_PASS=.*/DASH_PASS=$(openssl rand -base64 12 | tr -d '/+=')/" .env
  echo "    generated .env (review DASH_USER/DASH_PASS)"
fi
[ -f devices.yml ] || { cp devices.example.yml devices.yml; echo "    seeded devices.yml from example — set real passwords or use add-device.sh"; }
chmod 600 .env devices.yml

echo "==> [6/8] npm install + render config"
set -a; . ./.env; set +a
npm install --omit=dev --no-audit --no-fund >/dev/null
node bin/gen-mediamtx.js
chown mediamtx:mediamtx "$MEDIAMTX_CONFIG"
chown -R fpv:fpv "$APP_DIR"

echo "==> [7/8] systemd units"
sed "s#/opt/fpv-video-stream#${APP_DIR}#g" systemd/fpv-dashboard.service > /etc/systemd/system/fpv-dashboard.service
cp systemd/mediamtx.service /etc/systemd/system/mediamtx.service
systemctl daemon-reload
systemctl enable --now mediamtx.service
systemctl enable --now fpv-dashboard.service

echo "==> [8/8] Firewall guidance (not enforced automatically)"
cat <<EOF
  Ports in use (all bound to ${WG_IP} except API on 127.0.0.1):
    8554/tcp  RTSP ingest      (${WG_IP})
    8890/udp  SRT ingest       (${WG_IP})
    8889/tcp  WebRTC/WHEP      (${WG_IP})
    8189/udp  WebRTC ICE       (advertises ${WG_IP})
    9997/tcp  Control API      (127.0.0.1 only)
    8080/tcp  Dashboard        (${WG_IP})
  Optional ufw (only allow these in via ${WG_IFACE}):
    ufw allow in on ${WG_IFACE} to any port 8554 proto tcp
    ufw allow in on ${WG_IFACE} to any port 8890 proto udp
    ufw allow in on ${WG_IFACE} to any port 8889 proto tcp
    ufw allow in on ${WG_IFACE} to any port 8189 proto udp
    ufw allow in on ${WG_IFACE} to any port 8080 proto tcp
  Do NOT expose these on the public interface. WireGuard handshake is the only public port.

Done. Dashboard: http://${WG_IP}:8080  (login from .env)
EOF
```

- [ ] **Step 2: Syntax-check the installer**

Run: `bash -n install.sh && echo "install.sh OK"`
Expected: `install.sh OK` (no syntax errors). Also `bash -n add-device.sh && echo "add-device.sh OK"`.

- [ ] **Step 3: Commit**

```bash
git add install.sh
git commit -m "feat: idempotent parameterized installer (MediaMTX + dashboard, additive to WG)"
```

---

## Task 13: README

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write `README.md`** with these sections (use exact content):

````markdown
# FPV Video Stream — Server

Server side for multi-node FPV video surveillance: MediaMTX ingests H.264 from Raspberry Pi 5
nodes over WireGuard; a Node/Express dashboard shows all streams in a live WebRTC grid with
online/offline status. Everything is reachable only over WireGuard.

## Architecture

```
Pi (10.8.0.x) --H.264--> MediaMTX (10.8.0.1)  --WHEP--> Browser (wg-easy client)
   ffmpeg x264                RTSP :8554 / SRT :8890 ingest
                              WebRTC :8889 + ICE :8189
                              Control API 127.0.0.1:9997  <-- dashboard polls (read-only)
                          Dashboard :8080 (10.8.0.1)
```

`devices.yml` is the single source of truth. `mediamtx.yml` is generated from it
(`node bin/gen-mediamtx.js`); each device gets its own publish user, so a compromised node
cannot read or publish other streams.

## Install (Ubuntu 22.04/24.04, root)

```bash
git clone https://github.com/AndriyYaremenko/fpv-video-stream.git
cd fpv-video-stream
sudo WG_IP=10.8.0.1 WG_IFACE=wg0 ./install.sh
```

The installer is idempotent and **does not touch wg-easy/WireGuard**. It installs MediaMTX +
Node, generates `.env` and `devices.yml` (from examples) on first run, renders the config, and
starts both systemd services. Review `.env` afterwards (set `DASH_USER` / `DASH_PASS`).

## Add a new node

```bash
sudo ./add-device.sh pi-03 "Garage" "Compound — South"
```

This generates credentials, updates `devices.yml`, regenerates `mediamtx.yml`, reloads MediaMTX,
and prints the exact Pi push command. No main-config editing required.

## Pi 5 push command (software x264 — Pi 5 has no hardware H.264 encoder)

RTSP (printed by add-device.sh, with real credentials filled in):

```bash
ffmpeg -f v4l2 -framerate 30 -video_size 720x576 -i /dev/video0 \
  -c:v libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p \
  -g 30 -b:v 2M -maxrate 2M -bufsize 2M \
  -f rtsp -rtsp_transport tcp \
  "rtsp://<device-id>:<publish_pass>@10.8.0.1:8554/<device-id>"
```

SRT alternative (lower jitter on lossy links):

```bash
ffmpeg -f v4l2 -framerate 30 -video_size 720x576 -i /dev/video0 \
  -c:v libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p \
  -g 30 -b:v 2M -maxrate 2M -bufsize 2M -f mpegts \
  "srt://10.8.0.1:8890?streamid=#!::m=publish,r=<device-id>,u=<device-id>,s=<publish_pass>&latency=200000&pkt_size=1316"
```

On the Pi, wrap this command in a `systemd` service with `Restart=always` so it auto-reconnects after
reboots or link drops (Pi-side setup is out of scope for this server repo).

## Access the dashboard

Operators connect as a wg-easy WireGuard client, then browse to `http://10.8.0.1:8080` and log in
with `DASH_USER` / `DASH_PASS` from `.env`.

## Ports & interfaces

| Port | Proto | Service | Bind |
|---|---|---|---|
| 8554 | tcp | RTSP ingest | 10.8.0.1 (WG) |
| 8890 | udp | SRT ingest | 10.8.0.1 (WG) |
| 8889 | tcp | WebRTC/WHEP | 10.8.0.1 (WG) |
| 8189 | udp | WebRTC ICE | advertises 10.8.0.1 |
| 9997 | tcp | Control API | 127.0.0.1 only |
| 8080 | tcp | Dashboard | 10.8.0.1 (WG) |

WireGuard handshake (wg-easy) is the only public-facing port. Optional `ufw` rules are printed at
the end of `install.sh` — allow the above only on `wg0`.

## Telemetry hook (optional, stubbed)

Pi (or any WG client) can POST JSON to `http://10.8.0.1:8080/api/telemetry/<device-id>`:

```bash
curl -X POST http://10.8.0.1:8080/api/telemetry/pi-01 \
  -H 'Content-Type: application/json' -d '{"rssi":-58,"freq":"5.8G","alarm":false}'
```

The latest payload shows on the device tile. Set `TELEMETRY_TOKEN` in `.env` to require
`Authorization: Bearer <token>`. No live source is wired yet — this is a ready hook.

## Public TLS access (later, optional)

Not enabled in this iteration. To expose the dashboard publicly, put Caddy (automatic TLS) in front
of `127.0.0.1:8080` with a login, point a domain at the server's public IP, open 80/443, and keep
the MediaMTX control API internal. The dashboard already runs behind a login.

## Operations

```bash
systemctl status mediamtx fpv-dashboard
journalctl -u mediamtx -f
journalctl -u fpv-dashboard -f
node bin/gen-mediamtx.js && systemctl reload mediamtx   # after manual devices.yml edits
```

## Development / tests

```bash
npm install
npm test          # node --test over lib/ and dashboard/
```
````

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: README (install, add-device, Pi push, ports, telemetry, ops)"
```

---

## Task 14: Final full-suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run the complete test suite**

Run: `npm test`
Expected: all test files pass; summary shows `pass` count > 0 and `fail 0`.

- [ ] **Step 2: End-to-end generation smoke (off-server)**

Run (bash):
```bash
cp devices.example.yml /tmp/e2e.yml
DEVICES_FILE=/tmp/e2e.yml MEDIAMTX_CONFIG=/tmp/e2e.mtx.yml node bin/gen-mediamtx.js
DEVICES_FILE=/tmp/e2e.yml MEDIAMTX_CONFIG=/tmp/e2e.mtx.yml node bin/add-device.js pi-99 "Test" "Lab"
node --check dashboard/public/app.js && node --check dashboard/public/whep.js
bash -n install.sh && bash -n add-device.sh
echo "E2E OK"
```
Expected: config written twice (2 then 3 devices), push commands printed, JS checks pass, shell scripts parse, `E2E OK`.

- [ ] **Step 3: Confirm secrets are git-ignored**

Run: `git status --porcelain --ignored | grep -E "devices.yml|\.env|mediamtx.yml" || echo "no tracked secrets"`
Expected: the real `.env`/`devices.yml`/`mediamtx.yml` (if any were created locally) appear under ignored, NOT staged.

- [ ] **Step 4: Final commit (if anything outstanding)**

```bash
git add -A
git commit -m "chore: final verification pass" || echo "nothing to commit"
```

---

## Notes for the executor

- **Where TDD applies:** Tasks 2–6 and 8 are pure-logic/unit-tested (registry, config render, status,
  API client, push commands, server routes). Tasks 7, 9, 11, 12, 13 are config/scripts/UI verified by
  smoke checks (`node --check`, `bash -n`, generation runs) — there is no meaningful unit test for a
  systemd unit or a CSS grid, so don't invent one.
- **Windows dev box:** `node --test`, `node bin/*.js`, and `node --check` run on Windows. `chmod`/
  `systemctl` calls are guarded (try/catch or run only on the server). Shell-script syntax checks need
  bash (available via the Bash tool / WSL). Real deployment + browser/WebRTC verification happen on the
  Ubuntu dev server (193.242.163.139) by running `install.sh`.
- **Deviations from spec (intentional):** (1) Single root `package.json` instead of a separate
  `dashboard/package.json` — one `npm install`, shared `lib/`. (2) Config is rendered fully from
  `lib/render-config.js` (JS object → YAML) rather than a brittle `mediamtx.template.yml` string
  template. (3) `add-device` logic lives in `bin/add-device.js` (testable) with `add-device.sh` as the
  documented wrapper. All three keep the spec's guarantees (per-device auth, registry-driven, no
  hand-editing) while being cleaner/testable.
- **Caddyfile** is intentionally not created (WG-only access chosen); the README documents the future
  public-TLS path.
