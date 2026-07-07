# Server Frame Archive + 🖼️ Gallery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Archive every demodulated analog-video frame (MQTT `fpv/+/video`) as a PNG on the dashboard server with a JSON index, expose it via `GET /api/frames` + `GET /api/frames/:scanner/:file`, prune to 7 days, and add a "🖼️ Кадри" gallery modal to the dashboard.

**Architecture:** Mirror the detection journal (`lib/detection-journal.js` → `config.journal` → route in `createApp` → modal in `app.js`). A new `lib/frame-archive.js` class decodes base64 PNGs to `runtime/frames/<scanner>/<tsMs>_<centerMhz>.png`, keeps an in-memory index persisted to `runtime/frames-index.json`, and prunes by age on a 30-min timer. UI is a pure HTML-builder module (`dashboard/public/frames-gallery.js`) so it's testable with `node --test`, wired into `app.js`.

**Tech Stack:** Node ≥18 ESM, Express 4, `node --test` + `node:assert/strict`, mqtt (already a dep). **No new dependencies.**

**Spec:** `docs/superpowers/specs/2026-07-03-server-frame-archive-design.md`

**Branch:** `feat/server-frame-archive` off `main` (per spec). The spec file is currently *untracked* in the main checkout at `C:\Users\ZohanAdmin\Desktop\fpv-video-stream` — if you work in a fresh worktree, copy it in first (Task 1 commits it).

## Global Constraints

- Node ≥18, `"type": "module"` (ESM imports only).
- Tests: `node --test` (`npm test` runs the whole suite from the repo root; `node --test test/<file>` runs one file).
- No new npm dependencies.
- Synchronous `node:fs` I/O in the archive, exactly like `lib/detection-journal.js` (frames are infrequent — one per demod hit).
- Never crash the server on a bad MQTT message; warn at most once per 60 s (existing `lastWarn` pattern in `dashboard/server.js`).
- UI copy is Ukrainian ("🖼️ Кадри", "оновити", "Закрити", "всі сканери", "Кадрів немає.").
- Env: `FRAMES_RETENTION_DAYS` default **7**; sweep every **30 min**; also prune once at startup.
- `./runtime` is already mounted rw into the dashboard container (`docker-compose.yml`) — **no compose change needed**; frames live at `/runtime/frames`, index at `/runtime/frames-index.json` (derived from `dirname(MEDIAMTX_CONFIG)`).

### Deliberate deviations from the spec (agreed conventions)

