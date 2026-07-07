# SDR Live-View Stream (ручний режим перегляду) — Design

**Date:** 2026-07-05
**Status:** Approved design, ready to implement (own branch off `main`).
**Target:** scanner Pi 5 (`andriy@192.168.1.204`, `/opt`, `SCAN_ID=hackrf`, HackRF One) + dashboard.

## Goal
Operator-triggered **live demod streaming**: switch the scanner into a manual "view" mode at a chosen
frequency — the sweep pauses, the Pi demodulates the FM analog-video signal continuously and streams
it (grayscale H.264) to MediaMTX like a camera; a normal dashboard tile plays it via WHEP. Exiting
the mode (button or timeout) resumes the sweep.

## Decisions (from the operator)
- Entry paths (all three): **click on any band's spectrum canvas** (fills the frequency field; on
  5.8G the click ALSO keeps tuning the RX5808 as today), **▶ button per detection row** (starts
  immediately at that detection's `center_mhz`), **manual MHz field + ▶/■ buttons** in the scanner
  block.
- **Auto-timeout 10 min** (`VIEW_MAX_S`, default 600) — the scanner must not stay blind forever.
- Video path: **pseudo-camera** — register a `kind=camera` device `hackrf-view`; the Pi pushes RTSP
  with its generated creds; the dashboard tile works with zero new video infrastructure.
- Architecture: **approach A** — view mode lives inside the existing `fpv-scan` service (single
  owner of the HackRF), subprocess pipeline `hackrf_transfer → NumPy demod → ffmpeg → RTSP`.

## Command & state (MQTT — no broker/ACL changes)
The browser's `sub` user may write ONLY `fpv/+/rxcmd` (mosquitto ACL), so view commands reuse it:

- `fpv/<id>/rxcmd` ← `{view: "start", freq_mhz: <number>}` or `{view: "stop"}`.
  `MqttPublisher._on_message` routes: payload with a `view` key → `on_view_command(data)` (new
  callback); otherwise legacy `on_command(mode, channel)` (RX5808 path unchanged).
- `fpv/<id>/view` (retained, QoS 1) → `{active: bool, freq_mhz: number|null, ts, until_ts: number|null, error: string|null}`.
  Published on every state change: start, manual stop, timeout, pipeline error. `pub` may write
  `fpv/#` ✓; the browser reads it via the existing `fpv/#` subscription.

## Pi — view controller + streamer

### `agent/scan/view_controller.py` (new)
Thread-safe mode state consumed by the main loop:
- `set_command(data)` — called from the MQTT thread; validates `freq_mhz` (finite, 100–6000 MHz —
  the HackRF tuning range; otherwise ignored with a log) and stores the request.
- `pending() -> request|None` — checked by `main()` between scan cycles.
- `run_view(request)` — blocking: publishes `view {active:true, until_ts}`, runs the streamer until
  stop command / `VIEW_MAX_S` timeout / pipeline error, publishes `view {active:false, error?}`,
  calls `reset_hackrf()` (device reuse safety), returns → the sweep loop continues.
- While a view is active the scan loop does NOT run: no spectrum/detection/video publishes (the
  dashboard shows the stale spectrum + an explanatory view badge).

### `agent/video/stream_demod.py` (new; flat module like the rest of agent/video)
Continuous IQ → grayscale frames → ffmpeg:
- Capture: `hackrf_transfer -r - -f <Hz> -s VIEW_SAMPLE_RATE_HZ -l <lna> -g <vga> -a <amp>`
  (stdout pipe). A dedicated **reader thread always drains stdout** into a "latest chunk" slot,
  dropping backlog — the USB stream must never stall on a full pipe.
- Demod loop: take the latest ~0.5 s chunk → `iq_from_int8` → `fm_demod` + `lowpass` (reuse) →
  `detect_standard` once at start (`VIEW_STANDARD=auto|pal|ntsc`; on `auto`, fall back to `pal`
  if the gate rejects — the stream must still show *something* on noise) → `reconstruct_frames`
  (reuse) → resize each luma to fixed `VIEW_WIDTH×H` (H = 288 PAL / 240 NTSC; Pillow, already a
  Pi dep) → write raw gray bytes to ffmpeg stdin. Best-effort fps: slower demod ⇒ fewer frames,
  never a growing delay.
- Encode/push: `ffmpeg -f rawvideo -pix_fmt gray -s WxH -r <nominal> -i - -c:v libx264
  -preset ultrafast -tune zerolatency -pix_fmt yuv420p -f rtsp VIEW_PUSH_URL`.
- Any subprocess death or write error → clean shutdown of both subprocesses → error reported up.
- Pure/testable pieces: command builders (hackrf/ffmpeg argv), chunk→frames function, resize,
  standard fallback, pacing logic. Subprocess wiring kept thin.

### Config (env, parsed in `agent/video/vconfig.py` or scan `config.py` — follow existing pattern)
| env | default | meaning |
|---|---|---|
| `VIEW_ENABLED` | `0` | master switch (unit opt-in like other features) |
| `VIEW_PUSH_URL` | — (required when enabled) | `rtsp://hackrf-view:<pass>@10.8.0.1:8554/hackrf-view` |
| `VIEW_SAMPLE_RATE_HZ` | `8000000` | capture rate |
| `VIEW_MAX_S` | `600` | auto-return to sweep |
| `VIEW_WIDTH` | `480` | output frame width (H fixed by standard: 288 PAL / 240 NTSC) |
| `VIEW_STANDARD` | `auto` | `auto` tries detect, falls back to `pal` |
Gains reuse the scan `LNA/VGA/AMP` settings.

## Server — zero code changes
Register a camera device `hackrf-view` (name «SDR перегляд (hackrf)») via the dashboard ➕ —
MediaMTX path + publish creds are generated as for any camera. Put the resulting RTSP push URL into
the Pi's `fpv-scan` unit drop-in as `VIEW_PUSH_URL`. The dashboard tile (WHEP) then works as-is;
OFFLINE whenever the view is inactive.

## Dashboard UI
- `mqtt-scan.js`: subscribe `fpv/+/view` → `store[id].view`; add `publishView(id, payload)`
  (publishes to `fpv/<id>/rxcmd`).
- Scanner block (`spectrum.js`): new row «📺 SDR перегляд»: `<input>` МГц · ▶ (start at field value)
  · ■ (stop; enabled only when `view.active`) · badge «▶ NNNN МГц до HH:MM» while active
  (from `view.freq_mhz`/`until_ts`).
- Canvas click on ANY band canvas → fill the МГц field (existing 5.8G RX5808 tune-on-click stays).
- Detection table: per-row ▶ button → `publishView(id, {view:'start', freq_mhz: row.center_mhz})`.

## Testing
- Unit (pytest, no hardware — like the rest of agent/*): rxcmd routing (view vs rx5808 payloads);
  view-controller state machine with a fake clock (start → timeout → resume; stop; error);
  hackrf/ffmpeg argv builders; chunk→frames on synthetic IQ (`agent/video/synth.py`);
  fixed-size resize; standard fallback on noise.
- Node tests: `mqtt-scan.js` reducer for `fpv/+/view`; scanner-block HTML (view row, buttons state).
- **First implementation task: demod throughput benchmark on the Pi 5** (chunked pipeline at
  8 MS/s, measure ×realtime + achievable fps). Target ≥1× realtime; if the effective fps is
  below ~5, escalate to a SoapySDR in-process capture as a separate follow-up project.
- Live acceptance: start view from the dashboard at a strong 0.9G GSM carrier (today's antenna
  sees only noise on 5.8) — the `hackrf-view` tile shows the live demod output; stop/timeout
  resumes the sweep (spectrum updates return).

## Risks / notes
- **Antenna**: real 5.8 video needs the antenna fix ([[hackrf-detection-hardware]]); pipeline
  validation uses whatever strong carriers exist (GSM ~947 MHz → noise-picture with sync fallback).
- **CPU (Pi 5)**: benchmark first; best-effort fps design degrades gracefully.
- **No HW encoder on Pi 5** — software x264 ultrafast at SD grayscale is well within budget
  (camera nodes already run software x264).
- Sweep pauses during view are by design (operator's explicit choice + 10-min cap).
- bladeRF later: capture argv builder is a separate function — swapping the capture command is
  localized (out of scope now).

Spec pattern follows [[server-frame-archive]]/[[rx5808-autotune]]; deploy = Pi unit update (git pull
+ pip install if needed + restart `fpv-scan` — do NOT overwrite the hand-diverged unit file) +
dashboard surgical update.
