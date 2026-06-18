# Dashboard MQTT Subscriber + Waterfall (SP-C) — Design Spec

**Date:** 2026-06-18
**Author:** andriy@netlife.com.ua
**Status:** Approved (pending written-spec review)

## 1. Context & Goal

Final sub-project of the MQTT effort. SP-A stood up the broker + `GET /api/mqtt` (browser sub creds);
SP-B made the Pi publish scan data to `fpv/<id>/{spectrum,detection,status}`. SP-C makes the
**browser dashboard subscribe to those topics over WSS** and render them — adding a **waterfall**
(time×frequency heatmap) per band — and **removes** the old HTTP scan path (`POST /api/telemetry`
+ SSE-driven scan render). Cameras stay on SSE/WHEP, unchanged.

This completes the cutover: after SP-C, scan data flows Pi → broker → browser entirely over MQTT.

## 2. Confirmed decisions

| Decision | Value |
|---|---|
| Scan transport (browser) | **MQTT-over-WSS** via `mqtt.js`, creds from `GET /api/mqtt`. Subscribe `fpv/+/{spectrum,detection,status}`. |
| Per-band cell | **Line + waterfall** (classic SDR): live PSD polyline on top, scrolling waterfall (history) below. |
| Scanner block layout | **3 bands in a row** (columns): occupancy strip, three line+waterfall cells, detection table full-width below. |
| Bands | **Data-driven** from the self-describing spectrum frames (`bands:[{id,low_mhz,high_mhz,psd}]`) — render whatever bands arrive; custom bands work without code change (config UI is later). |
| Scanner identity | Registry (`/api/devices`, `kind=scanner`) = metadata + management; MQTT = live data + presence; **joined by id** (registry scanner id must equal the Pi `SCAN_ID`). |
| Presence | From retained `fpv/<id>/status` + LWT (replaces telemetry-freshness). |
| Old HTTP path | **Fully removed:** `POST /api/telemetry/:id`, the telemetry `Map`, the scanner-freshness logic. |
| `mqtt.js` delivery | **Vendored** as a static file (works over WG and public HTTPS; no CDN dependency). |
| Detections | Marked on the PSD line + listed in the detection table; the waterfall stays a clean heatmap. |
| Waterfall depth | Client-side ring buffer, ~60 frames per (scanner, band). |

## 3. Architecture & data flow

```
Cameras:  MediaMTX ──SSE /api/stream──▶ app.js render() ──WHEP──▶ <video>   (UNCHANGED)

Scanners: Pi ──MQTT──▶ broker ──WSS (wss://rerfpv.ksm.in.ua/mqtt)──▶ browser
                                   creds from GET /api/mqtt (login-gated)
   mqtt-scan.js: connect → subscribe fpv/+/{spectrum,detection,status}
                 → reduce messages into a client store (per scanner: status, detection, waterfalls)
                 → onChange → renderSpectrum(panel, scanners⨝store)
   registry scanners (/api/devices, kind=scanner) supply name/location + add/edit/delete/info
```

- **Two independent live loops:** SSE drives cameras (unchanged); MQTT drives the scan panel. They
  share the device registry only for scanner metadata + management.
- The browser learns the WSS URL + `sub` creds from `GET /api/mqtt` (added in SP-A), exactly like it
  learns WHEP creds from `/api/config`.
- The store is updated per message and triggers a panel re-render; the audio alert diffs new
  detection keys on each `detection` message (reusing `alert.js`).

## 4. Components / deliverables

```
dashboard/public/vendor/mqtt.min.js   (new: vendored mqtt.js browser build)
dashboard/public/mqtt-scan.js         (new: WSS subscriber + PURE store reducer + waterfall ring buffer)
dashboard/public/spectrum.js          (change: waterfall render + psdColor(); data-driven bands/detectionX)
dashboard/public/app.js               (change: start MQTT alongside SSE; scan render + alert from the store; scanner online from MQTT)
dashboard/public/styles.css           (change: waterfall + 3-column band layout styles)
dashboard/server.js                   (change: REMOVE POST /api/telemetry, telemetry Map, scanner-freshness; adjust scanner-create response)
test/server.test.js                   (change: remove telemetry/freshness tests; adjust scanner-create test)
test/mqtt-scan.test.js                (new: store reducer + psdColor + data-driven detectionX, node --test)
README.md                             (change: dashboard scan = MQTT note)
```

### 4.1 `mqtt-scan.js` — subscriber + pure store

- **Pure reducer (unit-tested, no network):**
  - `emptyStore()` → `{}` (map of scannerId → scanner state).
  - `reduce(store, topic, payload, opts?)` → new store. Parses `fpv/<id>/<kind>`:
    - `status` → set `store[id].online = payload.online`, `status_ts`.
    - `detection` → set `store[id].detection = {ts, detections, occupancy}`.
    - `spectrum` → for each band in `payload.bands`, push `{ts, psd}` into
      `store[id].waterfalls[bandId]` (ring buffer capped at `opts.depth ?? 60`), and set
      `store[id].bands[bandId] = {low_mhz, high_mhz}` (latest range) + `store[id].latestPsd[bandId] = psd`.
  - Pure → deterministic, fully testable without a broker.
- **`MqttScanClient` (browser only):** `connect({url,user,pass}, onChange)` — lazy-loads the vendored
  `mqtt` global, connects WSS, subscribes the three wildcards, calls `reduce` on each message, and
  invokes `onChange(store)` (debounced to animation frame). Reconnect handled by `mqtt.js`. Guarded
  so a malformed message never throws out of the handler.

### 4.2 `spectrum.js` — rendering (pure helpers stay unit-tested)

