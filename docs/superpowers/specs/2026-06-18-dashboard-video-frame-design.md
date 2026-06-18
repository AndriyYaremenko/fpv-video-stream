# Dashboard Consumption of `fpv/<id>/video` — Design Spec

**Date:** 2026-06-18
**Author:** andriy@netlife.com.ua
**Status:** Approved (pending written-spec review)

## 1. Context & Goal

The scan service now publishes a recovered analog-video luma frame to `fpv/<scanner_id>/video`
(QoS1, retained) — PR #6 + the scan-loop integration. Nothing consumes it: the dashboard's MQTT
reducer (`dashboard/public/mqtt-scan.js`) matches only `spectrum|detection|status` and subscribes to
`fpv/+/{spectrum,detection,status}`, so video frames are ignored.

This work makes the **dashboard display the latest recovered frame per scanner**: subscribe to the
video topic, reduce it into the store, render a thumbnail in each scanner block, and open it larger in
the existing modal on click.

The dashboard is plain ES modules (no build step). Pure reducer/helpers are unit-tested with
`node --test` (`test/*.test.js`); browser-only DOM code is validated with `node --check` + manual.

## 2. Confirmed decisions

| Decision | Value |
|---|---|
| What to show | **Latest frame only** per scanner (the topic is one retained frame per scanner; payload carries `center_mhz`). |
| Interaction | Inline thumbnail in the scanner block; **click opens it larger in the existing `#modal`** (swap the WHEP `<video>` for an `<img>`). |
| Placement | Right after the occupancy strip in `scannerBlock`, as its own row. |
| Caption | `standard · center MHz · SNR dB · HH:MM:SS` (from a pure, unit-tested `frameCaption()`). |
| Transmitted image | The **≤320px thumbnail** only (full-res PNG stays on the Pi). "Enlarge" upscales the thumbnail; the caption metadata is the value-add. A full-res fetch endpoint is out of scope. |

## 3. Architecture & data flow

```
mosquitto  fpv/<id>/video (retained)
   │  MqttScanClient.connect subscribes fpv/+/{spectrum,detection,status,video}
   ▼
reduce(store, 'fpv/<id>/video', payload)            [mqtt-scan.js — pure, unit-tested]
   store[<id>].video = { ts, center_mhz, standard, line_hz, sync_snr_db, frame_png_b64 }
   ▼  onChange -> renderScan()                       [app.js]
renderSpectrum(panel, scanners, store)               [spectrum.js]
   scannerBlock: head ▸ occupancy ▸ [VIDEO FRAME] ▸ band charts ▸ detection table
       <img class="scan-frame" src="data:image/png;base64,<b64>">  + frameCaption(video)
   ▼  click on .scan-frame  (delegated handler in app.js spectrumPanel listener)
openImageModal(src, caption)                         [app.js]
   hide #modal-video, show #modal-image(src), set #modal-caption, show #modal
```

## 4. Components / deliverables

```
dashboard/public/mqtt-scan.js   (change: regex + 'video'; ensure() video:null; reduce branch; subscribe fpv/+/video)
dashboard/public/spectrum.js    (change: frameCaption() helper + frame <img> section in scannerBlock)
dashboard/public/index.html     (change: add <img id="modal-image"> to #modal)
dashboard/public/app.js         (change: .scan-frame click -> openImageModal(); scannerInfoModal topic doc + video)
dashboard/public/styles.css     (change: .scan-frame, .scan-frame-cap, .modal-image)
test/mqtt-scan.test.js          (change: + 'reduce stores the video frame' test)
test/spectrum.test.js           (change: + frameCaption tests)
```

### 4.1 `mqtt-scan.js`
- `reduce` topic regex → `/^fpv\/([^/]+)\/(spectrum|detection|status|video)$/`.
- `ensure(store, id)` initial object gains `video: null`.
- New branch: `else if (kind === 'video') { s.video = { ts: data.ts || 0, center_mhz: data.center_mhz, standard: data.standard, line_hz: data.line_hz, sync_snr_db: data.sync_snr_db, frame_png_b64: data.frame_png_b64 || '' }; }`.
- `MqttScanClient.connect`: subscribe list gains `'fpv/+/video'`.

