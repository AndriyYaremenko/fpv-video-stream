# Detection Journal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The dashboard server subscribes to `fpv/+/detection`, records appeared/gone events per scanner into a persisted ring, and serves them at `GET /api/detections`; the dashboard shows them in a 📜 journal modal.

**Architecture:** A pure `diffDetections` + a `DetectionJournal` ring/persistence class (`lib/detection-journal.js`), wired in `server.js start()` to a node `mqtt` client; a new authed route; a top-bar button + modal table on the dashboard.

**Tech Stack:** Node ES modules, `node --test`, `mqtt` (node). Run JS tests from repo root (`npm test`).

**Reference spec:** `docs/superpowers/specs/2026-06-19-detection-journal-design.md`

---

### Task 1: `lib/detection-journal.js` — diff + ring + persistence

**Files:**
- Create: `lib/detection-journal.js`
- Test: `test/detection-journal.test.js`

- [ ] **Step 1: Write the failing test** (`test/detection-journal.test.js`)

```javascript
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { diffDetections, DetectionJournal } from '../lib/detection-journal.js';

function payload(ts, dets) { return { scanner_id: 'hackrf', ts, detections: dets, occupancy: {} }; }

test('diffDetections: baseline yields no events', () => {
  const { events, current } = diffDetections(new Map(), 'hackrf',
    payload(1, [{ band: '5.8G', center_mhz: 5800, channel: 'F4', class: 'analog', snr_db: 28, power_dbm: -47 }]), true);
  assert.equal(events.length, 0);
  assert.ok(current.has('5.8G:F4'));
});

test('diffDetections: appeared + gone', () => {
  const prev = new Map([['5.8G:F4', { band: '5.8G', center_mhz: 5800, channel: 'F4', class: 'analog', snr_db: 28, power_dbm: -47 }]]);
  const { events } = diffDetections(prev, 'hackrf',
    payload(9, [{ band: '5.8G', center_mhz: 5769, channel: 'R4', class: 'digital', snr_db: 22, power_dbm: -50 }]), false);
  const kinds = events.map((e) => `${e.event}:${e.channel}`).sort();
  assert.deepEqual(kinds, ['appeared:R4', 'gone:F4']);
  const gone = events.find((e) => e.event === 'gone');
  assert.equal(gone.center_mhz, 5800);        // gone carries the prior detection's fields
});

test('DetectionJournal: ingest logs changes, newest-first, capped, persists', () => {
  const dir = mkdtempSync(join(tmpdir(), 'jr-'));
  const file = join(dir, 'detections.json');
  const j = new DetectionJournal({ file, max: 3 });
  j.ingest('hackrf', payload(1, [{ band: '5.8G', channel: 'F4', center_mhz: 5800, class: 'analog', snr_db: 28 }]));  // baseline, no events
  j.ingest('hackrf', payload(2, [{ band: '5.8G', channel: 'R1', center_mhz: 5658, class: 'analog', snr_db: 20 }]));  // F4 gone + R1 appeared
  const evs = j.events(10);
  assert.equal(evs.length, 2);
  assert.ok(evs[0].ts >= evs[1].ts);          // newest first
  // cap at max=3 across more churn
  j.ingest('hackrf', payload(3, [{ band: '5.8G', channel: 'R2', center_mhz: 5695, class: 'analog', snr_db: 20 }]));  // R1 gone + R2 appeared
  assert.equal(j.events(99).length, 3);
  // persisted + reloads
  const reloaded = new DetectionJournal({ file, max: 3 });
  assert.equal(reloaded.events(99).length, 3);
  assert.deepEqual(JSON.parse(readFileSync(file, 'utf8')).length, 3);
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `npm test`
Expected: FAIL (cannot find `lib/detection-journal.js`).

- [ ] **Step 3: Implement** (`lib/detection-journal.js`)

```javascript
import { readFileSync, writeFileSync } from 'node:fs';

// Stable detection key — mirror of dashboard/public/alert.js detectionKey.
function detectionKey(d) {
  const band = d.band || '?';
  if (d.channel) return `${band}:${d.channel}`;
  const mhz = Math.round(Number(d.center_mhz) / 5) * 5;
  return `${band}:${mhz}`;
}

function makeEvent(ts, scannerId, event, d) {
  return {
    ts, scanner_id: scannerId, event,
    band: d.band, center_mhz: d.center_mhz, channel: d.channel || null,
    class: d.class, snr_db: d.snr_db, power_dbm: d.power_dbm,
  };
}

