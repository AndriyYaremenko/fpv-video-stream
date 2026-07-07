# Frames Gallery Filters + «Показати ще» Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Server-side filters (time range, band, min sync SNR, standard) + cursor pagination for the 🖼️ Кадри gallery, with a two-row filter toolbar and a «Показати ще» button.

**Architecture:** Extend `FrameArchive.list()` with cheap per-entry comparisons (`until`, `before` cursor, `fmin`/`fmax`, `snrMin`, `standard`); `/api/frames` passes them through as query params. The gallery module (`dashboard/public/frames-gallery.js`) stays a pure HTML/query builder (node-testable); `app.js` holds the filter state, re-fetches on every change, and appends on «Ще».

**Tech Stack:** Node ≥18 ESM, Express 4, `node --test`, browser-vanilla JS. **No new dependencies.**

**Spec:** `docs/superpowers/specs/2026-07-05-frames-gallery-filters-design.md`

**Branch:** `feat/frames-gallery-filters` off `main` (main = `8970396`, PR #16 merged). The spec and this plan currently exist only in the *old merged* worktree `.claude/worktrees/feat+server-frame-archive/docs/superpowers/...` — copy both into the new worktree and commit as Task 1 Step 0.

## Global Constraints

- Node ≥18, `"type": "module"` (ESM only); tests via `node --test` (`npm test` = whole suite).
- No new npm dependencies.
- UI copy Ukrainian: «всі сканери», «всі бенди», «всі стандарти», «Показати ще», «Кадрів немає.», presets «1 год / 24 год / 7 д / все».
- `since` keeps its exclusive (`ts > since`) semantics — do NOT change it.
- Numeric query params: `Number(...) || 0` → 0/NaN = unset (so `snr_min=0` is "no filter" — acceptable, real SNRs are positive).
- `standard` matches case-insensitively; entries with `null` `sync_snr_db` are EXCLUDED when `snrMin` is set.
- Band presets (exact MHz): 0.9G → 800–1000, 1.2G → 1000–1500, 2.4G → 2200–2700, 5.8G → 5000–6100.
- «Показати ще» is visible only when the last fetch returned exactly `limit` (200) frames; it re-sends the same filters plus `before=<ts of oldest shown>`.
- `frames-gallery.js` must stay importable by `node --test`: no DOM access at module scope, no imports.

### Current shapes (from PR #16 — what this plan modifies)

`lib/frame-archive.js` `list()` today:

```js
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
```

Entry fields: `{ id, scanner_id, ts (float epoch s), center_mhz, standard ('PAL'|'NTSC'|null), line_hz, sync_snr_db (number|null) }`.

`dashboard/public/frames-gallery.js` today exports `scannerIds(frames)`, `frameCaption(f)`, `galleryHtml(frames, scanner = '')` (the old second arg did *client-side* tile filtering — this plan removes that; the server filters now). `app.js` today: `openFrames()` fetches `/api/frames?limit=200` into `framesCache`, `formModal` change-handler re-renders on `#frames-scanner`.

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `lib/frame-archive.js` | modify `list()` only | new filter comparisons |
| `test/frame-archive.test.js` | append | filter unit tests |
| `dashboard/server.js` | modify `/api/frames` route | param passthrough |
| `test/server.test.js` | append | HTTP filter tests |
| `dashboard/public/frames-gallery.js` | extend + rework `galleryHtml` | `BAND_PRESETS`, `buildFramesQuery`, `toLocalDatetime`, `scannerOptions`, toolbar HTML, «Ще» |
| `test/frames-gallery.test.js` | append + replace galleryHtml tests | pure-logic tests |
| `dashboard/public/app.js` | modify frames section | filter state, fetch/append, event delegation |
| `dashboard/public/styles.css` | append + 1 edit | two-row toolbar, inputs, «Ще» |

---

### Task 1: `FrameArchive.list()` — new filter options

**Files:**
- Modify: `lib/frame-archive.js` (only the `list()` method)
- Test: `test/frame-archive.test.js` (append)

**Interfaces:**
- Consumes: existing entry shape (see Global Constraints).
- Produces: `list({ scanner='', since=0, until=0, before=0, fmin=0, fmax=0, snrMin=0, standard='', limit=200 })` — all numerics use truthiness (0 = unset); `until` inclusive (`ts <= until`), `before` strict (`ts < before`), `fmin`/`fmax` inclusive on `center_mhz`, `snrMin` → `sync_snr_db >= snrMin` (null SNR excluded), `standard` case-insensitive equality. Return shape unchanged (`{...entry, url}` newest-first).

- [ ] **Step 0: Bring the spec + plan into the branch and commit**

If absent in your worktree, copy from the old worktree
`.claude/worktrees/feat+server-frame-archive/docs/superpowers/specs/2026-07-05-frames-gallery-filters-design.md`
and `.../plans/2026-07-05-frames-gallery-filters.md` into the same repo-relative paths, then:

```bash
git add docs/superpowers/specs/2026-07-05-frames-gallery-filters-design.md docs/superpowers/plans/2026-07-05-frames-gallery-filters.md
git commit -m "docs: frames gallery filters design spec + implementation plan"
```

- [ ] **Step 1: Write the failing test** — append to `test/frame-archive.test.js`:

```js
test('list: until/before/fmin/fmax/snrMin/standard filters + combos', () => {
  const dir = tmp();
  const a = new FrameArchive({ dir, indexFile: join(dir, 'i.json') });
  a.ingest('hackrf', payload(100, 949, { standard: 'PAL', sync_snr_db: 26 }));
  a.ingest('hackrf', payload(200, 5865, { standard: 'NTSC', sync_snr_db: 9 }));
  a.ingest('hackrf', payload(300, 5745, { standard: 'PAL', sync_snr_db: null }));
  assert.deepEqual(a.list({ until: 200 }).map((f) => f.ts), [200, 100]);        // inclusive
  assert.deepEqual(a.list({ before: 200 }).map((f) => f.ts), [100]);            // strict cursor
  assert.deepEqual(a.list({ since: 100, until: 250 }).map((f) => f.ts), [200]);
  assert.deepEqual(a.list({ fmin: 5000, fmax: 6100 }).map((f) => f.ts), [300, 200]);
  assert.deepEqual(a.list({ fmax: 1000 }).map((f) => f.ts), [100]);
  assert.deepEqual(a.list({ snrMin: 10 }).map((f) => f.ts), [100]);             // 9 < 10; null excluded
  assert.deepEqual(a.list({ standard: 'pal' }).map((f) => f.ts), [300, 100]);   // case-insensitive
  assert.deepEqual(a.list({ fmin: 5000, standard: 'PAL' }).map((f) => f.ts), [300]); // combo
  assert.equal(a.list({}).length, 3);                                           // all unset = everything
});
```

(`payload(ts, center, extra)` and `tmp()` already exist at the top of this test file.)

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test test/frame-archive.test.js`
Expected: FAIL — `list({ until: 200 })` returns all 3 (new opts ignored).

- [ ] **Step 3: Implement** — replace the `list()` method in `lib/frame-archive.js` with:

```js
  // Newest-first metadata for GET /api/frames. Times are epoch seconds:
  // since = strictly newer (ts > since), until = inclusive, before = strict
  // pagination cursor (ts < before). fmin/fmax bound center_mhz inclusively;
  // snrMin drops entries with null sync_snr_db; standard is case-insensitive.
  // All numeric opts treat 0 as "unset".
  list({ scanner = '', since = 0, until = 0, before = 0, fmin = 0, fmax = 0,
         snrMin = 0, standard = '', limit = 200 } = {}) {
    const std = String(standard || '').toLowerCase();
    const out = [];
    for (let i = this._entries.length - 1; i >= 0 && out.length < limit; i--) {
      const e = this._entries[i];
      if (scanner && e.scanner_id !== scanner) continue;
      if (since && e.ts <= since) continue;
      if (until && e.ts > until) continue;
      if (before && e.ts >= before) continue;
      if (fmin && e.center_mhz < fmin) continue;
      if (fmax && e.center_mhz > fmax) continue;
      if (snrMin && !(e.sync_snr_db >= snrMin)) continue;
      if (std && String(e.standard || '').toLowerCase() !== std) continue;
      out.push({ ...e, url: `/api/frames/${e.id}.png` });
    }
    return out;
  }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test test/frame-archive.test.js`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add lib/frame-archive.js test/frame-archive.test.js
git commit -m "feat(frames): list() filters — until/before/fmin/fmax/snrMin/standard"
```

---

### Task 2: `/api/frames` — pass the filter query params through

**Files:**
- Modify: `dashboard/server.js` (the `GET /api/frames` handler)
- Test: `test/server.test.js` (append)

**Interfaces:**
- Consumes: Task 1's `list()` opts.
- Produces: query params `scanner, since, until, before, fmin, fmax, snr_min, standard, limit` (note: HTTP name `snr_min` → opt `snrMin`). NaN/absent numerics → 0 (unset).

- [ ] **Step 1: Write the failing test** — append to `test/server.test.js` (helpers `FR_PNG`, `startWith`, `login` already exist there):

```js
test('GET /api/frames passes filter query params through to list()', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'frq-'));
  const frames = new FrameArchive({ dir, indexFile: join(dir, 'i.json') });
  const mk = (ts, mhz, std, snr) => frames.ingest('hackrf', {
    ts, center_mhz: mhz, standard: std, line_hz: 15625,
    sync_snr_db: snr, frame_png_b64: FR_PNG.toString('base64'),
  });
  mk(100, 949, 'PAL', 26); mk(200, 5865, 'NTSC', 9); mk(300, 5745, 'PAL', 31);
  const { server, base } = await startWith({ devices: [] }, { ...config, frames });
  const cookie = await login(base);
  const get = async (qs) =>
    (await (await fetch(`${base}/api/frames?${qs}`, { headers: { cookie } })).json()).map((f) => f.ts);
  assert.deepEqual(await get('fmin=5000&fmax=6100'), [300, 200]);
  assert.deepEqual(await get('snr_min=10'), [300, 100]);
  assert.deepEqual(await get('standard=pal'), [300, 100]);
  assert.deepEqual(await get('until=200'), [200, 100]);
  assert.deepEqual(await get('before=300'), [200, 100]);
  assert.deepEqual(await get('since=100&until=250'), [200]);
  assert.deepEqual(await get('fmin=abc'), [300, 200, 100]);        // NaN → unset
  server.close();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test test/server.test.js`
Expected: FAIL — `fmin=5000&fmax=6100` returns all 3 ts (params not forwarded).

- [ ] **Step 3: Implement** — in `dashboard/server.js`, replace the body of the `app.get('/api/frames', ...)` handler with:

```js
  app.get('/api/frames', requireAuth, (req, res) => {
    if (!config.frames) return res.json([]);
    const limit = Math.min(2000, Math.max(1, Number(req.query.limit) || 200));
    res.json(config.frames.list({
      scanner: String(req.query.scanner || ''),
      since: Number(req.query.since) || 0,
      until: Number(req.query.until) || 0,
      before: Number(req.query.before) || 0,
      fmin: Number(req.query.fmin) || 0,
      fmax: Number(req.query.fmax) || 0,
      snrMin: Number(req.query.snr_min) || 0,
      standard: String(req.query.standard || ''),
      limit,
    }));
  });
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test test/server.test.js`
Expected: PASS (18 tests).

- [ ] **Step 5: Commit**

```bash
git add dashboard/server.js test/server.test.js
git commit -m "feat(frames): /api/frames filter query params"
```

---

### Task 3: gallery module — `BAND_PRESETS`, `buildFramesQuery`, `toLocalDatetime`, `scannerOptions`

**Files:**
- Modify: `dashboard/public/frames-gallery.js` (append new exports; `galleryHtml` untouched until Task 4)
- Test: `test/frames-gallery.test.js` (append)

**Interfaces:**
- Consumes: nothing new.
- Produces (used by Task 4 + 5):
  - `BAND_PRESETS` — `{ '0.9': {fmin:800, fmax:1000, label:'0.9G (шум)'}, '1.2': {fmin:1000, fmax:1500, label:'1.2G'}, '2.4': {fmin:2200, fmax:2700, label:'2.4G'}, '5.8': {fmin:5000, fmax:6100, label:'5.8G'} }`.
  - `buildFramesQuery(filter, extra = {}) → string` — filter = `{ scanner, band, standard, snrMin, from, to }` (band = a BAND_PRESETS key or ''; from/to = `datetime-local` strings or ''); extra = `{ limit, before }`. Maps from→`since`, to→`until` via `Math.floor(new Date(v).getTime()/1000)`.
  - `toLocalDatetime(ms) → 'YYYY-MM-DDTHH:MM'` (local time, minute precision) — for presets to fill the від field.
  - `scannerOptions(frames, registryIds = [], selected = '') → string[]` — sorted union.

- [ ] **Step 1: Write the failing test** — append to `test/frames-gallery.test.js`:

```js
import { BAND_PRESETS, buildFramesQuery, toLocalDatetime, scannerOptions } from '../dashboard/public/frames-gallery.js';

test('buildFramesQuery: defaults, band mapping, snr, before/limit', () => {
  assert.equal(buildFramesQuery({}), 'limit=200');
  const q = new URLSearchParams(buildFramesQuery(
    { scanner: 'hackrf', band: '5.8', standard: 'PAL', snrMin: '12' },
    { limit: 200, before: 123.5 }));
  assert.equal(q.get('scanner'), 'hackrf');
  assert.equal(q.get('fmin'), '5000');
  assert.equal(q.get('fmax'), '6100');
  assert.equal(q.get('standard'), 'PAL');
  assert.equal(q.get('snr_min'), '12');
  assert.equal(q.get('before'), '123.5');
  assert.equal(buildFramesQuery({ band: 'nope', snrMin: '0' }), 'limit=200'); // unknown band + snr 0 ignored
});

test('buildFramesQuery: from/to → since/until epoch (TZ-independent roundtrip)', () => {
  const from = '2026-07-05T10:30';
  const q = new URLSearchParams(buildFramesQuery({ from, to: '' }));
  assert.equal(q.get('since'), String(Math.floor(new Date(from).getTime() / 1000)));
  assert.equal(q.get('until'), null);
});

test('toLocalDatetime: format + roundtrips through Date at minute precision', () => {
  const ms = 1783111522000;
  const s = toLocalDatetime(ms);
  assert.match(s, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/);
  assert.equal(new Date(s).getTime(), Math.floor(ms / 60000) * 60000);
});

test('scannerOptions: union of registry, frames and selection, sorted', () => {
  const frames = [fr(), fr({ scanner_id: 'bladerf' })];
  assert.deepEqual(scannerOptions(frames, ['rx-pi'], 'zeta'), ['bladerf', 'hackrf', 'rx-pi', 'zeta']);
  assert.deepEqual(scannerOptions([], [], ''), []);
});

test('BAND_PRESETS ranges match the spec', () => {
  assert.deepEqual(BAND_PRESETS['0.9'], { fmin: 800, fmax: 1000, label: '0.9G (шум)' });
  assert.deepEqual(BAND_PRESETS['2.4'], { fmin: 2200, fmax: 2700, label: '2.4G' });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test test/frames-gallery.test.js`
Expected: FAIL — named exports not found.

- [ ] **Step 3: Implement** — append to `dashboard/public/frames-gallery.js`:

```js
// Band presets for the toolbar — keys are the <select> values, ranges go to
// the server as fmin/fmax (MHz, inclusive).
export const BAND_PRESETS = {
  '0.9': { fmin: 800, fmax: 1000, label: '0.9G (шум)' },
  '1.2': { fmin: 1000, fmax: 1500, label: '1.2G' },
  '2.4': { fmin: 2200, fmax: 2700, label: '2.4G' },
  '5.8': { fmin: 5000, fmax: 6100, label: '5.8G' },
};

// filter {scanner, band, standard, snrMin, from, to} + extra {limit, before}
// → /api/frames querystring. from/to are datetime-local strings interpreted in
// the browser's timezone; the server only ever sees epoch seconds.
export function buildFramesQuery(filter = {}, extra = {}) {
  const q = new URLSearchParams();
  q.set('limit', String(extra.limit || 200));
  if (filter.scanner) q.set('scanner', filter.scanner);
  const band = BAND_PRESETS[filter.band];
  if (band) { q.set('fmin', String(band.fmin)); q.set('fmax', String(band.fmax)); }
  if (filter.standard) q.set('standard', filter.standard);
  const snr = Number(filter.snrMin);
  if (Number.isFinite(snr) && snr > 0) q.set('snr_min', String(snr));
  const from = filter.from ? Math.floor(new Date(filter.from).getTime() / 1000) : 0;
  const to = filter.to ? Math.floor(new Date(filter.to).getTime() / 1000) : 0;
  if (from) q.set('since', String(from));
  if (to) q.set('until', String(to));
  if (extra.before) q.set('before', String(extra.before));
  return q.toString();
}

// Epoch ms → local 'YYYY-MM-DDTHH:MM' for <input type="datetime-local">.
export function toLocalDatetime(ms) {
  const t = new Date(ms);
  const p = (n) => String(n).padStart(2, '0');
  return `${t.getFullYear()}-${p(t.getMonth() + 1)}-${p(t.getDate())}T${p(t.getHours())}:${p(t.getMinutes())}`;
}

// Scanner <select> options: registry scanners ∪ ids in the current result ∪
// the current selection — so applying a scanner filter doesn't collapse the list.
export function scannerOptions(frames, registryIds = [], selected = '') {
  return [...new Set([...registryIds, ...scannerIds(frames), ...(selected ? [selected] : [])])].sort();
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test test/frames-gallery.test.js`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add dashboard/public/frames-gallery.js test/frames-gallery.test.js
git commit -m "feat(frames): band presets, query builder, datetime + scanner-option helpers"
```

---

### Task 4: `galleryHtml` — filter toolbar + «Показати ще»

**Files:**
- Modify: `dashboard/public/frames-gallery.js` (replace `galleryHtml`)
- Test: `test/frames-gallery.test.js` (replace the three old `galleryHtml:` tests; keep everything else)

**Interfaces:**
- Consumes: Task 3 exports; existing `frameCaption`, `escapeHtml`, `scannerIds`.
- Produces: `galleryHtml(frames, { filter = {}, scanners = [], hasMore = false } = {}) → string`. **Signature change** (old second arg was a scanner string that also filtered tiles client-side — removed; the server filters now, this renders every frame given). Element ids consumed by Task 5: selects `#frames-scanner`, `#frames-band`, `#frames-standard`; inputs `#frames-snr` (number), `#frames-from`, `#frames-to` (datetime-local); preset buttons `[data-tp="1h"|"24h"|"7d"|"all"]`; buttons `#frames-refresh` (kept), `#frames-more` (only when `hasMore`); tiles `.fr-tile[data-src][data-cap]` (unchanged).

- [ ] **Step 1: Replace the three old `galleryHtml:` tests** in `test/frames-gallery.test.js` (`galleryHtml: tiles with...`, `galleryHtml: scanner filter narrows...`, `galleryHtml: empty state + html escaping`) with:

```js
test('galleryHtml: toolbar reflects the filter, tiles render all given frames', () => {
  const frames = [fr(), fr({ scanner_id: 'bladerf', url: '/api/frames/bladerf/1_1.png' })];
  const html = galleryHtml(frames, {
    filter: { scanner: 'bladerf', band: '5.8', standard: 'PAL', snrMin: '12', from: '2026-07-05T10:30', to: '' },
    scanners: ['rx-pi'],
  });
  assert.match(html, /id="frames-refresh"/);
  assert.match(html, /<option value="bladerf" selected>/);
  assert.match(html, /<option value="rx-pi">/);                       // registry id present
  assert.match(html, /<option value="5.8" selected>/);                // band
  assert.match(html, /<option value="PAL" selected>/);                // standard
  assert.match(html, /id="frames-snr"[^>]*value="12"/);
  assert.match(html, /id="frames-from"[^>]*value="2026-07-05T10:30"/);
  assert.match(html, /data-tp="24h"/);
  assert.ok(html.includes('/api/frames/hackrf/'));                    // NO client-side tile filtering
  assert.ok(html.includes('/api/frames/bladerf/'));
  assert.match(html, /<img loading="lazy" src="\/api\/frames\/hackrf\/1751500000000_5865\.png"/);
});

test('galleryHtml: «Показати ще» only when hasMore', () => {
  assert.match(galleryHtml([fr()], { hasMore: true }), /id="frames-more"/);
  assert.ok(!galleryHtml([fr()], { hasMore: false }).includes('frames-more'));
  assert.ok(!galleryHtml([fr()]).includes('frames-more'));
});

test('galleryHtml: empty state + html escaping', () => {
  assert.match(galleryHtml([]), /Кадрів немає\./);
  const html = galleryHtml([fr({ scanner_id: '<x&>' })]);
  assert.ok(!html.includes('<x&>'));
  assert.ok(html.includes('&lt;x&amp;&gt;'));
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test test/frames-gallery.test.js`
Expected: FAIL — old `galleryHtml` ignores the options object (no toolbar ids/selected states).

- [ ] **Step 3: Implement** — replace the whole `galleryHtml` function in `dashboard/public/frames-gallery.js` with:

```js
// Full modal body: header, two-row filter toolbar, tile grid, optional «Ще».
// The server does ALL filtering — this renders every frame it is given; the
// toolbar only reflects `filter` back so the controls keep their state.
export function galleryHtml(frames, { filter = {}, scanners = [], hasMore = false } = {}) {
  const all = frames || [];
  const opt = (value, label, selected) =>
    `<option value="${escapeHtml(value)}"${selected ? ' selected' : ''}>${escapeHtml(label)}</option>`;
  const scanOpts = [opt('', 'всі сканери', !filter.scanner)]
    .concat(scannerOptions(all, scanners, filter.scanner || '').map((id) => opt(id, id, id === filter.scanner)))
    .join('');
  const bandOpts = [opt('', 'всі бенди', !filter.band)]
    .concat(Object.entries(BAND_PRESETS).map(([k, b]) => opt(k, b.label, k === filter.band)))
    .join('');
  const stdOpts = [opt('', 'всі стандарти', !filter.standard)]
    .concat(['PAL', 'NTSC'].map((s) => opt(s, s, s === filter.standard)))
    .join('');
  const presets = [['1h', '1 год'], ['24h', '24 год'], ['7d', '7 д'], ['all', 'все']]
    .map(([k, label]) => `<button type="button" class="btn-ghost" data-tp="${k}">${label}</button>`)
    .join('');
  const tiles = all.map((f) => {
    const cap = frameCaption(f);
    return `<button type="button" class="fr-tile" data-src="${escapeHtml(f.url)}" data-cap="${escapeHtml(cap)}">
      <img loading="lazy" src="${escapeHtml(f.url)}" alt="кадр" />
      <span class="fr-cap">${escapeHtml(cap)}</span>
    </button>`;
  }).join('');
  const grid = all.length ? `<div class="fr-grid">${tiles}</div>` : '<p class="muted">Кадрів немає.</p>';
  const more = hasMore ? '<div class="fr-more"><button type="button" id="frames-more" class="btn-ghost">Показати ще</button></div>' : '';
  return `<h2>🖼️ Кадри <button type="button" id="frames-refresh" class="btn-ghost">оновити</button></h2>
    <div class="fr-filters">
      <div class="fr-toolbar">
        <label>Сканер <select id="frames-scanner">${scanOpts}</select></label>
        <label>Бенд <select id="frames-band">${bandOpts}</select></label>
        <label>Стандарт <select id="frames-standard">${stdOpts}</select></label>
        <label>SNR ≥ <input type="number" id="frames-snr" min="0" step="1" value="${escapeHtml(filter.snrMin || '')}" placeholder="dB" /> dB</label>
      </div>
      <div class="fr-toolbar">
        ${presets}
        <label>від <input type="datetime-local" id="frames-from" value="${escapeHtml(filter.from || '')}" /></label>
        <label>до <input type="datetime-local" id="frames-to" value="${escapeHtml(filter.to || '')}" /></label>
      </div>
    </div>
    ${grid}
    ${more}
    <div class="form-actions"><button type="button" data-close class="btn-primary">Закрити</button></div>`;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test test/frames-gallery.test.js`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add dashboard/public/frames-gallery.js test/frames-gallery.test.js
git commit -m "feat(frames): filter toolbar + «Показати ще» in the gallery HTML"
```

---

### Task 5: `app.js` wiring + styles

**Files:**
- Modify: `dashboard/public/app.js` (the frames-gallery section)
- Modify: `dashboard/public/styles.css`

**Interfaces:**
- Consumes: `galleryHtml(frames, {filter, scanners, hasMore})`, `buildFramesQuery`, `toLocalDatetime` (Tasks 3–4); existing `showModal`, `openImageModal`, `formModal`, `scannersFromRegistry`.
- Produces: end-user behavior. Browser-only glue — no unit tests (same as the rest of `app.js`); verify via the full suite + manual check after deploy.

- [ ] **Step 1: Rework the frames section of `app.js`**

Update the import (add the two new names):

```js
import { galleryHtml, buildFramesQuery, toLocalDatetime } from '/frames-gallery.js';
```

Replace the whole `// ---- frames gallery ...` block (the `framesCache` variable and `openFrames`) with:

```js
// ---- frames gallery (server archive of demodulated frames) ----
// Filter state survives modal close/open; resets on page reload. The server
// does all filtering — every change re-fetches; «Ще» appends older frames.
const FRAMES_LIMIT = 200;
let framesFilter = { scanner: '', band: '', standard: '', snrMin: '', from: '', to: '' };
let framesList = [];
let framesHasMore = false;

async function fetchFrames({ append = false } = {}) {
  const extra = { limit: FRAMES_LIMIT };
  if (append && framesList.length) extra.before = framesList[framesList.length - 1].ts;
  let batch = [];
  try {
    const res = await fetch(`/api/frames?${buildFramesQuery(framesFilter, extra)}`);
    if (res.ok) batch = await res.json();
  } catch { /* render what we have */ }
  framesList = append ? framesList.concat(batch) : batch;
  framesHasMore = batch.length === FRAMES_LIMIT;
  showModal(galleryHtml(framesList, {
    filter: framesFilter,
    scanners: scannersFromRegistry.map((s) => s.id),
    hasMore: framesHasMore,
  }));
}

function openFrames() { fetchFrames(); }
```

In the `formModal` click handler, after the `.fr-tile` lines, add the «Ще» + time-preset delegation:

```js
  if (e.target.closest('#frames-more')) fetchFrames({ append: true });
  const tp = e.target.closest('[data-tp]');
  if (tp) {
    const hours = { '1h': 1, '24h': 24, '7d': 168 }[tp.dataset.tp];
    framesFilter.from = hours ? toLocalDatetime(Date.now() - hours * 3600e3) : '';
    framesFilter.to = '';
    fetchFrames();
  }
```

Replace the existing frames `change` delegate (the `#frames-scanner → showModal(galleryHtml(framesCache, sel.value))` handler) with:

```js
// Frames-gallery filters: any change updates the state and re-fetches from the server.
formModal.addEventListener('change', (e) => {
  const key = {
    'frames-scanner': 'scanner', 'frames-band': 'band', 'frames-standard': 'standard',
    'frames-snr': 'snrMin', 'frames-from': 'from', 'frames-to': 'to',
  }[e.target.id];
  if (key) { framesFilter[key] = e.target.value; fetchFrames(); }
});
```

- [ ] **Step 2: Styles** — in `dashboard/public/styles.css`, replace the `/* frames gallery modal */` block's first two rules with (adds `.fr-filters`, inputs, labels, «Ще»):

```css
/* frames gallery modal */
.fr-filters { display:flex; flex-direction:column; gap:.4rem; margin:.2rem 0 .8rem; }
.fr-toolbar { display:flex; gap:.6rem; align-items:center; flex-wrap:wrap; color:#cbd5e1; font-size:.85rem; }
.fr-toolbar label { display:flex; align-items:center; gap:.3rem; }
.fr-toolbar select, .fr-toolbar input { background:#0c1118; color:#cfd6e0; border:1px solid var(--line); border-radius:4px; font:inherit; padding:.15rem .3rem; }
.fr-toolbar input[type="number"] { width:4.5rem; }
.fr-more { display:flex; justify-content:center; margin:.6rem 0 0; }
```

(The `.fr-grid`, `.fr-tile`, `.fr-tile img`, `.fr-cap` rules below it stay unchanged. The old `.fr-toolbar` had `margin:.2rem 0 .8rem` — that moved to `.fr-filters`; `select` styling now also covers `input` and the stray `margin-left` is gone.)

- [ ] **Step 3: Syntax-check + full suite**

Run: `node -e "import('./dashboard/public/frames-gallery.js').then(()=>console.log('OK'))" && npm test`
Expected: `OK`, all tests PASS (suite grows to 105).

- [ ] **Step 4: Commit**

```bash
git add dashboard/public/app.js dashboard/public/styles.css
git commit -m "feat(frames): wire filter toolbar, time presets and «Показати ще»"
```

---

## Manual check (after deploy)

Over WG or `https://rerfpv.ksm.in.ua`: open 🖼️ Кадри → real hackrf frames visible; band «0.9G (шум)» shows the 947–949 МГц frames, «5.8G» hides them; SNR ≥ 30 leaves only strong ones; preset «1 год» fills «від»; clearing filters brings everything back; if >200 match, «Показати ще» appends older tiles without duplicates.

## Spec-coverage self-check (done while writing this plan)

- `list()` filters until/before/fmin/fmax/snrMin/standard, `since` untouched — Task 1 ✓
- `/api/frames` param passthrough incl. `snr_min` naming + NaN-ignore — Task 2 ✓
- Band presets exact MHz ranges, query builder, datetime-local↔epoch client-side, scanner-options union — Task 3 ✓
- Two-row toolbar reflecting state, presets 1г/24г/7д/все, «Показати ще» visibility rule, no client-side tile filtering, empty state kept — Task 4 ✓
- Filter state in app.js survives reopen, change→re-fetch, preset fills «від», «Ще» appends via `before` cursor — Task 5 ✓
- Tests per spec (archive filters, HTTP passthrough, query builder/toolbar/«Ще») — Tasks 1–4 ✓; deploy = dashboard-only surgical update ✓
