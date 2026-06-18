# Wire Analog Video Extraction into the Scan Loop — Design Spec

**Date:** 2026-06-18
**Author:** andriy@netlife.com.ua
**Status:** Approved (pending written-spec review)

## 1. Context & Goal

PR #6 added `agent/video/` — a standalone CLI (`iq_video.py`) that turns a HackRF IQ capture
into a luma PNG published to `fpv/<scanner_id>/video`. It is not yet invoked by anything: the
long-running scan service (`agent/scan/main.py`) sweeps, dwells, classifies, and publishes
detections, but never triggers video extraction.

This work **wires video extraction into the scan loop** so that whenever the scanner sees an
analog-classified candidate, it reconstructs a frame from the IQ it already captured and publishes
it over the live MQTT connection — with a per-channel cooldown so it doesn't spam the same frame
every cycle.

Current `run_cycle` (per band, per candidate up to a budget):
```python
iq = _get_iq(cfg, c)                      # dwell: 20 Msps, 2M samples (~0.1 s) -> complex array
feat = compute_features(iq, cfg.dwell_sample_rate_hz)
cls, conf = classify(feat, cfg.thresholds)   # "analog" | "digital" | "unknown"
detections.append(Detection(... signal_class=cls ...))
```
The candidate IQ is already in memory at classification time — no second capture is needed.

## 2. Confirmed decisions

| Decision | Value |
|---|---|
| Trigger | Only candidates with `cls == "analog"`. The `detect_standard` sync gate is the final filter (analog-but-not-video → `not_video`, nothing published). |
| Execution | **In-process** on the already-captured dwell IQ — no re-capture, no temp file, no subprocess. |
| Publish | Reuse the **live `MqttPublisher`** that `main()` already holds; add a `publish_video()` method (no new connection per frame). |
| Cadence | **Per-channel cooldown** (`emit_cooldown_s`, default 10 s) keyed by rounded center MHz. Throttles both re-publish and re-processing. |
| Capture params | Reuse the existing dwell (`cfg.dwell_sample_rate_hz` = 20 Msps, `dwell_num_samples` = 2M ≈ 0.1 s ≈ 2.5 PAL frames). No new dwell params. |
| Robustness | Video extraction is fully guarded; it must never crash or stall-fatally the scan/`reset_hackrf` loop. |
| Enable switch | `video_enabled` (`FPV_VIDEO_ENABLED`, default true) so the operator can disable extraction. |

## 3. Architecture & data flow

```
run_cycle (per analog candidate)
   iq (already captured)  ─┐
                           ▼
   emitter.maybe_emit(iq, fs=dwell_sample_rate_hz, center_mhz, now_ts)
       │  cooldown gate: now_ts - last[center] < emit_cooldown_s ?  ── yes ─▶ "cooldown" (skip)
       │  no ↓   record last[center] = now_ts
       ▼
   extract_frame(iq, fs, center_hz, vcfg)         [agent/video/pipeline.py]
       fm_demod ▸ lowpass ▸ detect_standard ▸ reconstruct_frames ▸ pick_sharpest ▸ normalize_luma
       → VideoFrame(standard|None, line_hz, sync_snr_db, luma|None)
       │  standard is None  ─▶ "not_video"   (no publish)
       │  luma is None      ─▶ "no_lines"    (no publish)
       ▼  luma present
   save_full_png(luma, <frames_dir>/<ts>_<mhz>.png)        (local, best-effort)
   publisher.publish_video(now_ts, center_mhz, standard, line_hz, sync_snr_db, thumbnail_b64(luma))
       → fpv/<scanner_id>/video  (QoS1, retained)  ── over the live connection
       → "published"
   (whole body wrapped in try/except → "error", never raises into run_cycle)
```

`extract_frame` is the shared IQ→luma core; both the standalone CLI and the in-loop emitter call it,
so the pipeline lives in exactly one place.

## 4. Components / deliverables