- `dashboard/lib/frame-archive.js` in the spec → **`lib/frame-archive.js`** (repo-root `lib/` is where `detection-journal.js` lives; `dashboard/lib/` doesn't exist).
- `GET /api/frames/:id` with `id = <scanner>/<ts>_<center>` → route is **`GET /api/frames/:scanner/:file`** because an Express `:id` param can't contain `/`. The logical id stays `<scanner>/<tsMs>_<centerMhz>` and the list API returns a ready-to-use `url`.
- `<ts>` in the filename is **milliseconds** (`Math.round(ts * 1000)`) — payload `ts` is float epoch seconds; second-resolution names could collide/overwrite two frames from the same second.

### Frame payload contract (what the Pi publishes, `agent/scan/publisher.py::build_video_payload`)

```json
{ "scanner_id": "hackrf", "ts": 1751500000.25, "center_mhz": 5865.0,
  "standard": "PAL", "line_hz": 15625.0, "sync_snr_db": 12.5,
  "frame_png_b64": "<base64 PNG thumbnail, <=320px>" }
```

Topic `fpv/<id>/video` is **QoS 1, retained** → on every dashboard reconnect the broker redelivers the last frame. Ingest MUST dedupe by id (same frame → skip), or every restart appends a duplicate index entry.

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `lib/frame-archive.js` | create | `FrameArchive`: `ingest`, `list`, `filePath`, `prune`, `_load`/`_persist` |
| `test/frame-archive.test.js` | create | unit tests for the archive |
| `dashboard/server.js` | modify | `GET /api/frames`, `GET /api/frames/:scanner/:file`; `start()`: archive instance, subscribe `fpv/+/video`, retention timer |
| `test/server.test.js` | modify | HTTP-level tests for both routes |
| `dashboard/public/frames-gallery.js` | create | pure HTML builders for the gallery modal (node-testable, no DOM at module scope) |
| `test/frames-gallery.test.js` | create | unit tests for the builders |
| `dashboard/public/index.html` | modify | "🖼️ Кадри" topbar button |
| `dashboard/public/app.js` | modify | `openFrames()`, modal event delegation |
| `dashboard/public/styles.css` | modify | gallery styles; `.modal` z-index 50→70 |
| `.env.example` | modify | `FRAMES_RETENTION_DAYS=7` |

---

### Task 1: `FrameArchive.ingest` — decode, write PNG, index, dedupe, reject garbage

**Files:**
- Create: `lib/frame-archive.js`
- Create: `test/frame-archive.test.js`

**Interfaces:**
- Consumes: nothing (new module).
- Produces: `class FrameArchive({ dir, indexFile = '', max = 20000 })` with `ingest(scannerId, payload) → record | null`, and `frameId(scannerId, ts, centerMhz) → string`. Record shape: `{ id, scanner_id, ts, center_mhz, standard, line_hz, sync_snr_db }` where `id = "<scanner>/<tsMs>_<centerMhz>"`. PNG lands at `<dir>/<id>.png`. Later tasks add `list`/`filePath`/`prune` to the same class.

- [ ] **Step 0: Bring the spec into the branch and commit it**

If `docs/superpowers/specs/2026-07-03-server-frame-archive-design.md` is absent in your worktree, copy it from the main checkout, then:

```bash
git add docs/superpowers/specs/2026-07-03-server-frame-archive-design.md
git commit -m "docs: server frame archive design spec"
```

- [ ] **Step 1: Write the failing tests**

Create `test/frame-archive.test.js`:

```js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { FrameArchive } from '../lib/frame-archive.js';

// A "PNG" is anything starting with the 8-byte PNG signature — enough for the magic check.
const PNG = Buffer.concat([
  Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
  Buffer.from('frame-bytes'),
]);
const B64 = PNG.toString('base64');

function payload(ts, center, extra = {}) {
  return {
    scanner_id: 'hackrf', ts, center_mhz: center,
    standard: 'PAL', line_hz: 15625, sync_snr_db: 12.5, frame_png_b64: B64, ...extra,
  };
}
const tmp = () => mkdtempSync(join(tmpdir(), 'fr-'));

test('ingest decodes and writes the PNG, indexes it, persists the index', () => {
  const dir = tmp();
  const idx = join(dir, 'frames-index.json');
  const a = new FrameArchive({ dir, indexFile: idx });
  const rec = a.ingest('hackrf', payload(1751500000.25, 5865.0));
  assert.equal(rec.id, 'hackrf/1751500000250_5865');
  assert.equal(rec.scanner_id, 'hackrf');
  assert.equal(rec.center_mhz, 5865);
  assert.equal(rec.sync_snr_db, 12.5);
  const file = join(dir, 'hackrf', '1751500000250_5865.png');
  assert.ok(existsSync(file));
  assert.deepEqual(readFileSync(file), PNG);          // decoded bytes, not base64 text
  const persisted = JSON.parse(readFileSync(idx, 'utf8'));
  assert.equal(persisted.length, 1);
  assert.equal(persisted[0].id, 'hackrf/1751500000250_5865');
});

test('ingest skips malformed payloads and bad scanner ids', () => {
  const dir = tmp();
  const a = new FrameArchive({ dir, indexFile: join(dir, 'i.json') });
  assert.equal(a.ingest('hackrf', null), null);
  assert.equal(a.ingest('hackrf', payload(1, 5865, { frame_png_b64: '' })), null);
  assert.equal(a.ingest('hackrf', payload(1, 5865, { frame_png_b64: Buffer.from('not a png').toString('base64') })), null);
  assert.equal(a.ingest('hackrf', payload(0, 5865)), null);              // bad ts
  assert.equal(a.ingest('hackrf', payload('nope', 5865)), null);         // non-numeric ts
  assert.equal(a.ingest('hackrf', payload(1, NaN)), null);               // bad center
  assert.equal(a.ingest('../evil', payload(1, 5865)), null);             // path traversal
  assert.equal(a.ingest('', payload(1, 5865)), null);
  assert.equal(a.list().length, 0);
});

test('ingest dedupes the retained-message redelivery (same id → skip)', () => {
  const dir = tmp();
  const a = new FrameArchive({ dir, indexFile: join(dir, 'i.json') });
  assert.ok(a.ingest('hackrf', payload(100, 5865)));
  assert.equal(a.ingest('hackrf', payload(100, 5865)), null);
  assert.equal(a.list().length, 1);
});
```

Note: this file already references `a.list()` (Task 2). To keep Task 1 self-contained, include a minimal `list()` returning the raw entries array — Task 2 replaces it with the real one.

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test test/frame-archive.test.js`
Expected: FAIL — `Cannot find module '.../lib/frame-archive.js'`.

- [ ] **Step 3: Implement `lib/frame-archive.js`**

```js
import { readFileSync, writeFileSync, mkdirSync, unlinkSync, existsSync } from 'node:fs';
import { join, resolve } from 'node:path';

const PNG_MAGIC = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
const SCANNER_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]*$/;   // also blocks '.'/'..'/path tricks

// Logical frame id: "<scanner>/<tsMs>_<centerMhz>". ts is float epoch seconds on
// the wire; ms in the name avoids two same-second frames overwriting each other.
export function frameId(scannerId, ts, centerMhz) {
  return `${scannerId}/${Math.round(Number(ts) * 1000)}_${Math.round(Number(centerMhz))}`;
}

export class FrameArchive {
  constructor({ dir, indexFile = '', max = 20000 } = {}) {
    this._dir = dir;
    this._indexFile = indexFile;
    this._max = max;
    this._entries = [];        // oldest..newest {id, scanner_id, ts, center_mhz, standard, line_hz, sync_snr_db}
    this._ids = new Set();
    this._load();
  }