### 4.2 `spectrum.js`
- `frameCaption(video)` — pure: returns `"<standard> · <center> МГц · SNR <snr> dB · <HH:MM:SS>"`,
  tolerant of missing fields (uses `fmtFreq`, formats `ts*1000` as a local `HH:MM:SS`, omits SNR if
  null). Unit-tested.
- In `scannerBlock`, after `block.appendChild(occ)`: if `live && live.video && live.video.frame_png_b64`,
  append a `div.scan-frame-wrap` containing `<img class="scan-frame" alt="recovered frame"
  src="data:image/png;base64,<b64>">` and a `div.scan-frame-cap` with `frameCaption(live.video)`.
  (`escapeHtml` is not needed on the base64 — but the caption text is built from numbers/known
  enums; still pass it through the existing text path, not innerHTML injection of untrusted data.)

### 4.3 `index.html`
- Inside `#modal`, after `#modal-video`, add `<img id="modal-image" class="modal-image hidden" alt="recovered frame">`.

### 4.4 `app.js`
- In the `spectrumPanel` click listener, before/after the `button[data-act]` handling: if
  `e.target.closest('.scan-frame')`, read its `src` and the sibling caption text and call
  `openImageModal(src, caption)` (don't also trigger the management actions).
- `openImageModal(src, caption)`: if a `modalPlayer` is open, close it; hide `#modal-video`
  (`classList.add('hidden')` or `style.display='none'`), set `#modal-image` `src` + show it, set
  `#modal-caption`, show `#modal`. The close handler hides the modal and clears/hides the image, and
  re-shows the video element for the next camera open.
- `scannerInfoModal`: change the MQTT-topics cred row from
  `fpv/${device.id}/{spectrum,detection,status}` to `fpv/${device.id}/{spectrum,detection,status,video}`.

### 4.5 `styles.css`
- `.scan-frame-wrap { margin:.5rem 0; }`
- `.scan-frame { max-width:320px; width:100%; border:1px solid var(--line); border-radius:4px; cursor:pointer; display:block; image-rendering:auto; }`
- `.scan-frame-cap { color:#9aa4b2; font-size:.8rem; margin-top:.25rem; }`
- `.modal-image { max-width:92vw; max-height:82vh; background:#000; image-rendering:pixelated; }`
- `.modal-image.hidden { display:none; }`

## 5. Error handling & resilience

- `reduce` already guards malformed JSON / non-object payloads (returns the store unchanged); the
  video branch reuses that path. A missing `frame_png_b64` → no thumbnail rendered (block falls back
  to the existing spectrum/detection view).
- The `<img>` `src` is a self-produced base64 data URI; a corrupt image just fails to render (browser
  shows the `alt`) — no script impact.
- Opening the image modal must not leave a WHEP player running (close it first), and closing must
  restore `#modal-video` visibility so the next camera click works.

## 6. Testing / verification

- **`test/mqtt-scan.test.js`:** a `fpv/hackrf/video` message stores
  `store.hackrf.video` with `standard/center_mhz/line_hz/sync_snr_db/frame_png_b64`; an unknown kind
  still ignored; malformed JSON swallowed (existing tests stay green).
- **`test/spectrum.test.js`:** `frameCaption({standard:'PAL', center_mhz:5800, sync_snr_db:18.3, ts:...})`
  contains `PAL`, `5800`, `18.3`; tolerates missing `sync_snr_db`.
- **Browser files:** `node --check dashboard/public/{mqtt-scan,spectrum,app}.js` passes.
- Run: `npm test` (whole `node --test` suite green).
- **Manual (after deploy):** with a retained `fpv/<id>/video` on the broker, the scanner block shows
  the thumbnail + caption; clicking opens the modal; the spectrum/detection views are unchanged;
  camera tiles/WHEP still work.

## 7. Out of scope

- A full-resolution frame endpoint (fetch the on-Pi PNG) — only the thumbnail is transmitted.
- A per-channel filmstrip / history of recent frames (latest-only for now).
- Any change to the scan service or the MQTT contract (already shipped).