```
agent/video/pipeline.py        (new: VideoFrame + extract_frame — shared IQ->luma core)
agent/video/iq_video.py        (change: process() refactored to call extract_frame; CLI behavior unchanged)
agent/video/vconfig.py         (change: + video_enabled, + emit_cooldown_s)
agent/scan/publisher.py        (change: + self._t_video, + publish_video(); reuses build_video_payload + _publish)
agent/scan/video_emit.py       (new: VideoEmitter — scan<->video bridge, cooldown, local save, live publish)
agent/scan/main.py             (change: run_cycle gains emitter param + analog hook; main() lazily builds the emitter)
agent/video/tests/test_pipeline.py     (new)
agent/scan/tests/test_video_emit.py    (new)
agent/scan/tests/test_publisher.py     (change: + publish_video topic/qos/retain test)
agent/scan/tests/test_run_cycle.py     (change: inject a fake emitter; assert it fires on analog candidates only)
```

### 4.1 `agent/video/pipeline.py`
```python
@dataclass
class VideoFrame:
    standard: Optional[str]        # None => not_video
    line_hz: int
    sync_snr_db: float
    luma: Optional[np.ndarray]     # uint8 frame; None when not_video or empty reconstruction

def extract_frame(iq, fs, center_hz, vcfg, std="auto") -> VideoFrame
```
- `bb = lowpass(fm_demod(iq), fs, vcfg.lpf_cutoff_hz)`
- `res = detect_standard(bb, fs, forced=(None if std=="auto" else std), line_snr_db=vcfg.line_snr_db, harm_snr_db=vcfg.harm_snr_db)`
- `res.standard is None` → `VideoFrame(None, res.line_hz, res.sync_snr_db, None)`
- else reconstruct + `pick_sharpest`; if `frame.size == 0` → `VideoFrame(res.standard, res.line_hz, res.sync_snr_db, None)`
- else → `VideoFrame(res.standard, res.line_hz, res.sync_snr_db, normalize_luma(frame))`

### 4.2 `iq_video.py` refactor
`process()` becomes: `iq = load_iq(path); vf = extract_frame(iq, fs, center_hz, vcfg, std)`; then the
same not_video (exit 2) / no_lines (exit 1) / save+thumbnail+`publish_video_once` (exit 0/1) flow as
today, reading fields off `vf`. The `center_mhz`, exit codes, logging, and existing `test_cli.py`
behavior are unchanged.

### 4.3 `MqttPublisher.publish_video`
- `__init__`: add `self._t_video = f"fpv/{scanner_id}/video"`.
- `publish_video(self, ts, center_mhz, standard, line_hz, sync_snr_db, frame_png_b64)`:
  `self._publish(self._t_video, build_video_payload(self.scanner_id, ts, center_mhz, standard, line_hz, sync_snr_db, frame_png_b64), self.QOS_DETECTION)` — QoS1, retained (via existing `_publish`),
  guarded and a no-op when not connected (same as other publishes).

### 4.4 `agent/scan/video_emit.py` — `VideoEmitter`
- Imports the video package via a `sys.path` shim to `../video` (mirrors `agent/video/conftest.py`),
  done at module import (so a missing Pillow only breaks when video is actually enabled).
- `__init__(self, publisher, vcfg, cooldown_s)`: stores them + `self._last = {}`.
- `maybe_emit(self, iq, fs, center_mhz, now_ts) -> str`:
  1. `key = round(center_mhz, 1)`; if `key in self._last and now_ts - self._last[key] < self.cooldown_s`: return `"cooldown"`.
  2. `self._last[key] = now_ts` (throttle reprocessing regardless of outcome).
  3. `try:` `vf = extract_frame(iq, fs, center_mhz * 1e6, self.vcfg)`; `except Exception:` log, return `"error"`.
  4. `vf.standard is None` → `"not_video"`; `vf.luma is None` → `"no_lines"`.
  5. `save_full_png(vf.luma, os.path.join(vcfg.frames_dir, f"{int(now_ts)}_{int(round(center_mhz))}.png"))` (best-effort, guarded).
  6. `if self.publisher is not None: self.publisher.publish_video(now_ts, center_mhz, vf.standard, vf.line_hz, round(float(vf.sync_snr_db), 1), thumbnail_b64(vf.luma, vcfg.thumb_max_width))`.
  7. return `"published"`.

### 4.5 `agent/scan/vconfig`-equivalent fields
Add to `agent/video/vconfig.py` `VideoConfig`: `video_enabled: bool = True`
(`FPV_VIDEO_ENABLED`, falsey = "0"/"false"/"no"/"") and `emit_cooldown_s: float = 10.0`
(`FPV_EMIT_COOLDOWN_S`).