  // Archive one fpv/<id>/video payload: validate, decode, write PNG, index.
  // Returns the record, or null when malformed or already archived (the topic is
  // retained → the broker redelivers the last frame on every reconnect).
  ingest(scannerId, payload) {
    if (!SCANNER_RE.test(String(scannerId || ''))) return null;
    const p = payload || {};
    const ts = Number(p.ts);
    const center = Number(p.center_mhz);
    if (!Number.isFinite(ts) || ts <= 0 || !Number.isFinite(center) || center <= 0) return null;
    if (typeof p.frame_png_b64 !== 'string' || !p.frame_png_b64) return null;
    const png = Buffer.from(p.frame_png_b64, 'base64');
    if (png.length <= PNG_MAGIC.length || !png.subarray(0, PNG_MAGIC.length).equals(PNG_MAGIC)) return null;
    const id = frameId(scannerId, ts, center);
    if (this._ids.has(id)) return null;
    mkdirSync(join(this._dir, scannerId), { recursive: true });
    writeFileSync(join(this._dir, `${id}.png`), png);
    const rec = {
      id, scanner_id: scannerId, ts, center_mhz: center,
      standard: p.standard ?? null, line_hz: p.line_hz ?? null,
      sync_snr_db: p.sync_snr_db ?? null,
    };
    this._entries.push(rec);
    this._ids.add(id);
    while (this._entries.length > this._max) this._remove(0);  // count backstop; age retention is the real bound
    this._persist();
    return rec;
  }

  list() { return this._entries; }   // placeholder — real filtering lands in Task 2

  // Drop the entry at position i AND its PNG.
  _remove(i) {
    const [e] = this._entries.splice(i, 1);
    this._ids.delete(e.id);
    try { unlinkSync(join(this._dir, `${e.id}.png`)); } catch { /* already gone */ }
  }

  _load() {
    if (!this._indexFile) return;
    try {
      const arr = JSON.parse(readFileSync(this._indexFile, 'utf8'));
      if (Array.isArray(arr)) {
        this._entries = arr.filter((e) => e && typeof e.id === 'string').slice(-this._max);
        this._ids = new Set(this._entries.map((e) => e.id));
      }
    } catch { /* no index yet — start empty */ }
  }

  // Sync full rewrite per ingest/prune, like the detection journal: frames are
  // infrequent (one per demod hit) and the index is capped, so O(n) is fine.
  _persist() {
    if (!this._indexFile) return;
    try { writeFileSync(this._indexFile, JSON.stringify(this._entries), 'utf8'); } catch { /* best-effort */ }
  }
}
```

(`resolve` is imported now, used by Task 2's `filePath`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test test/frame-archive.test.js`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the whole suite, then commit**

Run: `npm test` — everything green.

```bash
git add lib/frame-archive.js test/frame-archive.test.js
git commit -m "feat(frames): FrameArchive.ingest — decode, store, index, dedupe"
```

---

### Task 2: `FrameArchive.list` + `filePath`

**Files:**
- Modify: `lib/frame-archive.js`
- Modify: `test/frame-archive.test.js`

**Interfaces:**
- Consumes: Task 1's class internals (`_entries`, `_ids`, `_dir`).
- Produces:
  - `list({ scanner = '', since = 0, limit = 200 } = {})` → newest-first array of `{ id, scanner_id, ts, center_mhz, standard, line_hz, sync_snr_db, url }` where `url = "/api/frames/<id>.png"`; `scanner` = exact id filter, `since` = epoch seconds (strictly newer).
  - `filePath(scanner, file)` → absolute PNG path, or `null` when either segment is malformed, the id isn't indexed, or the file is gone (pruned). `file` looks like `"1751500000250_5865.png"`.

- [ ] **Step 1: Write the failing tests** (append to `test/frame-archive.test.js`)

```js
test('list: newest-first with scanner/since/limit filters and url', () => {
  const dir = tmp();
  const a = new FrameArchive({ dir, indexFile: join(dir, 'i.json') });
  a.ingest('hackrf', payload(100, 5865));
  a.ingest('bladerf', payload(200, 5800));
  a.ingest('hackrf', payload(300, 5745));
  const all = a.list();
  assert.deepEqual(all.map((f) => f.ts), [300, 200, 100]);            // newest first
  assert.equal(all[0].url, '/api/frames/hackrf/300000_5745.png');
  assert.deepEqual(a.list({ scanner: 'hackrf' }).map((f) => f.ts), [300, 100]);
  assert.deepEqual(a.list({ since: 100 }).map((f) => f.ts), [300, 200]);   // strictly newer
  assert.equal(a.list({ limit: 1 }).length, 1);
  assert.equal(a.list({ limit: 1 })[0].ts, 300);
});

test('filePath: serves only well-formed, indexed, on-disk ids', () => {
  const dir = tmp();
  const a = new FrameArchive({ dir, indexFile: join(dir, 'i.json') });
  a.ingest('hackrf', payload(100, 5865));
  const p = a.filePath('hackrf', '100000_5865.png');
  assert.ok(p && existsSync(p));
  assert.deepEqual(readFileSync(p), PNG);
  assert.equal(a.filePath('hackrf', '999999_5865.png'), null);        // not indexed
  assert.equal(a.filePath('..', '100000_5865.png'), null);            // traversal
  assert.equal(a.filePath('hackrf', '../../etc/passwd'), null);       // traversal
  assert.equal(a.filePath('hackrf', '100000_5865.PNG'), null);        // strict name
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test test/frame-archive.test.js`
Expected: FAIL — `list` ignores opts / `filePath` is not a function.

- [ ] **Step 3: Implement — replace the placeholder `list()` and add `filePath` + `FILE_RE`**

