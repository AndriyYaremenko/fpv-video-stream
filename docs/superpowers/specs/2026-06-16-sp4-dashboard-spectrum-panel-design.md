# SP4 — Dashboard Spectrum Panel — Design Spec

**Date:** 2026-06-16
**Author:** andriy@netlife.com.ua
**Status:** Approved (pending written-spec review)

## 1. Context & Goal

The HackRF scan service (Sub-project 1, merged) is a Pi-side daemon that sweeps 1.2/2.4/5.8 GHz,
detects video carriers, classifies analog/digital/unknown, and **already POSTs its payload to the
dashboard telemetry hook** (`POST /api/telemetry/<scanner-id>`) plus a local state file. The
dashboard stores each device's last telemetry payload and attaches it as `d.telemetry` on every
status snapshot (SSE `/api/stream` + `GET /api/devices`).

**SP4 renders that scan data on the dashboard.** A scanner is **not** a camera (it never publishes
video), so it must not get a dead WebRTC tile — its data belongs in a dedicated panel.

This spec covers SP4 only. SP2 (reception hardware) and SP3 (auto-tune) are unaffected and need
hardware; SP4 needs none.

## 2. Confirmed decisions

| Decision | Value |
|---|---|
| Layout | **Dedicated "Spectrum" panel** above the camera grid (full width, hidden when no scanners) |
| Panel fidelity | Occupancy bars + **per-band spectrum line chart** (vanilla canvas) + detection table |
| Scanner identity | Explicit **`kind` field** in the registry (`camera` \| `scanner`, default `camera`) |
| Scanner liveness | Derived from **telemetry freshness** (`now − telemetry._ts < 15 s`), not MediaMTX paths |
| Band ranges for chart X-axis | **Client constant `BAND_RANGES`** (matches SP1 config); SP1 payload unchanged |
| Spectrum representation | Current-snapshot line per band (waterfall/time-history is out of scope, future) |

## 3. Architecture & data flow

```
Pi scanner ──POST /api/telemetry/<id>──▶ dashboard (telemetry Map: last payload per id)
                                              │
   snapshot() merges registry + MediaMTX paths + telemetry; adds `kind`; scanner online
   from telemetry freshness                   │
                                              ▼
   SSE /api/stream ──▶ app.js render(): split by kind
        cameras  ──▶ existing WHEP tile grid (unchanged)
        scanners ──▶ spectrum.js renderSpectrum(panel, scanners)
                        occupancy bars · canvas line per band · detection table
```

- The scanner payload (`{scanner_id, ts, detections[], occupancy{}, spectrum{}}`) already arrives
  as `d.telemetry` — **no new endpoint** is needed.
- Camera path is untouched: ingest, WHEP playback, tiles, restart logic all unchanged.

## 4. Server / registry changes (minimal, backward-compatible)

### 4.1 `lib/registry.js`
- `addDevice(reg, { id, name, location, kind })` accepts `kind`; defaults to `"camera"`; rejects
  values other than `camera`/`scanner`. Stores `kind` on the device object in `devices.yml`.
- Devices already in `devices.yml` without `kind` are treated as `camera` everywhere (read-time
  default; the file is not rewritten just to add the field).
- `publish_pass` is still generated for scanners (uniform schema; unused for scanners — harmless).
- `updateDevice` is unchanged (name/location only; `kind` is set at creation, immutable here).

### 4.2 `lib/render-config.js`
- Exclude scanners from MediaMTX auth/paths: the per-device publish users are built from
  `devices.filter((d) => d.kind !== 'scanner')`. A scanner never publishes video, so it gets no
  `authInternalUsers` publish entry. This is the only change here.

### 4.3 `lib/status.js`
- `mergeStatus` passes through `kind: d.kind || 'camera'` on each returned device. (Camera online
  logic via MediaMTX `ready` is unchanged; scanners have no path, so base `online` is `false`.)

### 4.4 `dashboard/server.js`
- `snapshot()`: after merge, for `kind === 'scanner'` set `online = !!tel && (now - tel._ts) < SCANNER_FRESH_MS` (15000). Scanner `bitrateKbps`/`readers`/`uptimeSec` stay `null`. `d.telemetry`
  attachment is unchanged.
- `POST /api/devices`: accept `kind` from the body, pass to `addDevice`. For a scanner the response
  omits the RTSP/SRT push block (irrelevant) and instead returns a small telemetry hint
  (`{ device, scanner: { telemetryPath: '/api/telemetry/<id>' } }`); cameras return `{ device, push }`
  exactly as today.
- The telemetry hook, SSE stream, and other endpoints are unchanged.

## 5. Frontend changes

### 5.1 `dashboard/public/index.html`
- Add `<section id="spectrum-panel" class="hidden"></section>` immediately above `#grid`.

### 5.2 `dashboard/public/app.js`
- In `render()`, split the incoming devices by `kind` (`splitByKind`). Cameras → existing tile flow
  (unchanged). Scanners → `renderSpectrum(panel, scanners)`; toggle `#spectrum-panel.hidden` based
  on whether any scanners exist.
- Add-device form gains a **Тип** selector (Камера / Сканер). On submit, send `kind`. The success
  modal: for `scanner`, show the telemetry hint (use this `id` as `SCAN_ID`; posts to
  `/api/telemetry/<id>`) instead of the RTSP/SRT push commands.
