# Dashboard MQTT Subscriber + Waterfall (SP-C) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The browser dashboard subscribes to scan data over MQTT-WSS and renders a per-band line+waterfall (3 bands in a row), replacing the HTTP `/api/telemetry` + SSE scan path. Cameras stay on SSE/WHEP.

**Architecture:** Two independent live loops — SSE (cameras, unchanged) + MQTT (scanners). A new `mqtt-scan.js` holds a **pure store reducer** (unit-tested) and a browser `MqttScanClient` (vendored `mqtt.js`, WSS creds from `GET /api/mqtt`). `spectrum.js` gains a `psdColor` helper + data-driven `detectionX` + a waterfall DOM render. `server.js` drops the telemetry hook. Scanner metadata/management stays in the registry; live data + presence come from MQTT, joined by id.

**Tech Stack:** vanilla ES modules (no build step), `mqtt.js` (vendored), `node --test`, `node --check`.

Spec: `docs/superpowers/specs/2026-06-18-mqtt-dashboard-subscriber-design.md`

**Test commands (repo root):** `npm test` (= `node --test`, the dashboard + helper suites). Browser-only files are checked with `node --check <file>` (syntax) — they import browser globals at runtime only, so they are not unit-tested (existing convention).

**Branch note:** this branch (`feat/mqtt-dashboard-subscriber`) is cut from `feat/mqtt-broker` because it needs SP-A's `GET /api/mqtt`. When SP-A merges to `main`, rebase onto `main`.

---

## File Structure

```
dashboard/public/mqtt-scan.js         (new: pure reducer emptyStore/reduce + MqttScanClient)
dashboard/public/vendor/mqtt.min.js   (new: vendored mqtt.js browser build)
dashboard/public/spectrum.js          (change: psdColor, data-driven detectionX, waterfall render, drop BAND_RANGES)
dashboard/public/app.js               (change: MQTT alongside SSE; scan render + alert + online from the store)
dashboard/public/styles.css           (change: waterfall + 3-column band styles)
dashboard/server.js                   (change: remove telemetry hook + scanner-freshness; adjust scanner-create)
test/server.test.js                   (change: drop telemetry/freshness tests; adjust scanner-create + add 404)
test/mqtt-scan.test.js                (new: reducer unit tests)
test/spectrum.test.js                 (change: data-driven detectionX, psdColor; drop BAND_RANGES test)
README.md                             (change: dashboard scan = MQTT)
```

**Interim note:** between Task 3 (detectionX signature change / BAND_RANGES drop) and Task 5/6 (DOM rework + app wiring) the browser scan panel is temporarily inconsistent. `node --check` + `npm test` stay green throughout; the final state (after Task 6) is correct. This branch is developed in one sitting and merged only when complete.

---

## Task 1: server.js — remove the HTTP telemetry path

**Files:**
- Modify: `dashboard/server.js`
- Test: `test/server.test.js`

- [ ] **Step 1: Update the tests**

In `test/server.test.js`:

(a) **Delete** these three tests entirely: `telemetry hook stores last value and surfaces it on devices`, `scanner online is derived from telemetry freshness`, `scanner freshness window is configurable (stale telemetry -> offline)`.

(b) **Replace** the test `POST /api/devices with kind=scanner returns no push, includes telemetry hint` with:

```javascript
test('POST /api/devices with kind=scanner returns no push, includes the MQTT topic prefix', async () => {
  const reg = { read_user: 'viewer', read_pass: 'rpw', devices: [] };
  const { server, base } = await startWith(reg);
  const cookie = await login(base);
  const res = await fetch(`${base}/api/devices`, {
    method: 'POST', headers: { cookie, 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: 'hackrf', name: 'Scanner', kind: 'scanner' }),
  });
  const body = await res.json();
  assert.equal(res.status, 201);
  assert.equal(body.device.kind, 'scanner');
  assert.equal(body.push, undefined);
  assert.equal(body.scanner.topicPrefix, 'fpv/hackrf');
  server.close();
});
```

(c) **Add** a test that the telemetry route is gone:

