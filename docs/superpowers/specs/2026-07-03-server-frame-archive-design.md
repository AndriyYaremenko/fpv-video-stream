# Server-side Frame Archive + History Browser — Design

**Date:** 2026-07-03
**Status:** Approved design, ready to implement (own branch off `main`).

## Goal
Archive demodulated analog-video frames on the **server** (dashboard host) and let the operator
browse the detection history together with the demodulated frames. Today frames are published to
`fpv/<id>/video` (retained → only the latest) and the full-res PNGs live only on the Pi.

## Decisions (from the operator)
- **Archive ALL frames** (no SNR filter) — noise frames (e.g. the 950 MHz GSM false-lock) are
  accepted and controlled by retention, not filtering.
- **Retention: last 7 days** — prune anything older on a timer.
- **UI: a separate frames gallery** (new "🖼️ Кадри" modal), not folded into the 📜 detection journal.

## Architecture
`dashboard/server.js` already runs an MQTT client (subscribes `fpv/+/detection` for the journal at
`mqtt://127.0.0.1:1883`) and an Express app. Extend it:

1. **Archive** — also subscribe to `fpv/+/video`. On each message (`{scanner_id, ts, center_mhz,
   standard, line_hz, sync_snr_db, frame_png_b64}`): decode the base64 PNG, write it to
   `runtime/frames/<scanner_id>/<ts>_<centerMHz>.png`, and append a metadata record to an in-memory
   index (persisted to `runtime/frames-index.json`, like `detections.json`). Guard: skip malformed
   payloads; throttle warnings (mirror the journal's `lastWarn` pattern).
2. **Retention** — a periodic sweep (e.g. every 30 min) deletes frame files + index entries with
   `ts` older than 7 days (env `FRAMES_RETENTION_DAYS`, default 7). Bounds disk and prunes noise.
3. **API**:
   - `GET /api/frames?scanner=&since=&limit=` → JSON list of `{id, scanner_id, ts, center_mhz,
     standard, sync_snr_db, url}` newest-first.
   - `GET /api/frames/:id` → the PNG file (id = `<scanner>/<ts>_<center>`), 404 if pruned.
4. **Dashboard UI** (`dashboard/public/`): a "🖼️ Кадри" button → modal with a thumbnail grid
   (each tile: frame + time, center MHz, scanner, sync SNR); click a tile → full-res. Sort
   newest-first; simple scanner filter. Reuse the existing modal/styling of the 📜 journal.

## Components
| File | Change |
|---|---|
| `dashboard/server.js` | subscribe `fpv/+/video`; `saveFrame()` (decode+write+index); retention timer; `GET /api/frames`, `GET /api/frames/:id`. |
| `dashboard/lib/frame-archive.js` (new) | pure-ish archive: `ingest(scannerId, payload)`, `list(opts)`, `prune(now, maxAgeMs)`, `_persist/_load` (mirror `lib/detection-journal.js`). |
| `dashboard/public/app.js` + `index.html` | "🖼️ Кадри" button + gallery modal + full-res view. |
| compose / `.env` | mount a writable `runtime/frames` volume; `FRAMES_RETENTION_DAYS`. |

## Data flow
Pi demod → MQTT `fpv/<id>/video` → server MQTT client → `frame-archive.ingest` → PNG on disk +
index → `/api/frames` → dashboard gallery. Retention timer prunes >7 days.

## Testing
- Unit (Node test runner, like the journal): `frame-archive` ingest writes a decoded PNG + index
  entry; `list` returns newest-first with metadata; `prune` drops >maxAge and removes files;
  malformed payload is skipped.
- Integration: publish a synthetic `fpv/test/video` to the broker, assert `/api/frames` lists it
  and `/api/frames/:id` serves the PNG bytes.

## Risks / notes
- **Disk**: ~400 KB/frame; with archive-all + noise, retention (7 d) is what bounds it — verify the
  prune actually runs and deletes files, not just index entries.
- Base64 PNG over MQTT is the thumbnail (≤320 px per the emitter), not full-res — the gallery shows
  the thumbnail. If full-res history is wanted later, the Pi would have to ship full PNGs (bigger).
- Frame↔detection correlation (time+center+scanner) is loose; a tight join is out of scope here
  (the operator chose a standalone gallery).

Spec pattern follows [[detection-journal]]; deploy via the documented surgical dashboard update.
