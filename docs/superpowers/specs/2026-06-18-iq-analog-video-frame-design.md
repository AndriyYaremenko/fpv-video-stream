# Analog FPV Video — IQ → Frame → MQTT (`agent/video/`) — Design Spec

**Date:** 2026-06-18
**Author:** andriy@netlife.com.ua
**Status:** Approved (pending written-spec review)

## 1. Context & Goal

The HackRF scan service (`agent/scan/`) already sweeps the FPV bands, finds a suspicious narrow
stable carrier, returns its **center frequency**, and can capture raw IQ to a file via
`hackrf_transfer -r cap.iq -f <center_hz> -s <fs> -n <samples> -a 1 -l 32 -g 32`.

This project adds a **separate, one-shot module** on the Pi that takes such an IQ capture and:
1. loads the raw IQ,
2. confirms it is **analog FPV video** (FM-modulated composite PAL/NTSC; ~6–20 MHz wide in the
   1.2 / 3.3 / 5.8 GHz bands),
3. reconstructs a **monochrome (luma) frame**,
4. publishes the frame + metadata to the server over MQTT.

Only a **PNG (tens of KB)** crosses the WireGuard tunnel, never raw IQ (~MB). **Digital FPV
(OFDM: DJI/HDZero/Walksnail) is out of scope.**