Add next to `SCANNER_RE`:

```js
const FILE_RE = /^\d+_\d+\.png$/;
```

Replace `list() { return this._entries; }` with:

```js
  // Newest-first metadata for GET /api/frames. since = epoch seconds, strictly newer.
  list({ scanner = '', since = 0, limit = 200 } = {}) {
    const out = [];
    for (let i = this._entries.length - 1; i >= 0 && out.length < limit; i--) {
      const e = this._entries[i];
      if (scanner && e.scanner_id !== scanner) continue;
      if (since && e.ts <= since) continue;
      out.push({ ...e, url: `/api/frames/${e.id}.png` });
    }
    return out;
  }

  // Absolute PNG path for GET /api/frames/:scanner/:file — null unless both
  // segments are strictly well-formed AND the id is indexed AND the file exists
  // (pruned → 404). res.sendFile requires an absolute path.
  filePath(scanner, file) {
    if (!SCANNER_RE.test(String(scanner || '')) || !FILE_RE.test(String(file || ''))) return null;
    const id = `${scanner}/${file.slice(0, -'.png'.length)}`;
    if (!this._ids.has(id)) return null;
    const p = resolve(join(this._dir, `${id}.png`));
    return existsSync(p) ? p : null;
  }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test test/frame-archive.test.js`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add lib/frame-archive.js test/frame-archive.test.js
git commit -m "feat(frames): list (newest-first, filters) + validated filePath"
```

---

### Task 3: `FrameArchive.prune` + reload-from-disk + count cap

**Files:**
- Modify: `lib/frame-archive.js`
- Modify: `test/frame-archive.test.js`

**Interfaces:**
- Consumes: Task 1/2 internals.
- Produces: `prune(nowMs, maxAgeMs) → number` (removed count) — deletes **both** index entries and PNG files older than `maxAgeMs`; persists the shrunk index. (Spec risk note: "verify the prune actually runs and deletes files, not just index entries" — the test asserts the file is gone.)

- [ ] **Step 1: Write the failing tests** (append)

```js
test('prune drops old entries AND deletes their files, persists, returns count', () => {
  const dir = tmp();
  const idx = join(dir, 'i.json');
  const a = new FrameArchive({ dir, indexFile: idx });
  a.ingest('hackrf', payload(1000, 5865));                  // old
  a.ingest('hackrf', payload(2000, 5800));                  // fresh
  const oldFile = join(dir, 'hackrf', '1000000_5865.png');
  assert.ok(existsSync(oldFile));
  // now = 2000s in ms; maxAge = 500s → cutoff 1500s: drops ts=1000, keeps ts=2000
  const removed = a.prune(2000 * 1000, 500 * 1000);
  assert.equal(removed, 1);
  assert.ok(!existsSync(oldFile));                          // file deleted, not just the entry
  assert.deepEqual(a.list().map((f) => f.ts), [2000]);
  assert.equal(JSON.parse(readFileSync(idx, 'utf8')).length, 1);
  assert.equal(a.prune(2000 * 1000, 500 * 1000), 0);        // idempotent
});

test('reloads the persisted index; filePath works after reload', () => {
  const dir = tmp();
  const idx = join(dir, 'i.json');
  new FrameArchive({ dir, indexFile: idx }).ingest('hackrf', payload(100, 5865));
  const b = new FrameArchive({ dir, indexFile: idx });
  assert.equal(b.list().length, 1);
  assert.ok(b.filePath('hackrf', '100000_5865.png'));
  assert.equal(b.ingest('hackrf', payload(100, 5865)), null);   // dedupe survives restart
});