```javascript
test('POST /api/telemetry is removed (404)', async () => {
  const { server, base } = await startServer();
  const res = await fetch(`${base}/api/telemetry/pi-01`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
  });
  assert.equal(res.status, 404);
  server.close();
});
```

- [ ] **Step 2: Run the tests to verify the new ones fail**

Run: `npm test`
Expected: the scanner-create test fails (response still has `telemetryPath`, not `topicPrefix`); the 404 test fails (route still exists → not 404). (The three deleted tests no longer run.)

- [ ] **Step 3: Implement the server changes**

In `dashboard/server.js`:

(a) **Remove** the telemetry hook block:

```javascript
  // ---- telemetry hook (called by Pi over WG; optional bearer token) ----
  app.post('/api/telemetry/:id', (req, res) => { ... });
```

(b) **Remove** the in-memory telemetry state and the freshness constant:

```javascript
  const telemetry = new Map();
```
and
```javascript
  const SCANNER_FRESH_MS = config.scannerFreshMs || 60000;
```
(also remove the now-unused comment block above `SCANNER_FRESH_MS`).

(c) In `snapshot()`, **remove** the per-scanner telemetry + freshness handling so the loop becomes:

```javascript
  async function snapshot() {
    const paths = await getPaths();
    const now = Date.now();
    const merged = mergeStatus(registry, paths, now);
    for (const d of merged) {
      const prev = samples.get(d.id);
      d.bitrateKbps = d.online ? computeBitrateKbps(prev?.bytes, prev?.ts, d.bytesReceived, now) : null;
      if (d.online) samples.set(d.id, { bytes: d.bytesReceived, ts: now });
    }
    return merged;
  }
```

(d) In the `POST /api/devices` handler, change the scanner branch response from the telemetry hint to the MQTT topic prefix:

```javascript
      if (device.kind === 'scanner') {
        return res.status(201).json({ device, scanner: { topicPrefix: `fpv/${device.id}` } });
      }
```

(e) In `delete` handler, **remove** the `telemetry.delete(req.params.id);` line (the Map is gone); keep `samples.delete(...)`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `npm test`
Expected: PASS — the dashboard suite green, including the new scanner-create + 404 tests.

- [ ] **Step 5: Commit**

```bash
git add dashboard/server.js test/server.test.js
git commit -m "feat(dashboard): remove HTTP telemetry path (scan moves to MQTT)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: mqtt-scan.js — pure store reducer

**Files:**
- Create: `dashboard/public/mqtt-scan.js`
- Test: `test/mqtt-scan.test.js`

- [ ] **Step 1: Write the failing tests**

Create `test/mqtt-scan.test.js`:

```javascript
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { emptyStore, reduce } from '../dashboard/public/mqtt-scan.js';

test('reduce ignores unknown/malformed topics', () => {
  assert.deepEqual(reduce(emptyStore(), 'fpv/x/other', '{}'), {});
  assert.deepEqual(reduce(emptyStore(), 'nope', '{}'), {});
});

test('reduce sets presence from status', () => {
  const s = reduce(emptyStore(), 'fpv/hackrf/status', JSON.stringify({ online: true, ts: 5 }));
  assert.equal(s.hackrf.online, true);
  assert.equal(s.hackrf.status_ts, 5);
});

test('reduce stores the detection payload', () => {
  const s = reduce(emptyStore(), 'fpv/hackrf/detection',
    JSON.stringify({ ts: 9, detections: [{ band: '5.8G', class: 'analog' }], occupancy: { '5.8G': 0.4 } }));
  assert.equal(s.hackrf.detection.detections[0].class, 'analog');
  assert.equal(s.hackrf.detection.occupancy['5.8G'], 0.4);
});

test('reduce captures self-describing bands + latest psd + waterfall frame', () => {
  const s = emptyStore();
  reduce(s, 'fpv/hackrf/spectrum',
    JSON.stringify({ ts: 1, bands: [{ id: '5.8G', low_mhz: 5645, high_mhz: 5945, psd: [-90, -50] }] }));
  assert.deepEqual(s.hackrf.bands['5.8G'], { low_mhz: 5645, high_mhz: 5945 });
  assert.deepEqual(s.hackrf.latestPsd['5.8G'], [-90, -50]);
  assert.equal(s.hackrf.waterfalls['5.8G'].length, 1);
});