The sweep and primary detection already exist — this module only receives a `center_freq` and a
file. It is invoked from the capture pipeline right after `hackrf_transfer` (wiring that invocation
into `main.py`'s scan loop is **out of scope** here; §10).

## 2. Confirmed decisions

| Decision | Value |
|---|---|
| MQTT topic | New kind **`fpv/<scanner_id>/video`** (QoS1, retained). Consistent with `fpv/<id>/{spectrum,detection,status}`; the dashboard reducer matches `fpv/+/<kind>` so a flat `fpv/detection` would be ignored and would collide with the RF-detection topic. |
| Payload | Includes **`scanner_id`** (the original spec's flat payload omitted it). |
| Config | **Reuse env + `config.py`** (`load_config()` for MQTT creds / `scanner_id`); per-capture params via **CLI flags**. **No YAML** (`--config` dropped). |
| Code home | New **`agent/video/`** package of flat modules; bootstraps `../scan` onto `sys.path` (same shim pattern as the existing `conftest.py`). |
| IQ read | **Reuse `iq_from_int8`** from `agent/scan/dweller.py`. |
| Publishing | **Reuse `MqttPublisher`** (`agent/scan/publisher.py`) — add a pure `build_video_payload(...)` + a one-shot `publish_video_once(...)` that **does not touch the `status`/presence topic or set an LWT**, so it never clobbers the long-running scan service's presence for the same `scanner_id`. |
| Runtime | Runs in the shared `agent/scan/.venv`; **add `Pillow`** to `agent/scan/requirements.txt`. |
| Color | **Luma only.** No color subcarrier decode (4.43/3.58 MHz). |

## 3. Architecture & data flow

```
hackrf_transfer ──▶ cap.iq (int8 I,Q,I,Q…)
        │
        ▼
iq_video.py (one-shot CLI, exit 0/1/2)
  load_iq ──▶ fm_demod ──▶ lowpass(~5 MHz) ──▶ detect_standard (GATE)
   reuse        angle(iq[1:]·conj(iq[:-1]))      FFT/Goertzel @ 15625 (PAL) / 15734 (NTSC)
   iq_from_int8 − median (de-carrier)            + 2nd harmonic; line ≥+10 dB, harm ≥+6 dB
                                                   │
                          no sync ────────────────┤──▶ status not_video, exit 2, publish NOTHING
                                                   │ sync found
                                                   ▼
  slice_lines ──▶ build_frame ──▶ pick_sharpest ──▶ render
  samples/line =   active lines →   Laplacian-var    normalize 2–98%, PIL 'L'
  fs/(lines·fps)   np.interp→720px  over ~5 frames    │
  PAL 625/25       2D luma array    (0.2 s @ 25 fps)  ├─▶ full PNG → /var/lib/fpv/frames/<ts>.png
  NTSC 525/30                                          └─▶ thumbnail ≤320px → base64
                                                          │
                                                          ▼
                                  publish_video_once ─MQTT QoS1 retained─▶ fpv/<scanner_id>/video
                                  (no status/LWT)         10.8.0.1:1883 (WG)
```

All stages are **vectorized numpy** — no per-sample Python loops. `fm_demod` is one complex multiply
+ `np.angle` over the array; `lowpass` is a cumsum moving average; `slice_lines` reshapes to
`(n_lines, samples_per_line)`; per-line resample is a batched `np.interp`. Target **< ~1 s on Pi 4**
for 3.2 M samples (16 Msps × 0.2 s). No redundant copies of the large array are retained.

## 4. Components / deliverables

```
agent/video/iqio.py        (new: load_iq — reuse iq_from_int8)
agent/video/demod.py       (new: fm_demod, lowpass)
agent/video/standard.py    (new: detect_standard → (standard, line_hz, sync_snr_db))
agent/video/frame.py       (new: slice_lines, build_frame, pick_sharpest, (opt) deinterlace)
agent/video/render.py      (new: normalize_luma, save_full_png, thumbnail_b64)
agent/video/vconfig.py     (new: VideoConfig + load_video_config — DSP knobs (env) + MQTT via reused Config)
agent/video/synth.py       (new: synthetic PAL/NTSC CVBS → FM → int8+noise, for tests)
agent/video/iq_video.py    (new: CLI orchestrator, exit codes, structured logging)
agent/video/conftest.py    (new: sys.path shim for ../scan + own dir, mirrors agent/scan/conftest.py)
agent/video/tests/         (new: unit test per pipeline function + one end-to-end on synthetic IQ)
agent/scan/publisher.py    (change: add build_video_payload + publish_video_once; no status/LWT)
agent/scan/requirements.txt(change: +Pillow)
```

### 4.1 `iqio.py`
- `load_iq(path) -> np.ndarray` — read bytes, delegate to `iq_from_int8` (complex64, normalized /128).

### 4.2 `demod.py`
- `fm_demod(iq) -> np.ndarray` — instantaneous frequency `np.angle(iq[1:] * np.conj(iq[:-1]))`, then
  subtract the **median** to remove the residual carrier offset. Returns real baseband at `fs`.
- `lowpass(x, fs, cutoff_hz) -> np.ndarray` — cumsum moving average with window `~fs/cutoff`
  (default cutoff ~5 MHz). Vectorized; preserves length (edge-padded).

### 4.3 `standard.py`
- `detect_standard(baseband, fs, forced=None) -> StdResult` where
  `StdResult = {standard: "PAL"|"NTSC"|None, line_hz: int, sync_snr_db: float, harm_snr_db: float}`.
  Measures power at the candidate line rates (PAL 15625, NTSC 15734) and their 2nd harmonics
  against a local spectral-noise floor (FFT or Goertzel on the baseband). **Gate:** line tone
  ≥ +10 dB and 2nd harmonic ≥ +6 dB over floor. `forced` (`pal`/`ntsc`) skips auto-selection but
  still measures SNR. No qualifying tone ⇒ `standard=None` (→ `not_video`).

### 4.4 `frame.py`
- `slice_lines(baseband, fs, lines, fps) -> np.ndarray` — find horizontal sync (lowest CVBS level),
  align, slice into rows; `samples_per_line = fs / (lines * fps)`.
- `build_frame(rows, width=720, blank_frac=0.18) -> np.ndarray` — drop the leading sync+blanking
  interval of each line (~`blank_frac` of its length), resample the remaining active portion to
  `width` px via batched `np.interp`, stack into a 2D luma array.
- `pick_sharpest(frames) -> np.ndarray` — choose the frame with the highest Laplacian variance.
- `deinterlace(frame) -> np.ndarray` — optional field recombination (off by default).

### 4.5 `render.py`
- `normalize_luma(arr, lo=2, hi=98) -> uint8 array` — percentile stretch to 0–255.
- `save_full_png(arr, path) -> None` — full-res PIL `L` PNG to `/var/lib/fpv/frames/<ts>.png`
  (`os.makedirs(..., exist_ok=True)`).
- `thumbnail_b64(arr, max_width=320) -> str` — downscaled `L` PNG, base64-encoded for MQTT.

### 4.6 `vconfig.py`
- `VideoConfig` dataclass: DSP/IO knobs with env overrides — `frames_dir` (`FPV_FRAMES_DIR`,
  default `/var/lib/fpv/frames`), `lpf_cutoff_hz`, `frame_width`, `thumb_max_width`,
  `line_snr_db=10`, `harm_snr_db=6`, default `fs`. MQTT host/port/user/pass + `scanner_id` come from
  the **reused** `load_config()` (`agent/scan/config.py`). `load_video_config(env)` builds it; CLI
  flags override per run.

### 4.7 `synth.py` (test fixture generator)
- `make_cvbs(standard, pattern, ...) -> real baseband` — synthetic composite frame: line sync +
  blanking + a test image (gradient / checkerboard).
- `fm_modulate(baseband, fs, deviation) -> complex IQ`, then `to_int8(iq, noise_db) -> bytes`
  (quantize + additive noise). Used to drive the pipeline end-to-end without hardware.

### 4.8 `iq_video.py` (CLI)
```
python3 iq_video.py --iq cap.iq --fs 16e6 --center 5800e6 [--std auto|pal|ntsc] [--frames N]
```
- `--std auto` (default) auto-selects PAL/NTSC by measured line rate; `--frames 1` + `--fs 10e6` is
  the **degraded mode** (Pi Zero 2: one 40 ms frame).
- **Exit codes:** `0` frame published · `2` `not_video` (no sync — nothing published) · `1` error,
  **or broker unreachable after the frame was built** (frame still saved locally, warning logged).
- **Structured logging** (`key=value`): `ts`, `center_mhz`, `standard`, `sync_snr_db`, `frame_path`,
  `mqtt_status`.

### 4.9 `publisher.py` additions
- `build_video_payload(scanner_id, ts, center_mhz, standard, line_hz, sync_snr_db, frame_png_b64)
  -> dict` — pure, unit-testable, emits the §5 contract.
- `publish_video_once(host, port, user, password, scanner_id, payload, keepalive=60,
  client_factory=None) -> bool` — connect (reusing `_default_client_factory`), publish to
  `fpv/<scanner_id>/video` QoS1 retain, `wait_for_publish()`, disconnect. **No `will_set`, no
  `status` publish.** Returns `True` on confirmed publish, `False` (no raise) if the broker is
  unreachable so the CLI can fall back to exit 1 + local-only.

## 5. MQTT contract

Topic `fpv/<scanner_id>/video`, **QoS 1, retain=true** (a fresh dashboard subscriber immediately
sees the last frame). Payload:

```json
{
  "scanner_id": "scan-01",
  "ts": 1718700000.0,
  "center_mhz": 5800.0,
  "standard": "PAL",
  "line_hz": 15625,
  "sync_snr_db": 18.3,
  "frame_png_b64": "<base64 PNG thumbnail, ≤320 px wide>"
}
```

## 6. Failure & resilience

- **Not video** (no sync tone): exit `2`, publish nothing, no PNG written.
- **Broker unreachable:** `publish_video_once` returns `False` — the full PNG is already on disk
  (`/var/lib/fpv/frames/<ts>.png`), a warning is logged, CLI exits `1`. The process never crashes.
- **Processing error** (bad/empty file, etc.): logged, exit `1`.
- The one-shot publisher never sets an LWT and never writes `fpv/<id>/status`, so it cannot disturb
  the scan service's retained presence for the same `scanner_id`.

## 7. Testing / verification

- **Automated (pytest, `agent/video/tests/`), no hardware:**
  - `synth.py` builds a synthetic PAL/NTSC CVBS → FM → int8 IQ at a chosen SNR.
  - `detect_standard` returns `PAL`/`NTSC` at SNR ~20 dB and `None`/`not_video` on pure noise.
  - The reconstructed frame **correlates** with the source test image above a threshold.
  - `--std auto` distinguishes PAL vs NTSC on the respective synthetic signals.
  - `build_video_payload` + `publish_video_once` (fake paho client) produce valid JSON with a
    decodable PNG, correct topic/QoS/retain, and **no** `status` publish / LWT.
  - Unit test per pipeline function (`load_iq`, `fm_demod`, `detect_standard`, `slice_lines`,
    `build_frame`, `pick_sharpest`) + **one end-to-end** test on synthetic IQ.
  - Run: `cd agent/scan && python -m pytest ../video/tests -q` (shared venv, `../scan` on path).
- **Acceptance criteria** (from the brief):
  - Synthetic PAL IQ @ SNR ~20 dB → recognizable frame published to `fpv/<id>/video`.
  - Pure noise → `not_video` (exit 2), nothing published.
  - Pi 4 full cycle (read → detect → frame → publish) within ~1 s for 16 Msps / 0.2 s.
  - Broker down → process survives, frame saved locally, warning logged.
  - `--std auto` correctly separates PAL/NTSC.
  - pytest green; README with run + pipeline-integration example.

## 8. Deployment

On the Pi: `pip install -r agent/scan/requirements.txt` (now includes `Pillow`) into the shared
`agent/scan/.venv`. The MQTT creds (`MQTT_PUB_USER`/`MQTT_PUB_PASS`) and `SCAN_MQTT_HOST` already
live in `/etc/fpv-scan.env` for the scan unit; the CLI reuses them via `load_config()`. Ensure
`/var/lib/fpv/frames/` is writable by the service user. The broker (SP-A) must be reachable over WG.
No systemd unit is added — the CLI is invoked by the capture pipeline (§10).

## 9. Security

- MQTT `:1883` is reached only over WireGuard (`10.8.0.1`), not host-published. The `pub` user is
  publish-only on `fpv/#` (SP-A ACL); the new `fpv/<id>/video` topic is covered by that wildcard.
- No raw IQ leaves the Pi — only a luma PNG thumbnail (privacy + bandwidth).
- No TLS on `:1883` (WG already private — same decision as SP-A).

## 10. Out of scope

- Wiring the CLI into `main.py`'s scan loop (who writes `cap.iq` and shells out to `iq_video.py`
  per candidate) — a later integration step; this module is the standalone consumer of a file.
- Dashboard consumption of `fpv/<id>/video` (the reducer/subscriber + UI panel that renders the
  PNG) — a separate dashboard task.
- Color decode (4.43/3.58 MHz subcarrier), digital FPV / OFDM, the sweep + primary detection.

## 11. Assumptions

- The broker (SP-A) is reachable at `10.8.0.1:1883` over WG with the `pub` credentials.
- IQ files are HackRF `int8` interleaved `I,Q,I,Q…` (matches `iq_from_int8`).
- `Pillow` installs on the Pi's Python (the scan venv).
- `cfg.scanner_id` on the unit is the deployed id (e.g. `scan-01`), so the topic is
  `fpv/<that id>/video`.
