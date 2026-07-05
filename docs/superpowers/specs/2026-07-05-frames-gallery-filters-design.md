# Frames Gallery Filters + "Show More" Pagination — Design

**Date:** 2026-07-05
**Status:** Approved design, ready to implement (own branch off `main`).
**Builds on:** the server frame archive + 🖼️ gallery (PR #16, deployed 2026-07-03;
spec `docs/superpowers/specs/2026-07-03-server-frame-archive-design.md`).

## Goal
Let the operator narrow the 🖼️ Кадри gallery by **time range, frequency band, minimum sync SNR,
video standard** (plus the existing scanner filter), and page deeper than the newest 200 frames
with a **«Показати ще»** button. Today the gallery fetches `/api/frames?limit=200` and offers only
a client-side scanner select.

## Decisions (from the operator)
- Filters: **time range** (presets + exact від–до), **band presets** (mapped to MHz ranges, no
  freeform frequency fields), **min sync SNR**, **standard PAL/NTSC**. Scanner filter stays.
- Time UX: **preset buttons (1 год / 24 год / 7 д / все) + two `datetime-local` fields**; a preset
  fills the fields; empty "до" = open-ended.
- Pagination: **«Показати ще» button** (not infinite scroll, not a hard cap).
- Architecture: **all filtering server-side** (extends the existing `scanner`/`since` pattern) —
  client-side filtering can't page past what it fetched and caps at 2000.

## Server — `lib/frame-archive.js` `list()` + `GET /api/frames`
Extend `list(opts)` (all are cheap comparisons inside the existing newest-first loop; a non-matching
entry is skipped, the loop still stops at `limit` matches):

| opt | query param | semantics |
|---|---|---|
| `scanner` | `scanner` | exact `scanner_id` match (existing) |
| `since` | `since` | epoch s, `ts > since` — **strictly newer** (existing, unchanged) |
| `until` | `until` | epoch s, `ts <= until` (inclusive) |
| `before` | `before` | epoch s, `ts < before` — **pagination cursor**; «Ще» passes the ts of the oldest frame shown |
| `fmin` / `fmax` | `fmin` / `fmax` | MHz, inclusive bounds on `center_mhz` |
| `snrMin` | `snr_min` | `sync_snr_db >= N`; entries with `null` SNR are **excluded** when set |
| `standard` | `standard` | case-insensitive exact match (PAL/NTSC) |
| `limit` | `limit` | unchanged (default 200, max 2000) |

`GET /api/frames` parses the params (Number(...) for numerics, ignore NaN/0 as "unset") and passes
them through. `ts` is stored as float epoch seconds (`time.time()` on the Pi) so equal-ts cursor
collisions are practically impossible — a plain `ts < before` cursor is enough.

## UI — toolbar in the gallery modal
Pure HTML builders stay in `dashboard/public/frames-gallery.js` (node-testable, no DOM at module
scope); event wiring stays in `app.js`.

- **Row 1:** `Сканер ▼` · `Бенд ▼` · `Стандарт ▼` (всі/PAL/NTSC) · `SNR ≥ [number] dB`
- **Row 2:** time presets `1 год · 24 год · 7 д · все` + `від`/`до` `datetime-local` fields.
  (No extra refresh button — every change re-fetches immediately, and the existing «оновити» in the
  modal header stays.)
- **Band presets → MHz ranges** (sent as `fmin`/`fmax`):
  | preset | range MHz |
  |---|---|
  | 0.9G (GSM-шум) | 800–1000 |
  | 1.2G | 1000–1500 |
  | 2.4G | 2200–2700 |
  | 5.8G | 5000–6100 |
  | всі | no fmin/fmax |
- **Scanner options** = registry scanners (`scannersFromRegistry` in `app.js`, kind=scanner) ∪
  scanner ids present in the current result ∪ the current selection — so the list doesn't collapse
  to one entry once a scanner filter is applied (today options come only from fetched frames).
- Any filter change → immediate re-fetch + re-render. A time preset fills the від/до fields.
- **«Показати ще»** under the grid, shown only when the last fetch returned exactly `limit` frames;
  fetches with `before=<oldest shown ts>` and the same filters, **appends** to the grid.
- Empty state stays «Кадрів немає.».

## State
- Filter state lives in a module-level object in `app.js` (`framesFilter`) — survives modal
  close/open, resets on page reload. The frames accumulated across «Ще» fetches live next to it.
- `buildFramesQuery(filter)` — pure function in `frames-gallery.js` mapping the filter object to
  the querystring (band preset → fmin/fmax, datetime-local values → epoch seconds). Testable in node.

## Testing
- `test/frame-archive.test.js`: `until`/`before`/`fmin`/`fmax`/`snrMin` (incl. null-SNR exclusion) /
  `standard` filters + combinations; cursor returns strictly older frames.
- `test/server.test.js`: query params reach `list()` (filtered HTTP responses), NaN/absent params ignored.
- `test/frames-gallery.test.js`: toolbar HTML (selected states persist), `buildFramesQuery`
  (presets, datetime parsing, unset fields), «Показати ще» visibility rule.

## Deploy
Surgical dashboard-only update (build + `up -d --no-deps dashboard`), as documented — no compose,
env, broker, or Pi changes.

## Risks / notes
- `since` keeps its historical exclusive (`>`) semantics; the UI's «від» maps to it — the 1-second
  edge difference is irrelevant for this use.
- Filtering is O(n) over the in-memory index per request (n ≤ FRAMES_MAX 20000, авторизовані
  запити з дашборда) — no indexing needed.
- `datetime-local` values are in the operator's browser timezone; conversion to epoch happens
  client-side (`new Date(value).getTime()/1000`), so server semantics stay timezone-free.

Spec pattern follows the frame-archive spec; deploy via the documented surgical dashboard update.
