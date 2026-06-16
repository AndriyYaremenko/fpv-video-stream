# SP4 Dashboard Spectrum Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render the HackRF scanner's telemetry (occupancy, per-band spectrum, detections) in a dedicated "Spectrum" panel above the camera grid, treating scanner devices distinctly from cameras.

**Architecture:** Add a `kind` (`camera`|`scanner`) field to the device registry. The server marks scanners online from telemetry freshness and excludes them from MediaMTX config. The vanilla-JS frontend splits devices by kind: cameras keep the WHEP tile grid; scanners feed a new `spectrum.js` panel (occupancy bars + canvas line charts + detection table). The scanner payload already arrives as `d.telemetry` — no new endpoint.

**Tech Stack:** Node + Express (ESM), `node --test` + `node:assert/strict`, vanilla JS/Canvas frontend, js-yaml registry. Tests run via `npm test`.

Spec: `docs/superpowers/specs/2026-06-16-sp4-dashboard-spectrum-panel-design.md`

---

## File Structure

```
lib/registry.js              (change: addDevice accepts/validates/stores `kind`)
lib/render-config.js         (change: scanners excluded from authInternalUsers)
lib/status.js                (change: mergeStatus passes through `kind`)
dashboard/server.js          (change: scanner online-from-telemetry; POST accepts kind; scanner response)
dashboard/public/spectrum.js (new: pure helpers + DOM renderSpectrum)
dashboard/public/index.html  (change: <section id="spectrum-panel">)
dashboard/public/app.js      (change: split by kind; panel wiring; kind selector; scanner modal/actions)
dashboard/public/styles.css  (change: spectrum panel/chart/table/occupancy styles)
test/registry.test.js        (add cases)
test/render-config.test.js   (add cases)
test/status.test.js          (add cases)
test/server.test.js          (add cases)
test/spectrum.test.js        (new: pure-helper tests)
README.md                    (change: scanner-node dashboard section)
```

Pure data-shaping logic lives in `spectrum.js` as exported functions (unit-tested via `node --test`, which imports the module). DOM/canvas rendering in the same module is browser-only and not unit-tested (consistent with `app.js`/`whep.js`); its files are guarded with `node --check` for parse errors.

---

## Task 1: Registry `kind` field

**Files:**
- Modify: `lib/registry.js` (the `addDevice` function + a `VALID_KINDS` constant)
- Test: `test/registry.test.js`

- [ ] **Step 1: Write the failing tests**

Append to `test/registry.test.js`:

```javascript
test('addDevice defaults kind to camera and stores an explicit scanner kind', () => {
  const reg = { devices: [] };
  const cam = addDevice(reg, { id: 'cam-1', name: 'Cam' });
  assert.equal(cam.kind, 'camera');
  const scan = addDevice(reg, { id: 'scan-01', name: 'Scanner', kind: 'scanner' });
  assert.equal(scan.kind, 'scanner');
});

test('addDevice rejects an invalid kind', () => {
  const reg = { devices: [] };
  assert.throws(() => addDevice(reg, { id: 'x-1', name: 'x', kind: 'drone' }), /invalid kind/i);
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm test`
Expected: the two new tests FAIL (`cam.kind` is `undefined`; the invalid-kind call does not throw).

- [ ] **Step 3: Implement**

In `lib/registry.js`, add a constant after `const ID_RE = ...`:

```javascript
const VALID_KINDS = ['camera', 'scanner'];
```

Replace the `addDevice` function with:

```javascript
export function addDevice(reg, { id, name, location, kind }) {
  if (!validateId(id)) {
    throw new Error(`invalid device id "${id}" (use lowercase a-z, 0-9, -, _; start alphanumeric)`);
  }
  const deviceKind = kind || 'camera';
  if (!VALID_KINDS.includes(deviceKind)) {
    throw new Error(`invalid kind "${kind}" (expected camera or scanner)`);
  }
  reg.devices = reg.devices || [];
  if (reg.devices.some((d) => d.id === id)) {
    throw new Error(`device "${id}" already exists`);
  }
  const device = { id, name: name || id, location: location || '', kind: deviceKind, publish_pass: genSecret() };
  reg.devices.push(device);
  return device;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm test`