### 4.6 `main.py`
- `run_cycle(cfg, now_ts, publisher=None, emitter=None)`: inside the candidate loop, right after
  `detections.append(...)`, add:
  ```python
  if emitter is not None and cls == "analog":
      emitter.maybe_emit(iq, cfg.dwell_sample_rate_hz, c.center_mhz, now_ts)
  ```
- `main()`: after building `publisher`, if video is enabled, **lazily** import and construct the
  emitter (so a missing Pillow/video import can't take down the core scan service):
  ```python
  vcfg = load_video_config()        # imported lazily
  emitter = None
  if vcfg.video_enabled:
      try:
          from video_emit import VideoEmitter
          emitter = VideoEmitter(publisher, vcfg, vcfg.emit_cooldown_s)
      except Exception:
          LOG.exception("video emitter init failed; continuing without video")
  ```
  Pass `emitter` into `run_cycle`. (`load_video_config` import also via the `../video` shim — add the
  same `sys.path` insert near the top of `main.py`, guarded so import failure leaves `emitter=None`.)

## 5. Failure & resilience

- `maybe_emit` is fully guarded: extraction/render/publish errors are logged and swallowed; it
  returns a status string and never raises into `run_cycle`, so the sweep/dwell/`reset_hackrf`
  recovery loop is untouched.
- If the video import (or Pillow) is unavailable, `main()` logs once and runs with `emitter=None` —
  the core scan service (sweep, detect, classify, spectrum/detection publish, state file) is
  unaffected.
- `publish_video` is a no-op when the publisher isn't connected (same guard as the other publishes).
- The per-channel cooldown also bounds CPU: a given channel is re-processed at most once per
  `emit_cooldown_s`.

## 6. Performance

Extraction reuses the 2M-sample dwell (not a fresh 3.2M capture). On a Pi 4 one extraction is
expected ≈1 s (FFT-dominated; the standalone module measured 0.36 s for 3.2M on desktop). Processing
is synchronous in the loop, so N *newly-seen* analog channels in a single cycle cost ~N×1 s in
sequence. Acceptable: analog FPV channels are few and the cooldown keeps the steady state cheap. If
this ever becomes a bottleneck, move extraction to a worker thread/queue (out of scope now — YAGNI).

## 7. Testing / verification

- **`agent/video/tests/test_pipeline.py`:** `extract_frame` on synthetic PAL/NTSC IQ returns a
  `VideoFrame` with the right `standard` and a non-None uint8 `luma`; on pure noise returns
  `standard=None, luma=None`.
- **`agent/scan/tests/test_video_emit.py`:** with a fake publisher (records `publish_video` calls)
  and synthetic IQ: a PAL capture → `"published"` and one `publish_video` with topic-args; a second
  call within the window → `"cooldown"` and no second publish; pure noise → `"not_video"`, no
  publish; an `extract_frame` that raises (monkeypatched) → `"error"`, no crash, no publish.
- **`agent/scan/tests/test_publisher.py`:** `publish_video` uses `fpv/<id>/video`, QoS1, retain
  (fake client), and is a no-op when not connected.
- **`agent/scan/tests/test_run_cycle.py`:** inject a fake emitter; assert `maybe_emit` is called for
  each analog-classified candidate and **not** for digital/unknown (monkeypatch `classify` to a known
  sequence if the replay fixtures don't deterministically yield an analog detection).
- Run: `cd agent/scan && python -m pytest tests ../video/tests -q` (whole project green).
- **Ops verification (after deploy, §8 of the deploy step):** with the broker up and an analog video
  TX on a known channel, `mosquitto_sub -t 'fpv/#' -v` shows a retained `fpv/<id>/video`; `fpv/<id>/status`
  stays untouched; the scan loop keeps publishing spectrum/detection.

## 8. Deployment notes (handled in the deploy step)

The scan service venv must have Pillow (already in `agent/scan/requirements.txt`). `FPV_FRAMES_DIR`
(default `/var/lib/fpv/frames`) must be writable by the service user — add a `StateDirectory`/dir
create on the Pi. `FPV_VIDEO_ENABLED` and `FPV_EMIT_COOLDOWN_S` can be set in the unit/env file.

## 9. Out of scope

- Dashboard consumption of `fpv/<id>/video` (next follow-up).
- A worker-thread/async extraction path (only if synchronous proves too slow).
- New dwell parameters or a separate higher-rate video capture.