// Pure: diff a detection payload against the previous key->detection map for one scanner.
// isBaseline (first message) yields no events. Returns { events, current }.
export function diffDetections(prevByKey, scannerId, payload, isBaseline) {
  const dets = (payload && payload.detections) || [];
  const ts = (payload && payload.ts) || 0;
  const current = new Map();
  for (const d of dets) current.set(detectionKey(d), d);
  const events = [];
  if (!isBaseline) {
    for (const [k, d] of current) {
      if (!prevByKey.has(k)) events.push(makeEvent(ts, scannerId, 'appeared', d));
    }
    for (const [k, d] of prevByKey) {
      if (!current.has(k)) events.push(makeEvent(ts, scannerId, 'gone', d));
    }
  }
  return { events, current };
}

export class DetectionJournal {
  constructor({ file = '', max = 2000 } = {}) {
    this._file = file;
    this._max = max;
    this._events = [];                 // oldest..newest
    this._byScanner = new Map();       // scannerId -> Map(key -> detection)
    this._seen = new Set();            // scanners that have a baseline
    this._load();
  }

  ingest(scannerId, payload) {
    const isBaseline = !this._seen.has(scannerId);
    this._seen.add(scannerId);
    const prev = this._byScanner.get(scannerId) || new Map();
    const { events, current } = diffDetections(prev, scannerId, payload, isBaseline);
    this._byScanner.set(scannerId, current);
    if (events.length) {
      this._events.push(...events);
      if (this._events.length > this._max) this._events.splice(0, this._events.length - this._max);
      this._persist();
    }
    return events;
  }

  events(limit = 200) {
    const n = this._events.length;
    return this._events.slice(Math.max(0, n - limit)).reverse();   // newest first
  }

  _load() {
    if (!this._file) return;
    try {
      const arr = JSON.parse(readFileSync(this._file, 'utf8'));
      if (Array.isArray(arr)) this._events = arr.slice(-this._max);
    } catch { /* no file yet — start empty */ }
  }

  _persist() {
    if (!this._file) return;
    try { writeFileSync(this._file, JSON.stringify(this._events), 'utf8'); } catch { /* best-effort */ }
  }
}
```

- [ ] **Step 4: Run it to confirm it passes**

Run: `npm test`
Expected: PASS (3 new tests).

- [ ] **Step 5: Commit**

```bash
git add lib/detection-journal.js test/detection-journal.test.js
git commit -m "feat(journal): DetectionJournal — diff appeared/gone + persisted ring"
```

---

### Task 2: `server.js` — MQTT subscribe + `GET /api/detections`

**Files:**
- Modify: `package.json` (+ `mqtt` dep)
- Modify: `dashboard/server.js`

- [ ] **Step 1: Add the `mqtt` dependency**

Run: `npm install mqtt` (adds it to `package.json` dependencies + `package-lock.json`; the dashboard image's `npm ci --omit=dev` then installs it).

- [ ] **Step 2: Add the route** — in `dashboard/server.js`, in `createApp`, after the `/api/mqtt`
route, add:

```javascript
  app.get('/api/detections', requireAuth, (req, res) => {
    const limit = Math.min(2000, Math.max(1, Number(req.query.limit) || 200));
    res.json(config.journal ? config.journal.events(limit) : []);
  });
```

- [ ] **Step 3: Wire the journal + MQTT in `start()`** — in `dashboard/server.js`.

Add the import at the top (with the other lib imports):

```javascript
import { DetectionJournal } from '../lib/detection-journal.js';
```

In `start()`, after `const config = { … };` (the config object literal) and before
`const app = createApp(...)`, add:

```javascript
  const journal = new DetectionJournal({
    file: env.DETECTIONS_FILE || join(dirname(mediamtxConfig), 'detections.json'),
    max: Number(env.DETECTIONS_MAX || 2000),
  });
  config.journal = journal;
  try {
    const mqtt = (await import('mqtt')).default;
    const client = mqtt.connect(env.MQTT_TCP_URL || 'mqtt://127.0.0.1:1883', {
      username: config.mqtt.user, password: config.mqtt.pass, reconnectPeriod: 5000,
    });
    client.on('connect', () => client.subscribe('fpv/+/detection'));
    client.on('message', (topic, buf) => {
      const m = /^fpv\/([^/]+)\/detection$/.exec(topic);
      if (!m) return;
      let payload;
      try { payload = JSON.parse(buf.toString()); } catch { return; }
      try { journal.ingest(m[1], payload); } catch { /* never crash the server */ }
    });
    client.on('error', (e) => console.error('journal mqtt error:', e.message));
  } catch (e) {
    console.error('journal MQTT init failed; serving without live journal:', e.message);
  }