Expected: PASS — all registry tests green (existing ones unaffected: they don't pass `kind`, so it defaults to `camera`).

- [ ] **Step 5: Commit**

```bash
git add lib/registry.js test/registry.test.js
git commit -m "feat(dashboard): registry kind field (camera|scanner)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Exclude scanners from MediaMTX config

**Files:**
- Modify: `lib/render-config.js` (the device map inside `buildConfigObject`)
- Test: `test/render-config.test.js`

- [ ] **Step 1: Write the failing test**

Append to `test/render-config.test.js`:

```javascript
test('scanner devices get no publish user in MediaMTX config', () => {
  const regWithScanner = {
    read_user: 'viewer', read_pass: 'readsecret',
    devices: [
      { id: 'pi-01', name: 'A', location: 'x', kind: 'camera', publish_pass: 'p1' },
      { id: 'scan-01', name: 'S', location: 'z', kind: 'scanner', publish_pass: 'ps' },
    ],
  };
  const c = buildConfigObject(regWithScanner, opts);
  assert.ok(c.authInternalUsers.find((u) => u.user === 'pi-01'));
  assert.equal(c.authInternalUsers.find((u) => u.user === 'scan-01'), undefined);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test`
Expected: FAIL — `scan-01` currently gets a publish user.

- [ ] **Step 3: Implement**

In `lib/render-config.js`, inside `buildConfigObject`, change the device-users spread from:

```javascript
    ...devices.map((d) => ({
```

to:

```javascript
    ...devices.filter((d) => d.kind !== 'scanner').map((d) => ({
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm test`
Expected: PASS — new test green; the existing `authInternalUsers.length === 4` test still holds (its devices have no `kind`, so none are filtered).

- [ ] **Step 5: Commit**

```bash
git add lib/render-config.js test/render-config.test.js
git commit -m "feat(dashboard): exclude scanners from MediaMTX publish users" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: `mergeStatus` passes through `kind`

**Files:**
- Modify: `lib/status.js` (the returned object in `mergeStatus`)
- Test: `test/status.test.js`

- [ ] **Step 1: Write the failing test**

Append to `test/status.test.js`:

```javascript
test('mergeStatus passes through kind, defaulting missing to camera', () => {
  const r = { devices: [
    { id: 'pi-01', name: 'A', location: 'x' },
    { id: 'scan-01', name: 'S', location: 'z', kind: 'scanner' },
  ] };
  const out = mergeStatus(r, pathsList, now);
  assert.equal(out.find((d) => d.id === 'pi-01').kind, 'camera');
  assert.equal(out.find((d) => d.id === 'scan-01').kind, 'scanner');
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test`
Expected: FAIL — `kind` is `undefined` on the merged objects.

- [ ] **Step 3: Implement**

In `lib/status.js`, in the object returned by `mergeStatus`, add a `kind` line right after `location`:

```javascript
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm test`
Expected: PASS — new test green; existing status tests unaffected.

- [ ] **Step 5: Commit**

```bash
git add lib/status.js test/status.test.js
git commit -m "feat(dashboard): mergeStatus passes through device kind" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Server — scanner online-from-telemetry + POST `kind`

**Files:**
- Modify: `dashboard/server.js` (a `SCANNER_FRESH_MS` const, `snapshot()`, and `POST /api/devices`)
- Test: `test/server.test.js`

- [ ] **Step 1: Write the failing tests**

Append to `test/server.test.js`:

```javascript
test('POST /api/devices with kind=scanner returns no push, includes telemetry hint', async () => {
  const reg = { read_user: 'viewer', read_pass: 'rpw', devices: [] };
  const { server, base } = await startWith(reg);
  const cookie = await login(base);
  const res = await fetch(`${base}/api/devices`, {
    method: 'POST', headers: { cookie, 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: 'scan-01', name: 'Scanner', kind: 'scanner' }),
  });
  const body = await res.json();
  assert.equal(res.status, 201);
  assert.equal(body.device.kind, 'scanner');
  assert.equal(body.push, undefined);
  assert.equal(body.scanner.telemetryPath, '/api/telemetry/scan-01');
  server.close();
});

test('scanner online is derived from telemetry freshness', async () => {
  const reg = { read_user: 'viewer', read_pass: 'rpw',
    devices: [{ id: 'scan-01', name: 'S', location: '', kind: 'scanner', publish_pass: 'p' }] };
  const { server, base } = await startWith(reg);
  const cookie = await login(base);
  // no telemetry yet -> offline
  let body = await (await fetch(`${base}/api/devices`, { headers: { cookie } })).json();
  assert.equal(body.find((d) => d.id === 'scan-01').online, false);
  // post telemetry -> online, no bitrate
  await fetch(`${base}/api/telemetry/scan-01`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ detections: [], occupancy: {}, spectrum: {} }),
  });
  body = await (await fetch(`${base}/api/devices`, { headers: { cookie } })).json();
  const scan = body.find((d) => d.id === 'scan-01');
  assert.equal(scan.online, true);
  assert.equal(scan.bitrateKbps, null);
  server.close();
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm test`
Expected: FAIL — POST returns `push` for a scanner and no `scanner` hint; scanner online stays `false` after telemetry.

- [ ] **Step 3: Implement**

In `dashboard/server.js`, add a constant just inside `createApp` (next to the `telemetry`/`samples` maps):

```javascript
  const SCANNER_FRESH_MS = 15000; // a scanner is "online" if it posted telemetry within this window
```

Replace the body of the `snapshot()` loop so scanner liveness comes from telemetry:

```javascript
  async function snapshot() {
    const paths = await getPaths();
    const now = Date.now();
    const merged = mergeStatus(registry, paths, now);
    for (const d of merged) {
      const prev = samples.get(d.id);
      d.bitrateKbps = d.online ? computeBitrateKbps(prev?.bytes, prev?.ts, d.bytesReceived, now) : null;
      if (d.online) samples.set(d.id, { bytes: d.bytesReceived, ts: now });
      const tel = telemetry.get(d.id) || null;
      d.telemetry = tel;
      if (d.kind === 'scanner') {
        d.online = !!tel && (now - tel._ts) < SCANNER_FRESH_MS;
        d.bitrateKbps = null;
      }
    }
    return merged;
  }
```

Replace the `POST /api/devices` handler with:

```javascript
  app.post('/api/devices', requireAuth, (req, res) => {
    const { id, name, location, kind } = req.body || {};
    try {
      const finalId = (id && String(id).trim()) ? String(id).trim() : nextDeviceId(registry);
      const device = addDevice(registry, { id: finalId, name, location, kind });
      config.persistRegistry(registry);
      if (device.kind === 'scanner') {
        return res.status(201).json({ device, scanner: { telemetryPath: `/api/telemetry/${device.id}` } });
      }
      res.status(201).json({ device, push: pushFor(device) });
    } catch (e) {
      const code = /already exists/.test(e.message) ? 409 : 400;
      res.status(code).json({ error: e.message });
    }
  });
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm test`
Expected: PASS — both new tests green; all existing server tests (camera POST returns push, telemetry surfaces, etc.) still pass.

- [ ] **Step 5: Commit**

```bash
git add dashboard/server.js test/server.test.js
git commit -m "feat(dashboard): scanner online-from-telemetry + kind in device create" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: `spectrum.js` pure helpers

**Files:**
- Create: `dashboard/public/spectrum.js` (pure helpers only in this task; DOM render added in Task 6)
- Test: `test/spectrum.test.js`

- [ ] **Step 1: Write the failing tests**

Create `test/spectrum.test.js`:

```javascript
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { splitByKind, classColor, fmtPct, psdToPoints, detectionX, BAND_RANGES } from '../dashboard/public/spectrum.js';

test('splitByKind separates scanners from cameras (missing kind = camera)', () => {
  const { cameras, scanners } = splitByKind([
    { id: 'a', kind: 'camera' }, { id: 'b' }, { id: 's', kind: 'scanner' },
  ]);
  assert.deepEqual(cameras.map((d) => d.id), ['a', 'b']);
  assert.deepEqual(scanners.map((d) => d.id), ['s']);
});

test('classColor maps the three classes distinctly, unknown is the default', () => {
  assert.notEqual(classColor('analog'), classColor('digital'));
  assert.equal(classColor('whatever'), classColor('unknown'));
});

test('fmtPct rounds a fraction to a percentage and tolerates undefined', () => {
  assert.equal(fmtPct(0.5), '50%');
  assert.equal(fmtPct(0), '0%');
  assert.equal(fmtPct(undefined), '0%');
});

test('psdToPoints scales endpoints to the box', () => {
  const pts = psdToPoints([-100, -20], 100, 50, -100, -20);
  assert.equal(pts.length, 2);
  assert.equal(pts[0].x, 0);
  assert.equal(pts[1].x, 100);
  assert.ok(Math.abs(pts[0].y - 50) < 1e-9);   // -100 dBm -> bottom of box
  assert.ok(Math.abs(pts[1].y - 0) < 1e-9);    // -20 dBm -> top of box
});

test('psdToPoints clamps out-of-range power into the box', () => {
  const pts = psdToPoints([10, -300], 10, 40, -100, -20);
  for (const p of pts) assert.ok(p.y >= 0 && p.y <= 40);
});

test('detectionX maps center freq within band and clamps out-of-range', () => {
  assert.ok(Math.abs(detectionX(5795, '5.8G', 300) - 150) < 1); // band 5645..5945, mid -> 150
  assert.equal(detectionX(1000, '5.8G', 300), 0);               // below -> 0
  assert.equal(detectionX(9999, '5.8G', 300), 300);             // above -> width
});

test('BAND_RANGES covers the three FPV bands', () => {
  assert.deepEqual(Object.keys(BAND_RANGES).sort(), ['1.2G', '2.4G', '5.8G']);
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm test`
Expected: FAIL — `Cannot find module '../dashboard/public/spectrum.js'`.

- [ ] **Step 3: Implement**

Create `dashboard/public/spectrum.js`:

```javascript
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm test`
Expected: PASS — all 7 spectrum helper tests green.

- [ ] **Step 5: Commit**

```bash
git add dashboard/public/spectrum.js test/spectrum.test.js
git commit -m "feat(dashboard): spectrum.js pure helpers (split/scale/color)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: `spectrum.js` DOM render (`renderSpectrum`)

**Files:**
- Modify: `dashboard/public/spectrum.js` (append DOM rendering; browser-only, not unit-tested)

- [ ] **Step 1: Append the DOM render code**

Append to `dashboard/public/spectrum.js`:

```javascript
// ---- DOM rendering (browser only; not unit-tested, validated with `node --check` + manual) ----

export function renderSpectrum(container, scanners) {
  container.innerHTML = '';
  for (const s of scanners) container.appendChild(scannerBlock(s));
}

function el(tag, cls, html) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html != null) e.innerHTML = html;
  return e;
}

function scannerBlock(s) {
  const tel = s.telemetry || {};
  const block = el('div', 'scan-block');
  block.dataset.scannerId = s.id;

  const head = el('div', 'scan-head', `
    <strong>${escapeHtml(s.name)}</strong> <small>${escapeHtml(s.location || '')}</small>
    <span class="badge ${s.online ? 'on' : 'off'}">${s.online ? 'ONLINE' : 'OFFLINE'}</span>
    <span class="scan-actions">
      <button class="tile-btn" data-act="info" title="Інфо телеметрії">🔑</button>
      <button class="tile-btn" data-act="edit" title="Редагувати">✏️</button>
      <button class="tile-btn" data-act="del" title="Видалити">🗑</button>
    </span>`);
  block.appendChild(head);

  if (!s.online || !tel.detections) {
    block.appendChild(el('p', 'scan-empty', 'немає даних'));
    return block;
  }

  const occ = el('div', 'scan-occ');
  for (const band of Object.keys(BAND_RANGES)) {
    const frac = (tel.occupancy && tel.occupancy[band]) || 0;
    occ.appendChild(el('div', 'occ-bar', `
      <span class="occ-label">${band}</span>
      <span class="occ-track"><span class="occ-fill" style="width:${Math.round(frac * 100)}%"></span></span>
      <span class="occ-val">${fmtPct(frac)}</span>`));
  }
  block.appendChild(occ);

  const charts = el('div', 'scan-charts');
  for (const band of Object.keys(BAND_RANGES)) {
    const psd = (tel.spectrum && tel.spectrum[band]) || [];
    const dets = (tel.detections || []).filter((d) => d.band === band);
    charts.appendChild(bandChart(band, psd, dets));
  }
  block.appendChild(charts);

  block.appendChild(detectionTable(tel.detections || []));
  return block;
}

function bandChart(band, psd, dets) {
  const wrap = el('div', 'band-chart');
  wrap.appendChild(el('div', 'band-label', band));
  const w = 240;
  const h = 60;
  const canvas = document.createElement('canvas');
  canvas.width = w;
  canvas.height = h;
  canvas.className = 'chart-canvas';
  const ctx = canvas.getContext('2d');
  const pts = psdToPoints(psd, w, h);
  if (pts.length) {
    ctx.strokeStyle = '#6ca0ff';
    ctx.lineWidth = 1;
    ctx.beginPath();
    pts.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
    ctx.stroke();
  }
  for (const d of dets) {
    const x = detectionX(d.center_mhz, band, w);
    ctx.strokeStyle = classColor(d.class);
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, h);
    ctx.stroke();
  }
  wrap.appendChild(canvas);
  return wrap;
}

function detectionTable(dets) {
  if (!dets.length) return el('p', 'scan-empty', 'немає активних передавачів');
  const sorted = [...dets].sort((a, b) => (b.power_dbm ?? -999) - (a.power_dbm ?? -999));
  const table = el('table', 'scan-table',
    '<thead><tr><th>Бенд</th><th>Частота</th><th>Клас</th><th>RSSI</th><th>Смуга</th><th>Впевн.</th></tr></thead>');
  const tb = el('tbody');
  for (const d of sorted) {
    const freq = `${fmtFreq(d.center_mhz)}${d.channel ? ` (${escapeHtml(d.channel)})` : ''}`;
    tb.appendChild(el('tr', null, `
      <td>${escapeHtml(d.band)}</td>
      <td>${freq}</td>
      <td><span class="cls" style="color:${classColor(d.class)}">${escapeHtml(d.class)}</span></td>
      <td>${d.power_dbm ?? '—'} dBm</td>
      <td>${d.bandwidth_mhz ?? '—'} МГц</td>
      <td>${fmtPct(d.confidence)}</td>`));
  }
  table.appendChild(tb);
  return table;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
```

- [ ] **Step 2: Verify the module parses and tests still pass**

Run: `node --check dashboard/public/spectrum.js`
Expected: no output (exit 0 = valid).

Run: `npm test`
Expected: PASS — the Task 5 pure-helper tests still import and pass (adding DOM functions doesn't break import, since `document` is only referenced inside functions).

- [ ] **Step 3: Commit**

```bash
git add dashboard/public/spectrum.js
git commit -m "feat(dashboard): renderSpectrum panel (occupancy + charts + table)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: Panel markup, wiring, and styles

**Files:**
- Modify: `dashboard/public/index.html` (add the panel section)
- Modify: `dashboard/public/app.js` (import + split by kind + render the panel)
- Modify: `dashboard/public/styles.css` (panel/chart/table/occupancy styles)

- [ ] **Step 1: Add the panel section to `index.html`**

In `dashboard/public/index.html`, insert the panel between `</header>` and `<main id="grid">`:

```html
  </header>

  <section id="spectrum-panel" class="spectrum-panel hidden" aria-live="polite"></section>

  <main id="grid" class="grid" aria-live="polite"></main>
```

- [ ] **Step 2: Wire `app.js`**

In `dashboard/public/app.js`, add the import right after the existing `startWhep` import:

```javascript
import { splitByKind, renderSpectrum } from '/spectrum.js';
```

Add the panel element reference right after `const grid = document.getElementById('grid');`:

```javascript
const spectrumPanel = document.getElementById('spectrum-panel');
```

Replace the entire `render(devices)` function with:

```javascript
function render(devices) {
  const { cameras, scanners } = splitByKind(devices);
  for (const d of devices) lastById.set(d.id, d);

  document.getElementById('summary').textContent =
    `${cameras.filter((d) => d.online).length}/${cameras.length} онлайн`;

  renderSpectrumPanel(scanners);

  for (const d of cameras) {
    const el = tileEl(d);
    el.querySelector('.tile-meta strong').textContent = d.name;     // reflect edits
    el.querySelector('.tile-meta small').textContent = d.location;
    el.classList.toggle('offline', !d.online);
    const badge = el.querySelector(`#badge-${d.id}`);
    badge.textContent = d.online ? 'ONLINE' : 'OFFLINE';
    badge.className = `badge ${d.online ? 'on' : 'off'}`;

    el.querySelector(`#stats-${d.id}`).textContent = d.online
      ? `${fmtBitrate(d.bitrateKbps)} · ${fmtUptime(d.uptimeSec)} · 👁 ${d.readers}` : '';
    el.querySelector(`#tel-${d.id}`).textContent = d.telemetry ? telemetryLine(d.telemetry) : '';

    const state = players.get(d.id) || {};
    if (d.online && !state.player && !state.starting) {
      startPlayer(d);
    } else if (!d.online && state.player) {
      state.player.close();
      players.set(d.id, { player: null });
    }
  }

  // Drop tiles for cameras that no longer exist (e.g. deleted or converted).
  const ids = new Set(cameras.map((d) => d.id));
  for (const el of grid.querySelectorAll('.tile')) {
    const id = el.id.replace('tile-', '');
    if (!ids.has(id)) {
      const st = players.get(id);
      if (st && st.player) st.player.close();
      players.delete(id);
      lastById.delete(id);
      el.remove();
    }
  }
}

function renderSpectrumPanel(scanners) {
  if (!scanners.length) {
    spectrumPanel.classList.add('hidden');
    spectrumPanel.innerHTML = '';
    return;
  }
  spectrumPanel.classList.remove('hidden');
  renderSpectrum(spectrumPanel, scanners);
}
```

- [ ] **Step 3: Add styles to `styles.css`**

Append to `dashboard/public/styles.css`:

```css
/* spectrum panel (scanner data) */
.spectrum-panel { margin:.6rem; padding:.7rem; background:var(--panel); border:1px solid var(--line); border-radius:10px; }
.spectrum-panel.hidden { display:none; }
.scan-block { padding:.4rem 0; border-bottom:1px solid var(--line); }
.scan-block:last-child { border-bottom:none; }
.scan-head { display:flex; align-items:center; gap:.5rem; flex-wrap:wrap; }
.scan-head strong { font-size:.95rem; }
.scan-head small { color:#9aa4b2; }
.scan-actions { display:flex; gap:.3rem; margin-left:auto; }
.scan-empty { color:#9aa4b2; font-size:.85rem; margin:.4rem 0; }
.scan-occ { display:flex; gap:1rem; flex-wrap:wrap; margin:.5rem 0; }
.occ-bar { display:flex; align-items:center; gap:.4rem; font-size:.75rem; color:#cbd5e1; }
.occ-label { width:2.6rem; }
.occ-track { width:90px; height:8px; background:#0b0e13; border:1px solid var(--line); border-radius:4px; overflow:hidden; }
.occ-fill { display:block; height:100%; background:var(--accent); }
.occ-val { width:2.4rem; text-align:right; }
.scan-charts { display:flex; gap:.8rem; flex-wrap:wrap; margin:.4rem 0; }
.band-chart { display:flex; flex-direction:column; gap:.2rem; }
.band-label { font-size:.7rem; color:#9aa4b2; }
.chart-canvas { background:#0b0e13; border:1px solid var(--line); border-radius:6px; }
.scan-table { width:100%; border-collapse:collapse; font-size:.78rem; margin-top:.4rem; }
.scan-table th, .scan-table td { text-align:left; padding:.25rem .5rem; border-bottom:1px solid var(--line); }
.scan-table th { color:#9aa4b2; font-weight:600; }
.scan-table .cls { font-weight:700; }
```

- [ ] **Step 4: Verify parse + suite**

Run: `node --check dashboard/public/app.js`
Expected: no output (valid).

Run: `npm test`
Expected: PASS — no test imports `app.js`, so this confirms the rest of the suite is still green after the edits.

- [ ] **Step 5: (Optional) manual browser check**

The DOM panel is validated by eye on a running stack. With a real scanner registered (Task 9 docs) and the daemon posting, the panel appears above the grid showing occupancy bars, per-band charts, and the detection table. No automated browser harness exists in this repo (same as `app.js`/`whep.js`).

- [ ] **Step 6: Commit**

```bash
git add dashboard/public/index.html dashboard/public/app.js dashboard/public/styles.css
git commit -m "feat(dashboard): spectrum panel markup, split-by-kind wiring, styles" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 8: Add-device kind selector + scanner modal + panel actions

**Files:**
- Modify: `dashboard/public/app.js` (form kind selector, submit branch, scanner info modal, panel action delegation)

- [ ] **Step 1: Add the kind selector to the add form**

In `dashboard/public/app.js`, in `openAddForm()`, add a Тип selector. Insert this `<label>` block immediately before the `<label>Назва` block in the form template string:

```javascript
      <label>Тип
        <select name="kind">
          <option value="camera">Камера</option>
          <option value="scanner">Сканер (HackRF)</option>
        </select>
      </label>
```

- [ ] **Step 2: Send `kind` and branch on the scanner response**

In `submitAdd(e)`, replace the `payload` object and the success line. Change `payload` to:

```javascript
  const payload = {
    id: (fd.get('id') || '').trim(),
    name: (fd.get('name') || '').trim(),
    location: (fd.get('location') || '').trim(),
    kind: fd.get('kind') || 'camera',
  };
```

Replace the final success line `showCreds(body.device, body.push, true);` with:

```javascript
  if (body.scanner) scannerInfoModal(body.device, true);
  else showCreds(body.device, body.push, true);
```

- [ ] **Step 3: Add the scanner info modal**

Add this function in `dashboard/public/app.js` (e.g. right after `showCreds`):

```javascript
function scannerInfoModal(device, isNew) {
  showModal(`
    <h2>${isNew ? '✅ Сканер створено' : '📡 Сканер'}: ${escapeHtml(device.id)}</h2>
    <p class="muted">${escapeHtml(device.name || '')}${device.location ? ` · ${escapeHtml(device.location)}` : ''}</p>
    <p class="muted small">Вузол-сканер (HackRF) — не камера, відео не публікує.</p>
    ${credRow('SCAN_ID на Pi', device.id)}
    ${credRow('Ендпойнт телеметрії', `/api/telemetry/${device.id}`)}
    <div class="form-actions"><button type="button" data-close class="btn-primary">Готово</button></div>`);
}
```

- [ ] **Step 4: Delegate panel actions (info / edit / delete)**

Add a scanner delete helper and a delegated click listener. Add the helper near `deleteDevice`:

```javascript
async function deleteScanner(id) {
  const d = lastById.get(id) || { name: id };
  if (!confirm(`Видалити сканер «${d.name || id}»?`)) return;
  const res = await fetch(`/api/devices/${encodeURIComponent(id)}`, { method: 'DELETE' });
  if (!res.ok) { alert('Помилка видалення'); return; }
  lastById.delete(id);
  // panel re-renders without this scanner on the next SSE tick
}
```

Add this delegated listener once, right after the `const spectrumPanel = ...` line:

```javascript
spectrumPanel.addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-act]');
  if (!btn) return;
  const block = btn.closest('[data-scanner-id]');
  if (!block) return;
  const id = block.dataset.scannerId;
  const act = btn.dataset.act;
  if (act === 'edit') openEditForm(id);
  else if (act === 'del') deleteScanner(id);
  else if (act === 'info') scannerInfoModal(lastById.get(id) || { id, name: id, location: '' }, false);
});
```

(`openEditForm` already works for any id via `lastById`, so scanner name/location edits reuse it.)

- [ ] **Step 5: Verify parse + suite**

Run: `node --check dashboard/public/app.js`
Expected: no output (valid).

Run: `npm test`
Expected: PASS — full suite green.

- [ ] **Step 6: Commit**

```bash
git add dashboard/public/app.js
git commit -m "feat(dashboard): scanner create selector, info modal, panel actions" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 9: README scanner-node section + final suite

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Document the scanner node on the dashboard**

In `README.md`, find the `## Scan service (HackRF)` section (added in SP1) and append this subsection at its end:

````markdown
### Show a scanner on the dashboard

Register the scanner as a **scanner-kind** device so the dashboard renders a "Spectrum" panel
(occupancy bars, per-band spectrum charts, detection table) instead of a video tile:

- In the dashboard, **➕ Додати вузол** → set **Тип: Сканер (HackRF)** and an id (e.g. `scan-01`).
- Use that id as `SCAN_ID` for the Pi `fpv-scan` service. The scanner posts to
  `/api/telemetry/<id>`; the dashboard marks it online while telemetry stays fresh (~15 s).

Scanner devices are excluded from `mediamtx.yml` (they never publish video). To preview the panel
locally without a HackRF, run the scan service in replay mode (see above) pointing `SCAN_SERVER_URL`
at the dashboard.
````

- [ ] **Step 2: Run the full suite**

Run: `npm test`
Expected: PASS — entire suite green (registry, render-config, status, server, spectrum, plus the unchanged mtx-api/push-command tests).

- [ ] **Step 3: Verify the frontend files parse**

Run: `node --check dashboard/public/spectrum.js && node --check dashboard/public/app.js`
Expected: no output (both valid).

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs(dashboard): how to register a scanner node + spectrum panel" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage** (spec § → task):
- §2 layout/fidelity/kind/liveness/BAND_RANGES → Tasks 1–8 collectively (panel Task 7; fidelity Tasks 5–7; kind Task 1; liveness Task 4; BAND_RANGES Task 5).
- §4.1 registry kind → Task 1. §4.2 render-config excludes scanners → Task 2. §4.3 status kind → Task 3. §4.4 server scanner-online + POST kind + scanner hint → Task 4.
- §5.1 index.html panel → Task 7. §5.2 app.js split + form selector + scanner modal/actions → Tasks 7, 8. §5.3 spectrum.js helpers + renderSpectrum → Tasks 5, 6. §5.4 styles → Task 7.
- §6 detection table + chart + occupancy → Tasks 5 (helpers) + 6 (render). §7 edge cases (no scanners hide; offline "немає даних"; empty detections; missing keys guard; backward-compat camera) → Task 6 (render guards) + Task 7 (`renderSpectrumPanel` hide) + Tasks 1/3 (kind default).
- §8 tests → Tasks 1–5 add the documented cases; §8 frontend-manual note → Tasks 7/8 `node --check` + manual.
- §9 deliverables → match Tasks 1–9. §10 out-of-scope respected (no waterfall, no override, no SP1 payload change). §11 assumptions documented (BAND_RANGES constant; registration requirement in README Task 9; SCANNER_FRESH_MS in Task 4).

**Placeholder scan:** no TBD/TODO; every code step has complete code; commands have expected output. DOM-only steps are explicitly marked not-unit-tested with `node --check` guards.

**Type/name consistency:** `kind` (`camera`|`scanner`), `addDevice({id,name,location,kind})`, `SCANNER_FRESH_MS`, `splitByKind`, `renderSpectrum(container, scanners)`, `psdToPoints(psd,w,h,dbMin,dbMax)`, `detectionX(centerMhz,band,width)`, `classColor`, `fmtPct`, `fmtFreq`, `BAND_RANGES`, `scannerInfoModal(device,isNew)`, `deleteScanner(id)`, `renderSpectrumPanel(scanners)`, `#spectrum-panel`, `data-act`/`data-scanner-id` — used identically across tasks and match the spec.