test('count cap evicts the oldest entry and its file', () => {
  const dir = tmp();
  const a = new FrameArchive({ dir, indexFile: join(dir, 'i.json'), max: 2 });
  a.ingest('hackrf', payload(100, 5865));
  a.ingest('hackrf', payload(200, 5800));
  a.ingest('hackrf', payload(300, 5745));
  assert.deepEqual(a.list().map((f) => f.ts), [300, 200]);
  assert.ok(!existsSync(join(dir, 'hackrf', '100000_5865.png')));
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test test/frame-archive.test.js`
Expected: FAIL — `a.prune is not a function` (reload + cap tests may already pass; that's fine).

- [ ] **Step 3: Implement `prune`** (add after `filePath`)

```js
  // Retention sweep: drop entries (and files) older than maxAgeMs. Entries carry
  // epoch seconds; nowMs/maxAgeMs are milliseconds. Returns the removed count.
  prune(nowMs, maxAgeMs) {
    const cutoff = (nowMs - maxAgeMs) / 1000;
    let removed = 0;
    for (let i = this._entries.length - 1; i >= 0; i--) {
      if (this._entries[i].ts < cutoff) { this._remove(i); removed++; }
    }
    if (removed) this._persist();
    return removed;
  }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test test/frame-archive.test.js`
Expected: PASS (8 tests).

- [ ] **Step 5: Run the whole suite, then commit**

Run: `npm test` — everything green.

```bash
git add lib/frame-archive.js test/frame-archive.test.js
git commit -m "feat(frames): age-based prune deletes files + index entries"
```

---

### Task 4: API — `GET /api/frames` + `GET /api/frames/:scanner/:file`

**Files:**
- Modify: `dashboard/server.js` (the `createApp` part only — `start()` wiring is Task 5)
- Modify: `test/server.test.js`

**Interfaces:**
- Consumes: `config.frames` — a `FrameArchive` (optional: absent → empty list / 404, mirroring `config.journal`); `FrameArchive.list({scanner, since, limit})` and `filePath(scanner, file)` from Tasks 2.
- Produces: `GET /api/frames?scanner=&since=&limit=` → JSON array (auth required); `GET /api/frames/:scanner/:file` → PNG bytes via `res.sendFile` (auth required), 404 JSON `{error:'frame not found'}` when missing/pruned/malformed.

- [ ] **Step 1: Write the failing tests** (append to `test/server.test.js`)

Add imports at the top of the file:

```js
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { FrameArchive } from '../lib/frame-archive.js';
```

Append tests:

```js
const FR_PNG = Buffer.concat([
  Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
  Buffer.from('frame-bytes'),
]);

function makeArchive() {
  const dir = mkdtempSync(join(tmpdir(), 'frapi-'));
  const frames = new FrameArchive({ dir, indexFile: join(dir, 'i.json') });
  frames.ingest('hackrf', {
    ts: 1751500000, center_mhz: 5865, standard: 'PAL', line_hz: 15625,
    sync_snr_db: 14.2, frame_png_b64: FR_PNG.toString('base64'),
  });
  return frames;
}

test('GET /api/frames requires auth', async () => {
  const { server, base } = await startServer();
  const res = await fetch(`${base}/api/frames`, { redirect: 'manual' });
  assert.equal(res.status, 401);
  server.close();
});

test('frames API: list + PNG bytes + 404 for pruned/unknown', async () => {
  const frames = makeArchive();
  const reg = { devices: [] };
  const { server, base } = await startWith(reg, { ...config, frames });
  const cookie = await login(base);
  const res = await fetch(`${base}/api/frames?scanner=hackrf&limit=10`, { headers: { cookie } });
  assert.equal(res.status, 200);
  const list = await res.json();
  assert.equal(list.length, 1);
  assert.equal(list[0].center_mhz, 5865);
  assert.equal(list[0].sync_snr_db, 14.2);
  assert.equal(list[0].url, '/api/frames/hackrf/1751500000000_5865.png');
  const img = await fetch(`${base}${list[0].url}`, { headers: { cookie } });
  assert.equal(img.status, 200);
  assert.match(img.headers.get('content-type'), /image\/png/);
  assert.deepEqual(Buffer.from(await img.arrayBuffer()), FR_PNG);
  const missing = await fetch(`${base}/api/frames/hackrf/1_1.png`, { headers: { cookie } });
  assert.equal(missing.status, 404);
  server.close();
});

test('frames API without an archive: empty list, 404 file', async () => {
  const { server, base } = await startServer();          // base config has no .frames
  const cookie = await login(base);
  const list = await fetch(`${base}/api/frames`, { headers: { cookie } });
  assert.deepEqual(await list.json(), []);
  const img = await fetch(`${base}/api/frames/x/1_1.png`, { headers: { cookie } });
  assert.equal(img.status, 404);
  server.close();
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test test/server.test.js`
Expected: FAIL — `/api/frames` returns 404 (route doesn't exist; note 404 ≠ expected 401/200).

- [ ] **Step 3: Implement the routes** — in `dashboard/server.js`, right after the `app.get('/api/detections', ...)` block (line ~71), add:

```js
  app.get('/api/frames', requireAuth, (req, res) => {
    if (!config.frames) return res.json([]);
    const limit = Math.min(2000, Math.max(1, Number(req.query.limit) || 200));
    res.json(config.frames.list({
      scanner: String(req.query.scanner || ''),
      since: Number(req.query.since) || 0,
      limit,
    }));
  });

  // Frame id is "<scanner>/<tsMs>_<centerMhz>"; the archive validates both
  // segments (no path traversal) and 404s ids that were pruned.
  app.get('/api/frames/:scanner/:file', requireAuth, (req, res) => {
    const path = config.frames ? config.frames.filePath(req.params.scanner, req.params.file) : null;
    if (!path) return res.status(404).json({ error: 'frame not found' });
    res.sendFile(path);
  });
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test test/server.test.js`
Expected: PASS (all, including the 3 new ones).

- [ ] **Step 5: Commit**

```bash
git add dashboard/server.js test/server.test.js
git commit -m "feat(frames): GET /api/frames list + per-frame PNG endpoint"
```

---

### Task 5: `start()` wiring — subscribe `fpv/+/video`, retention timer, env

**Files:**
- Modify: `dashboard/server.js` (`start()` only)
- Modify: `.env.example`

**Interfaces:**
- Consumes: `FrameArchive` (Tasks 1–3); existing MQTT client + `lastWarn` throttle; env `FRAMES_RETENTION_DAYS` (default 7), optional `FRAMES_DIR`, `FRAMES_INDEX_FILE`, `FRAMES_MAX` (default 20000).
- Produces: a live archive at `/runtime/frames` fed by MQTT, pruned every 30 min and once at startup. No public code interface for later tasks.

Note: `start()` has no unit tests in this codebase (same as the journal wiring) — the deliverable is verified by the full suite staying green plus the manual integration check at the end of this plan.

- [ ] **Step 1: Import and instantiate the archive** — in `dashboard/server.js`:

Add to the imports:

```js
import { FrameArchive } from '../lib/frame-archive.js';
```

In `start()`, right after the `config.journal = journal;` line, add:

```js
  const frames = new FrameArchive({
    dir: env.FRAMES_DIR || join(dirname(mediamtxConfig), 'frames'),
    indexFile: env.FRAMES_INDEX_FILE || join(dirname(mediamtxConfig), 'frames-index.json'),
    max: Number(env.FRAMES_MAX || 20000),
  });
  config.frames = frames;
  const framesMaxAgeMs = Number(env.FRAMES_RETENTION_DAYS || 7) * 24 * 60 * 60 * 1000;
  frames.prune(Date.now(), framesMaxAgeMs);               // catch up after downtime
  setInterval(() => frames.prune(Date.now(), framesMaxAgeMs), 30 * 60 * 1000).unref();
```

- [ ] **Step 2: Route `fpv/+/video` into the archive** — in the same `try` block, replace the `client.on('connect', ...)` and `client.on('message', ...)` handlers with:

```js
    client.on('connect', () => client.subscribe(['fpv/+/detection', 'fpv/+/video']));
    client.on('message', (topic, buf) => {
      const m = /^fpv\/([^/]+)\/(detection|video)$/.exec(topic);
      if (!m) return;
      let payload;
      try { payload = JSON.parse(buf.toString()); } catch { return; }
      try {
        // never crash the server on a bad message
        if (m[2] === 'detection') journal.ingest(m[1], payload);
        else frames.ingest(m[1], payload);
      } catch (e) {
        const now = Date.now();
        if (now - lastWarn > 60000) { lastWarn = now; console.warn(`${m[2]} ingest error:`, e.message); }
      }
    });
```

Also update the two catch/error messages in that block to mention frames:
- `client.on('error', (e) => console.error('scan mqtt error:', e.message));`
- `console.error('scan MQTT init failed; serving without live journal/frames:', e.message);`

- [ ] **Step 3: Document the env knob** — append to `.env.example`:

```
# ---- Server frame archive (demodulated video frames from fpv/<id>/video) ----
# PNGs land in ./runtime/frames (already a rw mount in docker-compose); index in
# ./runtime/frames-index.json. Frames older than this are pruned every 30 min.
FRAMES_RETENTION_DAYS=7
```

- [ ] **Step 4: Run the whole suite**

Run: `npm test`
Expected: PASS — no regressions (this task adds no new tests).

- [ ] **Step 5: Commit**

```bash
git add dashboard/server.js .env.example
git commit -m "feat(frames): archive fpv/+/video via MQTT + 7-day retention sweep"
```

---

### Task 6: `frames-gallery.js` — pure HTML builders for the gallery modal

**Files:**
- Create: `dashboard/public/frames-gallery.js`
- Create: `test/frames-gallery.test.js`

**Interfaces:**
- Consumes: the `/api/frames` list item shape `{ id, scanner_id, ts, center_mhz, standard, sync_snr_db, url }` (Task 2).
- Produces (for Task 7):
  - `galleryHtml(frames, scanner = '') → string` — full modal body: `<h2>` with `#frames-refresh` button, toolbar with `<select id="frames-scanner">`, tile grid (`button.fr-tile` with `data-src`/`data-cap` + `img` + `span.fr-cap`), close button. `scanner` filters the tiles but the select always lists all scanners.
  - `scannerIds(frames) → string[]` (unique, sorted).
  - `frameCaption(f) → string` — `"YYYY-MM-DD HH:MM:SS · 5865 МГц · hackrf · sync 12.5 dB"`.
- No DOM access at module scope, no imports — importable by `node --test`.

- [ ] **Step 1: Write the failing tests**

Create `test/frames-gallery.test.js`:

```js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { galleryHtml, scannerIds, frameCaption } from '../dashboard/public/frames-gallery.js';

const fr = (over = {}) => ({
  id: 'hackrf/1751500000000_5865', scanner_id: 'hackrf', ts: 1751500000,
  center_mhz: 5865, standard: 'PAL', sync_snr_db: 12.5,
  url: '/api/frames/hackrf/1751500000000_5865.png', ...over,
});

test('scannerIds: unique + sorted', () => {
  assert.deepEqual(scannerIds([fr(), fr({ scanner_id: 'bladerf' }), fr()]), ['bladerf', 'hackrf']);
  assert.deepEqual(scannerIds([]), []);
});

test('frameCaption: time, MHz, scanner, SNR; SNR omitted when null', () => {
  const cap = frameCaption(fr());
  assert.match(cap, /\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}/);
  assert.match(cap, /5865 МГц · hackrf · sync 12\.5 dB/);
  assert.ok(!frameCaption(fr({ sync_snr_db: null })).includes('sync'));
});

test('galleryHtml: tiles with data-src/data-cap, lazy img, refresh + select', () => {
  const html = galleryHtml([fr()]);
  assert.match(html, /id="frames-refresh"/);
  assert.match(html, /<select id="frames-scanner">/);
  assert.match(html, /class="fr-tile" data-src="\/api\/frames\/hackrf\/1751500000000_5865\.png"/);
  assert.match(html, /<img loading="lazy" src="\/api\/frames\/hackrf\/1751500000000_5865\.png"/);
  assert.match(html, /5865 МГц/);
});

test('galleryHtml: scanner filter narrows tiles, select keeps all + selected', () => {
  const frames = [fr(), fr({ scanner_id: 'bladerf', url: '/api/frames/bladerf/1_1.png' })];
  const html = galleryHtml(frames, 'bladerf');
  assert.ok(!html.includes('/api/frames/hackrf/'));           // hackrf tile filtered out
  assert.match(html, /<option value="hackrf">/);              // ...but still selectable
  assert.match(html, /<option value="bladerf" selected>/);
});

test('galleryHtml: empty state + html escaping', () => {
  assert.match(galleryHtml([]), /Кадрів немає\./);
  const html = galleryHtml([fr({ scanner_id: '<x&>' })]);
  assert.ok(!html.includes('<x&>'));
  assert.ok(html.includes('&lt;x&amp;&gt;'));
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test test/frames-gallery.test.js`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `dashboard/public/frames-gallery.js`**

```js
// dashboard/public/frames-gallery.js — pure HTML builders for the 🖼️ frames
// gallery modal. No DOM access at module scope so node --test can import it.

function escapeHtml(s) { return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

function fmtWhen(tsSec) {
  const t = new Date(Number(tsSec) * 1000);
  const p = (n) => String(n).padStart(2, '0');
  return `${t.getFullYear()}-${p(t.getMonth() + 1)}-${p(t.getDate())} ${p(t.getHours())}:${p(t.getMinutes())}:${p(t.getSeconds())}`;
}

export function scannerIds(frames) {
  return [...new Set((frames || []).map((f) => f && f.scanner_id).filter(Boolean))].sort();
}

export function frameCaption(f) {
  const parts = [fmtWhen(f.ts), `${Math.round(Number(f.center_mhz))} МГц`, f.scanner_id];
  if (f.sync_snr_db != null) parts.push(`sync ${f.sync_snr_db} dB`);
  return parts.join(' · ');
}

// Full modal body. `scanner` filters the tiles; the select always lists every
// scanner present in `frames` so the operator can switch back to "всі".
export function galleryHtml(frames, scanner = '') {
  const all = frames || [];
  const shown = scanner ? all.filter((f) => f.scanner_id === scanner) : all;
  const opts = ['<option value="">всі сканери</option>']
    .concat(scannerIds(all).map((id) =>
      `<option value="${escapeHtml(id)}"${id === scanner ? ' selected' : ''}>${escapeHtml(id)}</option>`))
    .join('');
  const tiles = shown.map((f) => {
    const cap = frameCaption(f);
    return `<button type="button" class="fr-tile" data-src="${escapeHtml(f.url)}" data-cap="${escapeHtml(cap)}">
      <img loading="lazy" src="${escapeHtml(f.url)}" alt="кадр" />
      <span class="fr-cap">${escapeHtml(cap)}</span>
    </button>`;
  }).join('');
  const grid = shown.length ? `<div class="fr-grid">${tiles}</div>` : '<p class="muted">Кадрів немає.</p>';
  return `<h2>🖼️ Кадри <button type="button" id="frames-refresh" class="btn-ghost">оновити</button></h2>
    <div class="fr-toolbar"><label>Сканер <select id="frames-scanner">${opts}</select></label></div>
    ${grid}
    <div class="form-actions"><button type="button" data-close class="btn-primary">Закрити</button></div>`;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test test/frames-gallery.test.js`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add dashboard/public/frames-gallery.js test/frames-gallery.test.js
git commit -m "feat(frames): pure gallery HTML builders (testable, uk copy)"
```

---

### Task 7: Wire the UI — 🖼️ button, modal behavior, styles

**Files:**
- Modify: `dashboard/public/index.html`
- Modify: `dashboard/public/app.js`
- Modify: `dashboard/public/styles.css`

**Interfaces:**
- Consumes: `galleryHtml` (Task 6); existing `showModal`/`hideModal`, `openImageModal(src, caption)`, `formModal`/`formBody` in `app.js`; `GET /api/frames` (Task 4).
- Produces: end-user feature; nothing downstream.

Browser-only glue — no unit tests (same as the 📜 journal wiring); functional check is the manual verification below.

- [ ] **Step 1: Add the topbar button** — in `dashboard/public/index.html`, right after the `journal-btn` line, add:

```html
    <button id="frames-btn" title="Архів демодульованих кадрів">🖼️ Кадри</button>
```

- [ ] **Step 2: Wire `app.js`**

Add the import next to the other imports at the top:

```js
import { galleryHtml } from '/frames-gallery.js';
```

Next to the `journal-btn` listener (line ~105), add:

```js
document.getElementById('frames-btn').addEventListener('click', openFrames);
```

Inside the existing `formModal.addEventListener('click', ...)` handler, after the `#journal-refresh` line, add:

```js
  if (e.target.closest('#frames-refresh')) openFrames();
  const frTile = e.target.closest('.fr-tile');
  if (frTile) openImageModal(frTile.dataset.src, frTile.dataset.cap);
```

After that click handler, add a change delegate (the select re-renders the gallery from the cached list):

```js
formModal.addEventListener('change', (e) => {
  const sel = e.target.closest('#frames-scanner');
  if (sel) showModal(galleryHtml(framesCache, sel.value));
});
```

Add the gallery opener next to `openJournal` (after line ~486):

```js
// ---- frames gallery (server archive of demodulated frames) ----
let framesCache = [];
async function openFrames() {
  framesCache = [];
  try {
    const res = await fetch('/api/frames?limit=200');
    if (res.ok) framesCache = await res.json();
  } catch { /* show empty on failure */ }
  showModal(galleryHtml(framesCache));
}
```

- [ ] **Step 3: Styles** — in `dashboard/public/styles.css`:

Change the `.modal` rule (line 32) from `z-index:50` to `z-index:70` and extend the comment — the fullscreen viewer must sit **above** `.modal2` (z-index 60) because gallery tiles open it while the gallery card is still up:

```css
/* fullscreen viewer — above .modal2(60): gallery tiles open it over the card */
.modal { position:fixed; inset:0; background:rgba(0,0,0,.9); display:flex; align-items:center; justify-content:center; flex-direction:column; z-index:70; }
```

Append at the end of the file:

```css
/* frames gallery modal */
.fr-toolbar { display:flex; gap:.6rem; align-items:center; margin:.2rem 0 .8rem; color:#cbd5e1; font-size:.85rem; }
.fr-toolbar select { background:#0c1118; color:#cfd6e0; border:1px solid var(--line); border-radius:4px; margin-left:.4rem; }
.fr-grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(150px, 1fr)); gap:.6rem; max-height:60vh; overflow:auto; }
.fr-tile { background:#0c1118; border:1px solid var(--line); border-radius:6px; padding:.35rem; cursor:pointer; display:flex; flex-direction:column; gap:.3rem; text-align:left; }
.fr-tile img { width:100%; border-radius:4px; background:#000; image-rendering:pixelated; }
.fr-cap { color:#9aa4b2; font-size:.72rem; line-height:1.25; }
```

- [ ] **Step 4: Run the whole suite**

Run: `npm test`
Expected: PASS — no regressions.

- [ ] **Step 5: Commit**

```bash
git add dashboard/public/index.html dashboard/public/app.js dashboard/public/styles.css
git commit -m "feat(frames): 🖼️ Кадри gallery modal with scanner filter + fullscreen view"
```

---

## Manual integration check (after deploy — spec's integration test)

On the dev server (193.242.163.139, over WG), after the surgical dashboard update (`docker compose build dashboard && docker compose up -d dashboard` — do NOT touch wg-easy):

```bash
cd /opt/fpv-video-stream && set -a && . ./.env && set +a
# 1x1 PNG, publish a synthetic retained frame as the Pi would
B64='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII='
docker run --rm --network container:wg-easy eclipse-mosquitto:2 mosquitto_pub \
  -h 127.0.0.1 -p 1883 -u pub -P "$MQTT_PUB_PASS" -t fpv/testfr/video -r \
  -m "{\"scanner_id\":\"testfr\",\"ts\":$(date +%s),\"center_mhz\":5865,\"standard\":\"PAL\",\"line_hz\":15625,\"sync_snr_db\":9.9,\"frame_png_b64\":\"$B64\"}"

# login + list + fetch the PNG
curl -s -c /tmp/fpvc -d "user=$DASH_USER&pass=$DASH_PASS" http://10.8.0.1:8080/login >/dev/null
curl -s -b /tmp/fpvc 'http://10.8.0.1:8080/api/frames?limit=5'          # → JSON with the testfr frame
curl -s -b /tmp/fpvc -o /tmp/fr.png "http://10.8.0.1:8080$(curl -s -b /tmp/fpvc 'http://10.8.0.1:8080/api/frames?limit=1' | sed -E 's/.*"url":"([^"]+)".*/\1/')"
file /tmp/fr.png                                                        # → PNG image data
ls /opt/fpv-video-stream/runtime/frames/testfr/                        # → <tsMs>_5865.png
```

Then in the browser (over WG): 🖼️ Кадри → the synthetic tile shows; click → fullscreen; scanner filter works. Clean up the retained test topic:

```bash
docker run --rm --network container:wg-easy eclipse-mosquitto:2 mosquitto_pub \
  -h 127.0.0.1 -p 1883 -u pub -P "$MQTT_PUB_PASS" -t fpv/testfr/video -r -n
```

## Spec-coverage self-check (done while writing this plan)

- Subscribe `fpv/+/video`, decode, store PNG + index — Tasks 1, 5 ✓
- Malformed-payload guard + throttled warnings — Task 1 (guard), Task 5 (`lastWarn`) ✓
- Retention: `FRAMES_RETENTION_DAYS` (7), 30-min sweep, files AND entries deleted (tested) — Tasks 3, 5 ✓
- `GET /api/frames?scanner=&since=&limit=` newest-first with `url` — Tasks 2, 4 ✓
- `GET /api/frames/<scanner>/<file>` PNG, 404 if pruned — Tasks 2, 4 ✓
- 🖼️ Кадри modal: thumbnail grid (time, MHz, scanner, SNR), click → fullscreen, scanner filter, newest-first — Tasks 6, 7 ✓
- compose/.env: `./runtime` already rw-mounted; `FRAMES_RETENTION_DAYS` documented — Task 5 ✓
- Unit tests mirror the journal's; integration = HTTP-level server tests (Task 4) + manual broker check above ✓