- Keep/adapt pure helpers: `psdToPoints`, `classColor`, `fmtFreq`, `fmtPct`, `splitByKind`.
- **`detectionX(centerMhz, low, high, width)`** — now takes explicit `low`/`high` (data-driven), not
  the hardcoded `BAND_RANGES` (which is removed; bands come from the frames).
- **New `psdColor(db, dbMin=-100, dbMax=-20)`** — pure dBm→CSS-color (the agreed scale) for the
  waterfall; unit-tested at boundaries.
- **`renderSpectrum(container, scanners, store, highlightKeys)`** — per scanner: header (name/location
  + online from `store[id].online` + info/edit/del), occupancy strip (from `store[id].detection.occupancy`),
  a row of 3 band cells (each: PSD line from `latestPsd` + detection marks via `detectionX`, and a
  waterfall canvas drawn from `waterfalls[bandId]` via `psdColor`), and the detection table.
- Bands rendered = the union of bands present in `store[id]` (data-driven). No data yet → "немає даних".

### 4.3 `app.js`

- After `loadConfig()`, also `fetch('/api/mqtt')`; if it returns a url, `new MqttScanClient().connect(...)`.
- On store change: `renderSpectrumPanel(scannersFromRegistry, store, newKeys)`; compute `newKeys` by
  diffing detection keys across all scanners' `store[id].detection.detections` (reuse `diffNewKeys`),
  beep if armed.
- SSE `render()` keeps handling cameras; scanners still come in the device list (for metadata +
  management), but their **online + live data come from the store**, not from `s.telemetry`.
- Scanner management (add/edit/delete/info) unchanged except the info modal (4.4).

### 4.4 `server.js` (and tests)

- **Remove:** `POST /api/telemetry/:id`, the `telemetry` Map, the per-scanner freshness branch in
  `snapshot()` (`SCANNER_FRESH_MS`, the `d.kind === 'scanner'` online override). Scanners appear in
  the snapshot with metadata only; the client overlays MQTT presence/data.
- **Scanner-create response** (`POST /api/devices` kind=scanner) + the info modal: replace the
  `telemetryPath: /api/telemetry/<id>` hint with the MQTT topic prefix `fpv/<id>` + a note that the
  Pi's `SCAN_ID` must equal `<id>`.
- **Keep:** `GET /api/mqtt` (SP-A), `/api/config`, device CRUD, SSE for cameras.
- **Tests:** remove the telemetry-hook + scanner-freshness tests; update the scanner-create test to
  assert the new response shape.

## 5. Testing / verification

- **Automated (node --test):** `test/mqtt-scan.test.js` — `reduce` handles status/detection/spectrum,
  ring-buffer cap, data-driven band capture, malformed-payload safety; `psdColor` boundaries;
  data-driven `detectionX`. `test/server.test.js` — `/api/telemetry` gone (404), snapshot no longer
  marks scanners online from freshness, scanner-create returns the MQTT hint. `node --check` on the
  browser-only files.
- **Ops verification (live, after deploy):** with broker (SP-A) + Pi (SP-B) up, open the dashboard
  (public HTTPS), log in → the scanner block shows ONLINE (from `status`), live PSD lines + scrolling
  waterfalls per band, occupancy, and the detection table; stopping the Pi flips it OFFLINE (LWT);
  the audio alert fires on a new detection. Cameras still play over WG.

## 6. Security

- Browser uses the subscribe-only `sub` creds from the login-gated `/api/mqtt` (SP-A). No new
  endpoints. The WSS endpoint is the broker fronted by traefik (SP-A); no new public ports.
- `mqtt.js` is vendored (no third-party CDN at runtime).

## 7. Deployment

After merge, rebuild + recreate only the dashboard (`docker compose up -d --no-deps dashboard`).
Requires SP-A (broker + traefik `/mqtt` route) and SP-B (Pi publishing) to be live for data. The
registry must contain a scanner whose **id equals the Pi `SCAN_ID`** (e.g. `hackrf`) for the join.

## 8. Caveats / limitations

- **WG-only without internet** can't reach the public `wss://rerfpv.ksm.in.ua/mqtt`. Use the public
  HTTPS dashboard for the spectrum panel; a WG-native `ws://10.8.0.1:9001` URL is a future option
  (the broker's WS listener is reachable on the WG IP, but `/api/mqtt` returns the single public URL).
- The waterfall history is client-side and resets on page reload (retained `spectrum` repaints the
  last frame immediately; history rebuilds as frames arrive).
- Scanner metadata still lives in the registry; a scanner with no matching registry entry would
  publish to MQTT but not appear in the panel (no metadata to render) — documented in §7.

## 9. Out of scope (YAGNI)

- A custom-bands **configuration UI** (the data-driven render already accepts arbitrary bands; the UI
  to define them is later).
- A WG-native `/api/mqtt` URL variant; per-scanner ACL scoping (SP-A §9).
- Persisting waterfall history across reloads; exporting/recording spectra.

## 10. Assumptions

- `GET /api/mqtt` (SP-A) returns `{url, user, pass}` for the logged-in browser; the WSS route
  (`wss://rerfpv.ksm.in.ua/mqtt`) is live (SP-A traefik). This branch builds on `feat/mqtt-broker`.
- The SP-B payloads match the SP-A contract (`fpv/<id>/spectrum` = `{scanner_id,ts,bands:[{id,low_mhz,high_mhz,psd}]}`; `detection` = `{scanner_id,ts,detections[],occupancy}`; `status` = `{online,ts}`).
- A maintained `mqtt.js` browser build is vendored into `dashboard/public/vendor/`.