test('reduce caps the waterfall ring buffer at depth (oldest dropped)', () => {
  const s = emptyStore();
  for (let i = 0; i < 10; i += 1) {
    reduce(s, 'fpv/hackrf/spectrum',
      JSON.stringify({ ts: i, bands: [{ id: '5.8G', low_mhz: 5645, high_mhz: 5945, psd: [i] }] }), { depth: 3 });
  }
  const buf = s.hackrf.waterfalls['5.8G'];
  assert.equal(buf.length, 3);
  assert.deepEqual(buf.map((f) => f.ts), [7, 8, 9]);
});

test('reduce swallows malformed JSON', () => {
  assert.deepEqual(reduce(emptyStore(), 'fpv/hackrf/detection', '{not json'), {});
});

test('reduce accepts an already-parsed object payload', () => {
  const s = reduce(emptyStore(), 'fpv/hackrf/status', { online: false, ts: 2 });
  assert.equal(s.hackrf.online, false);
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `npm test`
Expected: FAIL — `Cannot find module '../dashboard/public/mqtt-scan.js'`.

- [ ] **Step 3: Create `dashboard/public/mqtt-scan.js` (pure part)**

```javascript
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
```

- [ ] **Step 4: Run to verify they pass**

Run: `npm test`
Expected: PASS (7 new tests + the rest).

- [ ] **Step 5: Commit**

```bash
git add dashboard/public/mqtt-scan.js test/mqtt-scan.test.js
git commit -m "feat(dashboard): pure MQTT scan store reducer (status/detection/spectrum + waterfall ring)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: spectrum.js — psdColor + data-driven detectionX

**Files:**
- Modify: `dashboard/public/spectrum.js`
- Test: `test/spectrum.test.js`

- [ ] **Step 1: Update the tests**

In `test/spectrum.test.js`:

(a) Change the import line to drop `BAND_RANGES` and add `psdColor`:

```javascript
import { splitByKind, classColor, fmtFreq, fmtPct, psdToPoints, detectionX, psdColor } from '../dashboard/public/spectrum.js';
```

(b) **Replace** the `detectionX maps center freq within band and clamps out-of-range` test with the data-driven signature:

```javascript
test('detectionX maps center freq within an explicit band range and clamps', () => {
  assert.ok(Math.abs(detectionX(5795, 5645, 5945, 300) - 150) < 1); // mid -> 150
  assert.equal(detectionX(1000, 5645, 5945, 300), 0);               // below -> 0
  assert.equal(detectionX(9999, 5645, 5945, 300), 300);             // above -> width
});
```

(c) **Delete** the `BAND_RANGES covers the three FPV bands` test.

(d) **Add** psdColor tests:

```javascript
test('psdColor clamps below/above the range to the endpoint colors', () => {
  assert.equal(psdColor(-200), psdColor(-100));   // clamp low
  assert.equal(psdColor(0), psdColor(-20));        // clamp high
});

test('psdColor returns an rgb() string and varies with power', () => {
  assert.match(psdColor(-60), /^rgb\(\d+, ?\d+, ?\d+\)$/);
  assert.notEqual(psdColor(-90), psdColor(-30));
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `npm test`
Expected: FAIL — `psdColor` not exported; the new detectionX call shape mismatches the old signature.

- [ ] **Step 3: Implement the helper changes**

In `dashboard/public/spectrum.js`:

(a) **Remove** the `BAND_RANGES` export:

```javascript
export const BAND_RANGES = { '1.2G': [1080, 1360], '2.4G': [2370, 2510], '5.8G': [5645, 5945] };
```

(b) **Replace** `detectionX` with the data-driven version:

```javascript
// X pixel for a detection center frequency within an explicit band range (clamped to [0, width]).
export function detectionX(centerMhz, lowMhz, highMhz, width) {
  const frac = (centerMhz - lowMhz) / ((highMhz - lowMhz) || 1);
  return Math.max(0, Math.min(width, frac * width));
}
```

(c) **Add** `psdColor` (place it after `psdToPoints`):

```javascript
// Map a dBm value to a CSS color on the spectrum scale (noise = dark blue → strong = red).
export function psdColor(db, dbMin = -100, dbMax = -20) {
  const span = (dbMax - dbMin) || 1;
  const t = Math.max(0, Math.min(1, (db - dbMin) / span));
  const stops = [[2, 2, 17], [23, 118, 102], [42, 170, 102], [255, 221, 51], [255, 51, 51]];
  const seg = t * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(seg));
  const f = seg - i;
  const [r1, g1, b1] = stops[i];
  const [r2, g2, b2] = stops[i + 1];
  const r = Math.round(r1 + (r2 - r1) * f);
  const g = Math.round(g1 + (g2 - g1) * f);
  const b = Math.round(b1 + (b2 - b1) * f);
  return `rgb(${r}, ${g}, ${b})`;
}
```

(Leave the DOM `renderSpectrum`/`bandChart` as-is for now — they will be reworked in Task 5. They reference the old `detectionX` signature and `BAND_RANGES`; this is a known interim inconsistency in the browser render until Task 5. `node --check` and `npm test` still pass because the pure helpers and tests are correct.)

- [ ] **Step 4: Run to verify they pass + syntax-check**

Run: `npm test`
Expected: PASS (updated detectionX + psdColor tests green).

Run: `node --check dashboard/public/spectrum.js`
Expected: no output (valid syntax).

- [ ] **Step 5: Commit**

```bash
git add dashboard/public/spectrum.js test/spectrum.test.js
git commit -m "feat(dashboard): data-driven detectionX + psdColor for the waterfall" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: vendor mqtt.js + MqttScanClient

**Files:**
- Create: `dashboard/public/vendor/mqtt.min.js`
- Modify: `dashboard/public/mqtt-scan.js`

- [ ] **Step 1: Vendor the mqtt.js browser build**

Download a pinned mqtt.js v5 browser bundle into `dashboard/public/vendor/`:

```bash
mkdir -p dashboard/public/vendor
curl -fsSL https://unpkg.com/mqtt@5.10.1/dist/mqtt.min.js -o dashboard/public/vendor/mqtt.min.js
```

Verify it downloaded a real bundle (non-trivial size, exposes the global):

```bash
wc -c dashboard/public/vendor/mqtt.min.js          # expect > 100000 bytes
grep -c "mqtt" dashboard/public/vendor/mqtt.min.js  # expect > 0
```
Expected: a file over ~100 KB; grep count > 0. (If unpkg is unreachable, try `https://cdn.jsdelivr.net/npm/mqtt@5.10.1/dist/mqtt.min.js`.)

- [ ] **Step 2: Append `MqttScanClient` to `dashboard/public/mqtt-scan.js`**

```javascript
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
    client.on('connect', () => client.subscribe(['fpv/+/spectrum', 'fpv/+/detection', 'fpv/+/status']));
    client.on('message', (topic, buf) => {
      try { reduce(this.store, topic, buf.toString(), { depth: this.depth }); } catch { return; }
      if (!raf) raf = requestAnimationFrame(notify);
    });
    this.client = client;
  }
}
```

- [ ] **Step 3: Verify**

Run: `node --check dashboard/public/mqtt-scan.js`
Expected: no output (valid syntax; the browser globals are referenced only inside `connect`, so importing the pure exports in node still works).

Run: `npm test`
Expected: PASS (the reducer tests still green; the new class isn't unit-tested).

- [ ] **Step 4: Commit**

```bash
git add dashboard/public/vendor/mqtt.min.js dashboard/public/mqtt-scan.js
git commit -m "feat(dashboard): vendored mqtt.js + MqttScanClient (WSS subscribe -> store)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: spectrum.js — line + waterfall DOM render (3 bands in a row)

**Files:**
- Modify: `dashboard/public/spectrum.js`

This reworks the DOM render to draw from the MQTT store (data-driven bands), with a live PSD line + a scrolling waterfall per band. Browser-only — validated with `node --check` + manual.

- [ ] **Step 1: Replace the DOM render section**

In `dashboard/public/spectrum.js`, replace everything from the `// ---- DOM rendering` comment to the end of `bandChart` (i.e. `renderSpectrum`, `scannerBlock`, `bandChart`) with the following. Keep `el`, `detectionTable`, `escapeHtml` (below `bandChart`) unchanged.

```javascript
// ---- DOM rendering (browser only; validated with `node --check` + manual) ----

// renderSpectrum(container, scanners, store): scanners = registry devices (kind=scanner, for
// name/location/management); store = MqttScanClient store keyed by scanner id (live data).
export function renderSpectrum(container, scanners, store = {}, highlightKeys = new Set()) {
  container.innerHTML = '';
  for (const s of scanners) container.appendChild(scannerBlock(s, store[s.id], highlightKeys));
}

function scannerBlock(s, live, highlightKeys) {
  const block = el('div', 'scan-block');
  block.dataset.scannerId = s.id;
  const online = !!(live && live.online);

  block.appendChild(el('div', 'scan-head', `
    <strong>${escapeHtml(s.name)}</strong> <small>${escapeHtml(s.location || '')}</small>
    <span class="badge ${online ? 'on' : 'off'}">${online ? 'ONLINE' : 'OFFLINE'}</span>
    <span class="scan-actions">
      <button class="tile-btn" data-act="info" title="Інфо">🔑</button>
      <button class="tile-btn" data-act="edit" title="Редагувати">✏️</button>
      <button class="tile-btn" data-act="del" title="Видалити">🗑</button>
    </span>`));

  const bandIds = live ? Object.keys(live.bands) : [];
  if (!online && !bandIds.length) {
    block.appendChild(el('p', 'scan-empty', 'немає даних'));
    return block;
  }

  const det = (live && live.detection) || { detections: [], occupancy: {} };

  // occupancy strip (data-driven over the bands we have)
  const occ = el('div', 'scan-occ');
  for (const band of bandIds) {
    const frac = (det.occupancy && det.occupancy[band]) || 0;
    occ.appendChild(el('div', 'occ-bar', `
      <span class="occ-label">${escapeHtml(band)}</span>
      <span class="occ-track"><span class="occ-fill" style="width:${Math.round(frac * 100)}%"></span></span>
      <span class="occ-val">${fmtPct(frac)}</span>`));
  }
  block.appendChild(occ);

  // 3 bands in a row: each = live PSD line + scrolling waterfall
  const charts = el('div', 'scan-charts');
  for (const band of bandIds) {
    const range = live.bands[band] || {};
    const psd = (live.latestPsd && live.latestPsd[band]) || [];
    const frames = (live.waterfalls && live.waterfalls[band]) || [];
    const dets = (det.detections || []).filter((d) => d.band === band);
    charts.appendChild(bandCell(band, range, psd, frames, dets));
  }
  block.appendChild(charts);

  block.appendChild(detectionTable(det.detections || [], highlightKeys));
  return block;
}

function bandCell(band, range, psd, frames, dets) {
  const wrap = el('div', 'band-cell');
  wrap.appendChild(el('div', 'band-label', escapeHtml(band)));
  const w = 240;

  // live PSD line + detection marks
  const lh = 44;
  const line = document.createElement('canvas');
  line.width = w; line.height = lh; line.className = 'chart-line';
  const lc = line.getContext('2d');
  const pts = psdToPoints(psd, w, lh);
  if (pts.length) {
    lc.strokeStyle = '#6ca0ff'; lc.lineWidth = 1; lc.beginPath();
    pts.forEach((p, i) => (i ? lc.lineTo(p.x, p.y) : lc.moveTo(p.x, p.y)));
    lc.stroke();
  }
  for (const d of dets) {
    const x = detectionX(d.center_mhz, range.low_mhz, range.high_mhz, w);
    lc.strokeStyle = classColor(d.class); lc.lineWidth = 2;
    lc.beginPath(); lc.moveTo(x, 0); lc.lineTo(x, lh); lc.stroke();
  }
  wrap.appendChild(line);

  // waterfall: one pixel row per frame, newest on top
  const rows = frames.length;
  const wf = document.createElement('canvas');
  wf.width = w; wf.height = Math.max(1, rows); wf.className = 'chart-wf';
  const wc = wf.getContext('2d');
  for (let r = 0; r < rows; r += 1) {
    const f = frames[rows - 1 - r];           // newest first
    const p = f.psd || [];
    const n = p.length;
    if (!n) continue;
    for (let x = 0; x < w; x += 1) {
      const idx = n === 1 ? 0 : Math.round((x / (w - 1)) * (n - 1));
      wc.fillStyle = psdColor(p[idx]);
      wc.fillRect(x, r, 1, 1);
    }
  }
  wrap.appendChild(wf);
  return wrap;
}
```

- [ ] **Step 2: Verify syntax + unit suite**

Run: `node --check dashboard/public/spectrum.js`
Expected: no output.

Run: `npm test`
Expected: PASS (pure-helper tests unaffected).

- [ ] **Step 3: Commit**

```bash
git add dashboard/public/spectrum.js
git commit -m "feat(dashboard): per-band line + waterfall render from the MQTT store" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: app.js — wire MQTT alongside SSE

**Files:**
- Modify: `dashboard/public/app.js`

Browser-only — validated with `node --check` + manual.

- [ ] **Step 1: Add the imports + MQTT client**

At the top of `dashboard/public/app.js`, update the imports:

```javascript
import { startWhep } from '/whep.js';
import { splitByKind, renderSpectrum } from '/spectrum.js';
import { diffNewKeys, SoundAlerter } from '/alert.js';
import { MqttScanClient } from '/mqtt-scan.js';
```

Add module state near the other top-level `let`s:

```javascript
const scanClient = new MqttScanClient();
let scannersFromRegistry = [];
```

- [ ] **Step 2: Re-point scan rendering at the store**

Replace the existing scanner/alert block inside `render(devices)` — the lines that compute `allDets`, `diffNewKeys`, `prevScanKeys`, and call `renderSpectrumPanel(scanners, ...)` — with just capturing the registry scanners (cameras still render below as before):

```javascript
  const { cameras, scanners } = splitByKind(devices);
  scannersFromRegistry = scanners;
  for (const d of devices) lastById.set(d.id, d);

  document.getElementById('summary').textContent =
    `${cameras.filter((d) => d.online).length}/${cameras.length} онлайн`;

  renderScan();   // draw from the MQTT store (presence/data), using the latest registry metadata
```

Keep the camera loop and the tile-drop loop below unchanged.

- [ ] **Step 3: Replace `renderSpectrumPanel` with a store-driven `renderScan`**

Replace the `renderSpectrumPanel(...)` function with:

```javascript
function renderScan() {
  const scanners = scannersFromRegistry;
  if (!scanners.length) {
    spectrumPanel.classList.add('hidden');
    spectrumPanel.innerHTML = '';
    return;
  }
  const store = scanClient.store;
  const allDets = scanners.flatMap((s) => (store[s.id] && store[s.id].detection && store[s.id].detection.detections) || []);
  const { keys, newKeys } = diffNewKeys(prevScanKeys, allDets);
  if (prevScanKeys !== null && newKeys.length && alerter.armed) alerter.beep();
  prevScanKeys = Object.keys(store).length ? keys : null;
  spectrumPanel.classList.remove('hidden');
  renderSpectrum(spectrumPanel, scanners, store, new Set(newKeys));
}
```

- [ ] **Step 4: Start the MQTT client in `init()`**

In the `init()` IIFE, after `render(first)` and before/after `connectSSE()`, add the MQTT connect:

```javascript
(async function init() {
  cfg = await loadConfig();
  if (!cfg) return;
  const first = await fetch('/api/devices').then((r) => r.json());
  render(first);
  connectSSE();
  try {
    const mq = await fetch('/api/mqtt').then((r) => (r.ok ? r.json() : null));
    if (mq && mq.url) scanClient.connect(mq, () => renderScan());
  } catch { /* no broker creds -> scan panel stays empty until available */ }
})();
```

- [ ] **Step 5: Verify**

Run: `node --check dashboard/public/app.js`
Expected: no output.

Run: `npm test`
Expected: PASS (unchanged; app.js is browser-only).

- [ ] **Step 6: Commit**

```bash
git add dashboard/public/app.js
git commit -m "feat(dashboard): subscribe scan data over MQTT-WSS alongside the camera SSE" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: styles.css + README

**Files:**
- Modify: `dashboard/public/styles.css`
- Modify: `README.md`

- [ ] **Step 1: Add waterfall + 3-column band styles**

Append to `dashboard/public/styles.css`:

```css
/* spectrum: 3 bands in a row, each = line + waterfall */
.scan-charts { display: flex; gap: 8px; margin-top: 8px; }
.scan-charts .band-cell { flex: 1; min-width: 0; }
.band-cell .band-label { font-size: 11px; color: #9aa0a6; margin-bottom: 3px; }
.band-cell .chart-line { width: 100%; height: 44px; background: #0c1118; border-radius: 4px 4px 0 0; display: block; }
.band-cell .chart-wf { width: 100%; height: 90px; image-rendering: pixelated; border-radius: 0 0 4px 4px; display: block; }
```

(`chart-wf` is a short canvas, one row per frame; CSS stretches it to 90px — `image-rendering: pixelated` keeps the rows crisp.)

- [ ] **Step 2: Update the README**

In `README.md`, in the scan/spectrum section, replace any description of the dashboard reading scan data via `POST /api/telemetry` / SSE with: the dashboard subscribes to `fpv/<id>/{spectrum,detection,status}` over MQTT-WSS (creds from `GET /api/mqtt`), renders a per-band line + waterfall, and joins live data to registry scanners by id (the Pi `SCAN_ID` must equal the registry scanner id). Note the WG-vs-public WSS caveat (spec §8).

- [ ] **Step 3: Verify + full suite**

Run: `npm test`
Expected: PASS (full suite).

Run: `node --check dashboard/public/spectrum.js && node --check dashboard/public/mqtt-scan.js && node --check dashboard/public/app.js`
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add dashboard/public/styles.css README.md
git commit -m "docs(dashboard): waterfall styles + README MQTT scan section" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage** (spec § → task):
- §2/§3 MQTT-WSS subscribe + creds from `/api/mqtt` → Task 4 (MqttScanClient) + Task 6 (app wiring). Pure store → Task 2.
- §4.1 reducer + ring buffer → Task 2; MqttScanClient + vendored mqtt.js → Task 4.
- §4.2 psdColor + data-driven detectionX → Task 3; line+waterfall render, data-driven bands → Task 5.
- §4.3 app.js (MQTT alongside SSE, scan render + alert + online from store) → Task 6.
- §4.4 server.js remove telemetry + freshness, scanner-create response → Task 1 (+ its tests).
- §5 testing: reducer/psdColor/detectionX (node --test) → Tasks 2/3; server tests → Task 1; node --check → Tasks 3–7. Ops verify → deploy.
- §8 caveats (WG vs public WSS) → README (Task 7).

**Placeholder scan:** no TBD/TODO; full code for every tested unit + the DOM render + app wiring; the only download (mqtt.min.js) has a pinned URL + size/grep verification + a fallback mirror.

**Type/name consistency:** store shape `{online,status_ts,detection:{ts,detections,occupancy},bands:{<id>:{low_mhz,high_mhz}},latestPsd:{<id>:[]},waterfalls:{<id>:[{ts,psd}]}}` — produced by `reduce` (Task 2), consumed by `renderSpectrum`/`bandCell` (Task 5) and `renderScan` (Task 6) identically. `detectionX(centerMhz, lowMhz, highMhz, width)` — same signature in Task 3 (def+test) and Task 5 (call, via `range.low_mhz`/`range.high_mhz`). `psdColor(db)` — Task 3 def, Task 5 use. Scanner-create response `{scanner:{topicPrefix:'fpv/<id>'}}` — Task 1 server + test. Topics `fpv/+/{spectrum,detection,status}` — Task 4 subscribe, Task 2 parse.

**Interim state:** Tasks 3→5 leave the browser scan render briefly inconsistent (old `renderSpectrum` vs new helper signatures); `node --check` + `npm test` stay green throughout; final state correct after Task 6. Acceptable for a one-sitting branch.

**Deploy (not a plan task):** after merge (with SP-A + SP-B live), `docker compose up -d --no-deps dashboard`; verify the panel shows ONLINE + live line/waterfall + detections over public HTTPS; LWT flips OFFLINE when the Pi stops.