```

- [ ] **Step 4: Validate it parses**

Run: `node --check dashboard/server.js`
Expected: no output (exit 0). (`mqtt` is dynamically imported inside `start()`, so the syntax check
doesn't require it installed.)

- [ ] **Step 5: Full JS suite (no regressions)**

Run: `npm test`
Expected: PASS (the journal unit tests + existing reducers — none import `mqtt`).

- [ ] **Step 6: Commit**

```bash
git add package.json package-lock.json dashboard/server.js
git commit -m "feat(journal): server subscribes fpv/+/detection + GET /api/detections"
```

---

### Task 3: Dashboard — 📜 journal button + modal

**Files:**
- Modify: `dashboard/public/index.html`
- Modify: `dashboard/public/app.js`
- Modify: `dashboard/public/styles.css`

- [ ] **Step 1: Add the top-bar button** — in `dashboard/public/index.html`, add the button before
`restart-all`:

```html
    <button id="journal-btn" title="Журнал детекцій">📜 Журнал</button>
    <button id="restart-all" title="Перезапустити всі перегляди">🔄 Усі</button>
```

- [ ] **Step 2: Render + wire the journal** — in `dashboard/public/app.js`.

Add `classColor` + `fmtFreq` to the existing spectrum import:

```javascript
import { splitByKind, renderSpectrum, classColor, fmtFreq } from '/spectrum.js';
```

Add the journal renderer + opener (e.g. after `scannerInfoModal`):

```javascript
function journalHtml(events) {
  const rows = (events || []).map((e) => {
    const t = new Date(Number(e.ts) * 1000);
    const p = (n) => String(n).padStart(2, '0');
    const when = `${t.getFullYear()}-${p(t.getMonth() + 1)}-${p(t.getDate())} ${p(t.getHours())}:${p(t.getMinutes())}:${p(t.getSeconds())}`;
    const freq = `${fmtFreq(e.center_mhz)}${e.channel ? ` (${escapeHtml(e.channel)})` : ''}`;
    const ev = e.event === 'gone'
      ? '<span class="jr-gone">зник</span>' : '<span class="jr-app">з\'явився</span>';
    return `<tr>
      <td>${when}</td>
      <td>${escapeHtml(e.scanner_id || '')}</td>
      <td>${escapeHtml(e.band || '')}</td>
      <td>${freq}</td>
      <td><span style="color:${classColor(e.class)}">${escapeHtml(e.class || '')}</span></td>
      <td>${e.snr_db == null ? '—' : escapeHtml(String(e.snr_db))} dB</td>
      <td>${ev}</td></tr>`;
  }).join('');
  const table = (events && events.length)
    ? `<table class="scan-table jr-table"><thead><tr><th>Час</th><th>Сканер</th><th>Бенд</th><th>Частота</th><th>Клас</th><th>SNR</th><th>Подія</th></tr></thead><tbody>${rows}</tbody></table>`
    : '<p class="muted">Журнал порожній.</p>';
  return `<h2>📜 Журнал детекцій <button type="button" id="journal-refresh" class="btn-ghost">оновити</button></h2>
    ${table}
    <div class="form-actions"><button type="button" data-close class="btn-primary">Закрити</button></div>`;
}

async function openJournal() {
  let events = [];
  try {
    const res = await fetch('/api/detections?limit=200');
    if (res.ok) events = await res.json();
  } catch { /* show empty on failure */ }
  showModal(journalHtml(events));
}
```

Wire the button (near the other top-bar wiring, e.g. after the `add-device` listener):

```javascript
document.getElementById('journal-btn').addEventListener('click', openJournal);
```

Add a refresh branch to the existing `formModal` click listener (inside its handler, after the copy
handling):

```javascript
  if (e.target.closest('#journal-refresh')) openJournal();
```

- [ ] **Step 3: Add styles** — append to `dashboard/public/styles.css`:

```css
.jr-table { max-height:60vh; display:block; overflow:auto; }
.jr-app { color:#3ddc84; font-weight:600; }
.jr-gone { color:#9aa4b2; }
```

- [ ] **Step 4: Validate it parses**

Run: `node --check dashboard/public/app.js && npm test`
Expected: no output from `node --check`, then JS suite green.

- [ ] **Step 5: Commit**

```bash
git add dashboard/public/index.html dashboard/public/app.js dashboard/public/styles.css
git commit -m "feat(dashboard): 📜 detection journal button + modal table"
```

---

## Self-Review notes (for the implementer)

- **Spec coverage:** Task 1 = journal core (§4.1); Task 2 = mqtt dep + route + start wiring (§4.2);
  Task 3 = button + modal + styles (§4.3). Resilience (§6) by the guards in Tasks 1/2.
- **Commit trailers:** append to every commit:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01Fr3LCjweDyLf1WRPz9PNUX`.
- `mqtt` is `import()`-ed dynamically inside `start()` so `node --check` and the unit tests don't need
  it installed; the Docker image installs it via `npm ci`.
- **Deploy:** dashboard-only — rebuild the container (`docker compose build dashboard && up -d --no-deps
  dashboard`). The server reaches mosquitto in the shared wg-easy netns at `mqtt://127.0.0.1:1883`; the
  journal persists at `/runtime/detections.json` (rw-mounted, survives rebuilds). No Pi change.
```