- Scanner edit (name/location) and delete reuse the existing `PATCH`/`DELETE` endpoints, surfaced
  from the panel block header (not a tile).

### 5.3 `dashboard/public/spectrum.js` (new)
Pure, DOM-free helpers (unit-tested via `node --test`):
- `splitByKind(devices) -> { cameras, scanners }`
- `BAND_RANGES = { '1.2G': [1080, 1360], '2.4G': [2370, 2510], '5.8G': [5645, 5945] }`
- `classColor(cls)` → CSS color for `analog` | `digital` | `unknown`
- `psdToPoints(psd, width, height, dbMin, dbMax)` → array of `{x, y}` polyline points
- `detectionX(centerMhz, band, width)` → x-pixel using `BAND_RANGES` (clamped to [0, width])
- `fmtFreq(mhz)`, `fmtPct(fraction)` formatting helpers

DOM/canvas render functions (not unit-tested, like `whep.js`):
- `renderSpectrum(container, scanners)` builds one block per scanner: header (name/location +
  online badge + edit/delete/info actions), per-band occupancy bars, a `<canvas>` line chart per
  band (PSD polyline via `psdToPoints` + detection markers via `detectionX`), and a detection
  table.

### 5.4 `dashboard/public/styles.css`
- Styles for the panel, canvas charts, occupancy bars, and the detection table (class-colored
  rows). Follows the existing dark dashboard theme.

## 6. Detection table & chart

- **Detection table** columns: band · frequency (`center_mhz` + `channel` if present) · class
  (color-coded) · RSSI/power (`power_dbm`) · bandwidth (`bandwidth_mhz`) · confidence (`fmtPct`).
  Rows sorted by `power_dbm` descending.
- **Spectrum chart** per band: PSD polyline from `telemetry.spectrum[band]` scaled to the canvas;
  vertical markers at each detection's `center_mhz` (color by class). X-axis spans the band's
  `BAND_RANGES`; exact frequencies come from the table (authoritative).
- **Occupancy bars** per band: a small bar showing `telemetry.occupancy[band]` (busy fraction).

## 7. Edge cases

- No scanners registered → panel hidden (`#spectrum-panel.hidden`).
- Scanner offline (no/stale telemetry) → block shows offline badge + "немає даних", empty charts.
- Empty `detections` → table shows "немає активних передавачів".
- Missing `spectrum`/`occupancy` keys in a payload → render whatever is present; guard against
  `undefined` (no throw).
- Backward compatibility → any device without `kind` renders as a camera, exactly as today.

## 8. Testing (`node --test`, matching the existing `test/` suite)

- `test/registry.test.js` — `kind` defaults to `camera`; `kind=scanner` is stored; an invalid kind
  is rejected.
- `test/render-config.test.js` — a scanner device produces **no** `authInternalUsers` publish entry;
  cameras still do.
- `test/status.test.js` — `mergeStatus` passes through `kind` (and defaults missing `kind` to camera).
- `test/server.test.js` — `POST /api/devices` with `kind=scanner` creates a scanner and the response
  has no `push`; `GET /api/devices` marks a scanner `online` when its telemetry is fresh and
  `offline` when stale; a camera is unaffected.
- `test/spectrum.test.js` (new) — pure helpers: `psdToPoints` scaling (endpoints, clamping),
  `detectionX` (band mapping + clamp), `classColor` (3 classes), `splitByKind`.

Frontend DOM/canvas rendering is validated manually in the browser (no jsdom harness in this repo),
consistent with how `app.js`/`whep.js` are handled today.

## 9. Deliverables (file list)

```
lib/registry.js              (change: kind field + validation)
lib/render-config.js         (change: exclude scanners from authInternalUsers)
lib/status.js                (change: pass through kind)
dashboard/server.js          (change: scanner online-from-telemetry; POST kind; scanner hint)
dashboard/public/index.html  (change: #spectrum-panel)
dashboard/public/app.js      (change: split by kind; kind selector; scanner success modal)
dashboard/public/styles.css  (change: panel/chart/table/occupancy styles)
dashboard/public/spectrum.js (new: pure helpers + renderSpectrum)
test/registry.test.js        (add cases)
test/render-config.test.js   (add cases)
test/status.test.js          (add cases)
test/server.test.js          (add cases)
test/spectrum.test.js        (new)
README.md                    (change: scanner-node section in the dashboard docs)
```

## 10. Out of scope (YAGNI for SP4)

- Waterfall / time-history spectrum (future polish; needs client-side history buffering).
- Manual channel override / commanding the scanner or receivers (belongs to SP2/SP3 once hardware
  exists).
- Changing the SP1 telemetry payload shape (band ranges live as a client constant instead).
- A frontend test harness (jsdom); pure helpers are extracted and unit-tested instead.

## 11. Assumptions / open items

- `BAND_RANGES` in the client must match SP1's `config.bands`. If SP1's band plan is customised, the
  chart x-scaling is approximate but the detection table (exact `center_mhz`) stays correct; update
  the constant if needed.
- Scanner `scanner_id` must be registered as a `kind=scanner` device (the telemetry hook 404s
  unknown ids — see SP1 README note). The add-device form's Тип selector is the primary way to do this.
- `SCANNER_FRESH_MS = 15 s` is a reasonable default given SP1 cycles every few seconds; tunable.
