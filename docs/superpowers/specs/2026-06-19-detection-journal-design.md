# Detection Journal (history of RF detections) — Design Spec

**Date:** 2026-06-19
**Author:** andriy@netlife.com.ua
**Status:** Approved (pending written-spec review)

## 1. Context & Goal

The scan service publishes the current detections to `fpv/<id>/detection` (latest, retained, per
cycle). There is no history — you only ever see the live snapshot. The operator wants a **detection
journal**: a persistent log of what was detected and when, viewable on the dashboard.

The dashboard server (`dashboard/server.js`) is the only always-on, dashboard-reachable, persistent
component, so it owns the journal: it subscribes to `fpv/+/detection`, diffs each message against the
previous state per scanner, and records **appeared / gone** events — logging continuously even when no
browser is open.

## 2. Confirmed decisions

| Decision | Value |
|---|---|
| Owner | **`server.js` (always-on)** — subscribes to MQTT and logs regardless of dashboard viewers. Adds a node `mqtt` dependency. |
| Event | A transmitter **appearing** (a new `detectionKey`) **and disappearing** (`gone`) — both logged, deduped by key (not per-cycle). |
| Keying | The existing `detectionKey` (`band:channel`, else `band:round5(center_mhz)`) — mirrors `alert.js`. |
| Source topic | The existing `fpv/+/detection` (no Pi change). |
| Storage | In-memory ring (last `DETECTIONS_MAX`, default 2000) persisted to a JSON file; survives server restarts. |
| File path | `DETECTIONS_FILE`, default `<dir of MEDIAMTX_CONFIG>/detections.json` → `/runtime/detections.json` in the container (rw-mounted; no compose change). Best-effort. |
| API | `GET /api/detections?limit=N` (authed) → newest-first events. |
| UI | A **📜 Журнал** top-bar button → modal table (time · scanner · band · freq/channel · class · SNR · appeared/gone). |

## 3. Architecture & data flow

```
Pi scan ──fpv/<id>/detection (retained, per cycle)──▶ mosquitto
                                                          │ (server in wg-easy netns → mqtt://127.0.0.1:1883, sub creds)
                                                          ▼
server.js mqtt client ─▶ journal.ingest(scannerId, payload)
   DetectionJournal: diff vs prev per scanner → appeared/gone events → ring (last 2000) → detections.json
        │  first message per scanner = baseline (no events) — avoids a startup burst
        ▼
GET /api/detections?limit=N  (authed)  ─▶  dashboard 📜 modal table (newest first)
```

The browser is unchanged except for the new journal button/modal; it does not feed the journal (the
server does, always-on). The Pi and the detection contract are unchanged.

## 4. Components / deliverables

```
lib/detection-journal.js         (new: diffDetections (pure) + DetectionJournal class)
test/detection-journal.test.js   (new)
package.json                     (change: + "mqtt" dependency)
dashboard/server.js              (change: build journal + mqtt subscribe in start(); GET /api/detections in createApp)
dashboard/public/index.html      (change: 📜 Журнал top-bar button + reuse the form-modal)
dashboard/public/app.js          (change: openJournal — fetch + render the table)
dashboard/public/styles.css      (change: journal table styling, if needed)
```

### 4.1 `lib/detection-journal.js`
- `detectionKey(d)` — mirror of `alert.js` (band:channel, else band:round5(center_mhz)).
- `diffDetections(prevByKey, scannerId, payload, isBaseline) -> { events, current }` — pure. `current`
  is a `Map(key -> detection)`. When not baseline: keys in current not in prev → `appeared`; keys in
  prev not in current → `gone` (using the prev detection's fields). Event shape:
  `{ ts, scanner_id, event, band, center_mhz, channel, class, snr_db, power_dbm }` (`ts` = payload.ts).
- `class DetectionJournal({ file, max })`:
  - `ingest(scannerId, payload)` — first call per scanner is baseline (no events); diffs, updates the
    per-scanner `Map`, appends events to the ring (trim to `max`), persists (best-effort). Returns the events.
  - `events(limit=200)` — newest-first slice.
  - `_load()` on construct (best-effort JSON array, trimmed to `max`); `_persist()` writes the ring (best-effort).

### 4.2 `server.js`
- `createApp`: add `app.get('/api/detections', requireAuth, …)` → `config.journal ? config.journal.events(limit) : []`, `limit = clamp(req.query.limit, 1, 2000, default 200)`.
- `start()`: build `journal = new DetectionJournal({ file: env.DETECTIONS_FILE || join(dirname(mediamtxConfig), 'detections.json'), max: Number(env.DETECTIONS_MAX||2000) })`; set `config.journal = journal`; then a guarded MQTT client:
  `import('mqtt')` → `mqtt.connect(env.MQTT_TCP_URL || 'mqtt://127.0.0.1:1883', { username: config.mqtt.user, password: config.mqtt.pass, reconnectPeriod: 5000 })`; on `connect` → `subscribe('fpv/+/detection')`; on `message` → match `^fpv/([^/]+)/detection$`, `JSON.parse` (guarded), `journal.ingest(id, payload)` (guarded); on `error` → log + continue. A failure to init MQTT logs and the server still serves (journal just doesn't update).

### 4.3 Dashboard
- `index.html`: a `<button id="journal-btn">📜 Журнал</button>` next to the existing top-bar buttons.
- `app.js`: `openJournal()` → `fetch('/api/detections?limit=200')` → build a table (time via
  `new Date(ev.ts*1000)`, scanner, band, `fmtFreq` + channel, class colour, SNR, appeared/gone badge)
  → `showModal(html)` with a refresh button (re-fetch). Wire `#journal-btn` click.
- `styles.css`: reuse `.scan-table` styling; add an `appeared`/`gone` badge colour if needed.

## 5. MQTT / API contract

- Consumes the existing `fpv/<id>/detection` (`{scanner_id, ts, detections:[…], occupancy}`) — unchanged.
- `GET /api/detections?limit=N` → `[{ ts, scanner_id, event:"appeared"|"gone", band, center_mhz, channel, class, snr_db, power_dbm }, …]` newest-first.

## 6. Error handling & resilience

- The MQTT client, JSON parse, and `ingest` are each guarded — a malformed message or a broker outage
  never crashes the server; the journal simply stops updating until reconnect (paho/mqtt.js auto-reconnect).
- `_persist`/`_load` are best-effort (a missing/unwritable file degrades to in-memory).
- The first detection message per scanner (incl. the retained one on (re)subscribe) is a **baseline**
  with no events, so a server restart doesn't re-log already-present transmitters.
- `GET /api/detections` returns `[]` if the journal is absent (e.g. tests construct the app without one).

## 7. Testing / verification

- **`test/detection-journal.test.js`** (`node --test`): `diffDetections` — baseline yields no events;
  a new key → one `appeared`; a removed key → one `gone` (with the prior fields); no change → none.
  `DetectionJournal` — ingest sequence produces the right events; `events()` is newest-first and
  capped at `max`; persist + reload via a tmp file round-trips.
- **`node --check`** on changed browser files; `npm test` green.
- **Live (after deploy):** with the scanner running, transmitters appearing/disappearing add rows; the
  📜 modal lists them newest-first; the file `/runtime/detections.json` grows and survives a dashboard
  container rebuild.

## 8. Out of scope

- Any Pi/scan-service or detection-contract change.
- Journal search/filter/CSV export, and a clear/rotate action (v1 is a capped ring).
- Logging spectrum/video/rxtune history — detections only.
