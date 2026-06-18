# Wire Analog Video Extraction into the Scan Loop — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the scan loop classifies a candidate as analog, reconstruct a luma frame from the IQ it already captured and publish it to `fpv/<scanner_id>/video` over the live MQTT connection, throttled by a per-channel cooldown.

**Architecture:** Extract the video IQ→luma core into `agent/video/pipeline.py` (`extract_frame`), reused by both the standalone CLI and a new `VideoEmitter` bridge in `agent/scan/`. `MqttPublisher` gains a `publish_video()` method; `run_cycle` gains an `emitter` hook that fires only on analog candidates; `main()` lazily builds the emitter so missing video deps can't break the core scan service.

**Tech Stack:** Python 3, numpy, Pillow, paho-mqtt, pytest. Shared `agent/scan/.venv`. Test interpreter: `agent/scan/.venv/Scripts/python.exe` (Windows). Run tests from `agent/scan`.

**Reference spec:** `docs/superpowers/specs/2026-06-18-video-scan-integration-design.md`

---

### Task 1: `pipeline.extract_frame` — shared IQ→luma core

**Files:**
- Create: `agent/video/pipeline.py`
- Test: `agent/video/tests/test_pipeline.py`

- [ ] **Step 1: Write the failing test** (`agent/video/tests/test_pipeline.py`)

```python
import numpy as np

from pipeline import extract_frame, VideoFrame
from vconfig import load_video_config
from synth import make_cvbs, fm_modulate


def _gradient(h=120, w=120):
    return np.tile(np.linspace(0.0, 1.0, h)[:, None], (1, w))


def _pal_iq(fs):
    bb = make_cvbs("PAL", _gradient(), fs, frames=2)
    return fm_modulate(bb, fs, deviation_hz=2_000_000.0)


def test_extract_frame_returns_luma_for_pal():
    fs = 8_000_000.0
    vcfg = load_video_config(env={})
    vf = extract_frame(_pal_iq(fs), fs, 5_800_000_000.0, vcfg)
    assert isinstance(vf, VideoFrame)
    assert vf.standard == "PAL"
    assert vf.line_hz == 15625
    assert vf.luma is not None
    assert vf.luma.dtype == np.uint8 and vf.luma.ndim == 2


def test_extract_frame_noise_is_not_video():
    fs = 8_000_000.0
    vcfg = load_video_config(env={})
    rng = np.random.default_rng(0)
    iq = rng.normal(0, 1, 200_000) + 1j * rng.normal(0, 1, 200_000)
    vf = extract_frame(iq, fs, 5_800_000_000.0, vcfg)
    assert vf.standard is None
    assert vf.luma is None
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd agent/scan && ./.venv/Scripts/python.exe -m pytest ../video/tests/test_pipeline.py -q`
Expected: FAIL (No module named 'pipeline').

- [ ] **Step 3: Implement** (`agent/video/pipeline.py`)

```python
from dataclasses import dataclass
from typing import Optional

import numpy as np

from demod import fm_demod, lowpass
from standard import detect_standard
from frame import reconstruct_frames, pick_sharpest
from render import normalize_luma


@dataclass
class VideoFrame:
    standard: Optional[str]        # None => not_video
    line_hz: int
    sync_snr_db: float
    luma: Optional[np.ndarray]     # uint8 2D frame; None when not_video or empty reconstruction


def extract_frame(iq, fs, center_hz, vcfg, std="auto"):
    """IQ -> luma core shared by the CLI and the in-loop emitter.

    Returns a VideoFrame. standard=None means the sync gate rejected it (not_video);
    luma=None with a non-None standard means detected but too few lines to render.
    center_hz is accepted for symmetry with callers but not needed for reconstruction.
    """
    bb = lowpass(fm_demod(iq), fs, vcfg.lpf_cutoff_hz)
    forced = None if std == "auto" else std
    res = detect_standard(bb, fs, forced=forced,
                          line_snr_db=vcfg.line_snr_db, harm_snr_db=vcfg.harm_snr_db)
    if res.standard is None:
        return VideoFrame(None, res.line_hz, res.sync_snr_db, None)
    frame = pick_sharpest(
        reconstruct_frames(bb, fs, res.standard, vcfg.frame_width, vcfg.blank_frac)
    )
    if frame.size == 0:
        return VideoFrame(res.standard, res.line_hz, res.sync_snr_db, None)
    return VideoFrame(res.standard, res.line_hz, res.sync_snr_db, normalize_luma(frame))
```

- [ ] **Step 4: Run it to confirm it passes**

Run: `cd agent/scan && ./.venv/Scripts/python.exe -m pytest ../video/tests/test_pipeline.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add agent/video/pipeline.py agent/video/tests/test_pipeline.py
git commit -m "feat(video): extract_frame — shared IQ->luma pipeline core"
```

---

### Task 2: Refactor `iq_video.process()` to use `extract_frame`

**Files:**
- Modify: `agent/video/iq_video.py`
- Modify: `agent/video/tests/test_cli.py` (rewrite the empty-frame test for the new seam)

- [ ] **Step 1: Update the empty-frame test** in `agent/video/tests/test_cli.py`

Replace the existing `test_empty_reconstruction_errors_without_crashing` function with this version (it now monkeypatches the `extract_frame` seam instead of `reconstruct_frames`):

```python
def test_empty_reconstruction_errors_without_crashing(monkeypatch, tmp_path):
    # Sync gate passes but reconstruction yields no lines -> clean exit 1, no publish/crash.
    from pipeline import VideoFrame
    fs = 8_000_000.0
    iq_path = _write_iq(tmp_path, "PAL", fs)
    published = {"called": False}
    monkeypatch.setattr(iq_video, "extract_frame",
                        lambda *a, **k: VideoFrame("PAL", 15625, 20.0, None))
    monkeypatch.setattr(iq_video, "publish_video_once",
                        lambda *a, **k: published.__setitem__("called", True) or True)
    monkeypatch.setenv("FPV_FRAMES_DIR", str(tmp_path / "frames"))
    code = iq_video.main(["--iq", iq_path, "--fs", str(fs), "--center", "5800e6"])
    assert code == 1
    assert published["called"] is False                       # nothing published
    assert not os.path.isdir(str(tmp_path / "frames"))        # nothing saved
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd agent/scan && ./.venv/Scripts/python.exe -m pytest ../video/tests/test_cli.py::test_empty_reconstruction_errors_without_crashing -q`
Expected: FAIL (`iq_video` has no attribute `extract_frame` yet, or the old code path saves a frame).

- [ ] **Step 3: Refactor `agent/video/iq_video.py`**

Replace the import block (the lines importing demod/standard/frame/render pipeline pieces) and the `process` function. The new import block (lines after the `sys.path.insert`) becomes:

```python
from config import load_config                              # noqa: E402  (reused scan config)
from publisher import build_video_payload, publish_video_once  # noqa: E402
from iqio import load_iq                                    # noqa: E402
from pipeline import extract_frame                          # noqa: E402
from render import save_full_png, thumbnail_b64             # noqa: E402
from vconfig import load_video_config                       # noqa: E402
```

And replace the whole `process` function with:

```python
def process(iq_path, fs, center_hz, std, vcfg, scfg, now_ts):
    iq = load_iq(iq_path)
    vf = extract_frame(iq, fs, center_hz, vcfg, std)
    center_mhz = center_hz / 1e6
    if vf.standard is None:
        LOG.info("status=not_video center_mhz=%.3f sync_snr_db=%.1f",
                 center_mhz, vf.sync_snr_db)
        return EXIT_NOT_VIDEO
    if vf.luma is None:
        LOG.warning("status=error center_mhz=%.3f standard=%s reason=no_lines_reconstructed",
                    center_mhz, vf.standard)
        return EXIT_ERROR

    frame_path = os.path.join(vcfg.frames_dir, f"{now_ts}.png")
    save_full_png(vf.luma, frame_path)
    thumb = thumbnail_b64(vf.luma, vcfg.thumb_max_width)

    payload = build_video_payload(
        scfg.scanner_id, float(now_ts), center_mhz, vf.standard, vf.line_hz,
        round(float(vf.sync_snr_db), 1), thumb,
    )
    ok = publish_video_once(
        scfg.mqtt_host, scfg.mqtt_port, scfg.mqtt_user, scfg.mqtt_pass,
        scfg.scanner_id, payload, scfg.mqtt_keepalive,
    )
    LOG.info("status=%s center_mhz=%.3f standard=%s sync_snr_db=%.1f frame_path=%s mqtt=%s",
             "published" if ok else "local_only", center_mhz, vf.standard,
             vf.sync_snr_db, frame_path, ok)
    return EXIT_OK if ok else EXIT_ERROR
```

Leave `main()`, the exit-code constants, and the `sys.path.insert` line unchanged.

- [ ] **Step 4: Run the full CLI suite**

Run: `cd agent/scan && ./.venv/Scripts/python.exe -m pytest ../video/tests/test_cli.py -q`
Expected: PASS (5 passed — the 4 original behaviors + the rewritten empty-frame test).

- [ ] **Step 5: Commit**

```bash
git add agent/video/iq_video.py agent/video/tests/test_cli.py
git commit -m "refactor(video): iq_video uses shared extract_frame core"
```

---

### Task 3: `VideoConfig` — `video_enabled` + `emit_cooldown_s`

**Files:**
- Modify: `agent/video/vconfig.py`
- Modify: `agent/video/tests/test_vconfig.py`

- [ ] **Step 1: Add failing assertions** — append to `agent/video/tests/test_vconfig.py`:

```python
def test_video_emit_defaults():
    c = load_video_config(env={})
    assert c.video_enabled is True
    assert c.emit_cooldown_s == 10.0


def test_video_emit_env_overrides():
    c = load_video_config(env={"FPV_VIDEO_ENABLED": "0", "FPV_EMIT_COOLDOWN_S": "30"})
    assert c.video_enabled is False
    assert c.emit_cooldown_s == 30.0
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd agent/scan && ./.venv/Scripts/python.exe -m pytest ../video/tests/test_vconfig.py -q`
Expected: FAIL (AttributeError: 'VideoConfig' object has no attribute 'video_enabled').

- [ ] **Step 3: Implement** — in `agent/video/vconfig.py`.

Add two fields to the `VideoConfig` dataclass (after `default_fs`):

```python
    video_enabled: bool = True
    emit_cooldown_s: float = 10.0
```

And add to `load_video_config`, just before `return c`:

```python
    if "FPV_VIDEO_ENABLED" in env:
        c.video_enabled = env["FPV_VIDEO_ENABLED"].strip().lower() not in ("0", "false", "no", "")
    if "FPV_EMIT_COOLDOWN_S" in env:
        c.emit_cooldown_s = float(env["FPV_EMIT_COOLDOWN_S"])
```

- [ ] **Step 4: Run it to confirm it passes**

Run: `cd agent/scan && ./.venv/Scripts/python.exe -m pytest ../video/tests/test_vconfig.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add agent/video/vconfig.py agent/video/tests/test_vconfig.py
git commit -m "feat(video): VideoConfig video_enabled + emit_cooldown_s"
```

---

### Task 4: `MqttPublisher.publish_video`

**Files:**
- Modify: `agent/scan/publisher.py`
- Modify: `agent/scan/tests/test_publisher.py`

- [ ] **Step 1: Write the failing test** — append to `agent/scan/tests/test_publisher.py`:

```python
def test_publish_video_topic_qos_retain():
    fake = FakeClient()
    p = _pub(fake); p.connect(ts=1)
    p.publish_video(400, 5800.0, "PAL", 15625, 18.3, "QUJD")
    msg = [m for m in fake.published if m[0] == "fpv/hackrf/video"][-1]
    topic, payload, qos, retain = msg
    assert qos == 1 and retain is True
    body = json.loads(payload)
    assert body["scanner_id"] == "hackrf"
    assert body["ts"] == 400 and body["center_mhz"] == 5800.0
    assert body["standard"] == "PAL" and body["line_hz"] == 15625
    assert body["sync_snr_db"] == 18.3 and body["frame_png_b64"] == "QUJD"


def test_publish_video_is_noop_when_not_connected():
    p = publisher.MqttPublisher("h", 1, "u", "p", "hackrf")     # never connect()
    p.publish_video(1, 5800.0, "PAL", 15625, 10.0, "x")        # must not raise
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd agent/scan && ./.venv/Scripts/python.exe -m pytest tests/test_publisher.py -q`
Expected: FAIL (AttributeError: 'MqttPublisher' object has no attribute 'publish_video').

- [ ] **Step 3: Implement** — in `agent/scan/publisher.py`, inside `MqttPublisher`.

In `__init__`, add a video topic next to the other `self._t_*` assignments:

```python
        self._t_video = f"fpv/{scanner_id}/video"
```

Add this method (e.g. right after `publish_detection`):

```python
    def publish_video(self, ts, center_mhz, standard, line_hz, sync_snr_db, frame_png_b64):
        self._publish(
            self._t_video,
            build_video_payload(self.scanner_id, ts, center_mhz, standard, line_hz,
                                sync_snr_db, frame_png_b64),
            self.QOS_DETECTION,
        )
```

(`build_video_payload` is already defined at module level in this file; `_publish` already
uses `retain=True` and guards against not-connected / client errors.)

- [ ] **Step 4: Run it to confirm it passes**

Run: `cd agent/scan && ./.venv/Scripts/python.exe -m pytest tests/test_publisher.py -q`
Expected: PASS (existing + 2 new).

- [ ] **Step 5: Commit**

```bash
git add agent/scan/publisher.py agent/scan/tests/test_publisher.py
git commit -m "feat(video): MqttPublisher.publish_video over the live connection"
```

---

### Task 5: `VideoEmitter` — scan↔video bridge with cooldown

**Files:**
- Create: `agent/scan/video_emit.py`
- Test: `agent/scan/tests/test_video_emit.py`

- [ ] **Step 1: Write the failing test** (`agent/scan/tests/test_video_emit.py`)

```python
import numpy as np

import video_emit                                   # sets sys.path to include ../video
from vconfig import VideoConfig
from synth import make_cvbs, fm_modulate


def _gradient(h=120, w=120):
    return np.tile(np.linspace(0.0, 1.0, h)[:, None], (1, w))


def _pal_iq(fs):
    bb = make_cvbs("PAL", _gradient(), fs, frames=2)
    return fm_modulate(bb, fs, deviation_hz=2_000_000.0)


class _FakePub:
    def __init__(self):
        self.videos = []     # (ts, center_mhz, standard, line_hz, sync_snr_db, b64)

    def publish_video(self, ts, center_mhz, standard, line_hz, sync_snr_db, frame_png_b64):
        self.videos.append((ts, center_mhz, standard, line_hz, sync_snr_db, frame_png_b64))


def _emitter(tmp_path, pub, cooldown_s=100.0):
    vcfg = VideoConfig(frames_dir=str(tmp_path / "frames"))
    return video_emit.VideoEmitter(pub, vcfg, cooldown_s)


def test_publishes_pal_frame(tmp_path):
    fs = 8_000_000.0
    pub = _FakePub()
    em = _emitter(tmp_path, pub)
    status = em.maybe_emit(_pal_iq(fs), fs, 5800.0, now_ts=1000)
    assert status == "published"
    assert len(pub.videos) == 1
    ts, center_mhz, standard, line_hz, snr, b64 = pub.videos[0]
    assert center_mhz == 5800.0 and standard == "PAL" and line_hz == 15625
    assert isinstance(b64, str) and b64


def test_cooldown_suppresses_second_emit(tmp_path):
    fs = 8_000_000.0
    pub = _FakePub()
    em = _emitter(tmp_path, pub, cooldown_s=100.0)
    iq = _pal_iq(fs)
    assert em.maybe_emit(iq, fs, 5800.0, now_ts=1000) == "published"
    assert em.maybe_emit(iq, fs, 5800.0, now_ts=1050) == "cooldown"   # within window
    assert len(pub.videos) == 1                                       # no second publish


def test_noise_is_not_video_no_publish(tmp_path):
    fs = 8_000_000.0
    pub = _FakePub()
    em = _emitter(tmp_path, pub)
    rng = np.random.default_rng(2)
    iq = rng.normal(0, 1, 200_000) + 1j * rng.normal(0, 1, 200_000)
    assert em.maybe_emit(iq, fs, 5800.0, now_ts=1000) == "not_video"
    assert pub.videos == []


def test_extract_error_is_swallowed(tmp_path, monkeypatch):
    fs = 8_000_000.0
    pub = _FakePub()
    em = _emitter(tmp_path, pub)
    def _boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(video_emit, "extract_frame", _boom)
    assert em.maybe_emit(_pal_iq(fs), fs, 5800.0, now_ts=1000) == "error"
    assert pub.videos == []
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd agent/scan && ./.venv/Scripts/python.exe -m pytest tests/test_video_emit.py -q`
Expected: FAIL (No module named 'video_emit').

- [ ] **Step 3: Implement** (`agent/scan/video_emit.py`)

```python
import logging
import os
import sys

# Expose the agent/video flat modules (pipeline, render, vconfig) to this scan-side bridge.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "video")))

from pipeline import extract_frame                  # noqa: E402  (agent/video core)
from render import save_full_png, thumbnail_b64     # noqa: E402

LOG = logging.getLogger("scan.video")


class VideoEmitter:
    """Bridge: run the video pipeline on an already-captured candidate IQ and publish a
    frame over the live MqttPublisher, throttled per channel. Never raises into the caller."""

    def __init__(self, publisher, vcfg, cooldown_s):
        self.publisher = publisher
        self.vcfg = vcfg
        self.cooldown_s = cooldown_s
        self._last = {}              # round(center_mhz, 1) -> last attempt ts

    def maybe_emit(self, iq, fs, center_mhz, now_ts):
        key = round(center_mhz, 1)
        last = self._last.get(key)
        if last is not None and now_ts - last < self.cooldown_s:
            return "cooldown"
        self._last[key] = now_ts     # throttle reprocessing regardless of outcome
        try:
            vf = extract_frame(iq, fs, center_mhz * 1e6, self.vcfg)
        except Exception:
            LOG.exception("video extract failed for %.1f MHz", center_mhz)
            return "error"
        if vf.standard is None:
            return "not_video"
        if vf.luma is None:
            return "no_lines"
        try:
            path = os.path.join(self.vcfg.frames_dir, f"{int(now_ts)}_{int(round(center_mhz))}.png")
            save_full_png(vf.luma, path)
        except Exception:
            LOG.exception("video frame save failed for %.1f MHz", center_mhz)
        try:
            thumb = thumbnail_b64(vf.luma, self.vcfg.thumb_max_width)
            if self.publisher is not None:
                self.publisher.publish_video(now_ts, center_mhz, vf.standard, vf.line_hz,
                                             round(float(vf.sync_snr_db), 1), thumb)
        except Exception:
            LOG.exception("video publish failed for %.1f MHz", center_mhz)
            return "error"
        LOG.info("video status=published center_mhz=%.1f standard=%s sync_snr_db=%.1f",
                 center_mhz, vf.standard, vf.sync_snr_db)
        return "published"
```

- [ ] **Step 4: Run it to confirm it passes**

Run: `cd agent/scan && ./.venv/Scripts/python.exe -m pytest tests/test_video_emit.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add agent/scan/video_emit.py agent/scan/tests/test_video_emit.py
git commit -m "feat(video): VideoEmitter bridge with per-channel cooldown"
```

---

### Task 6: Wire the emitter into `run_cycle` + `main()`

**Files:**
- Modify: `agent/scan/main.py`
- Modify: `agent/scan/tests/test_run_cycle.py`

- [ ] **Step 1: Write the failing test** — append to `agent/scan/tests/test_run_cycle.py`:

```python
class _FakeEmitter:
    def __init__(self):
        self.calls = []      # (fs, center_mhz, now_ts)

    def maybe_emit(self, iq, fs, center_mhz, now_ts):
        self.calls.append((fs, center_mhz, now_ts))
        return "published"


def test_run_cycle_emits_video_for_analog_only(tmp_path, monkeypatch):
    _write_fixtures(tmp_path)
    cfg = _config(tmp_path)
    em = _FakeEmitter()
    monkeypatch.setattr(main, "classify", lambda feat, thr: ("analog", 0.9))

    main.run_cycle(cfg, now_ts=1718530000, publisher=_FakePub(), emitter=em)

    assert len(em.calls) == 1                     # one analog candidate in the fixture
    fs, center_mhz, now_ts = em.calls[0]
    assert fs == cfg.dwell_sample_rate_hz
    assert abs(center_mhz - 5800.0) < 2.0
    assert now_ts == 1718530000


def test_run_cycle_skips_video_for_non_analog(tmp_path, monkeypatch):
    _write_fixtures(tmp_path)
    cfg = _config(tmp_path)
    em = _FakeEmitter()
    monkeypatch.setattr(main, "classify", lambda feat, thr: ("digital", 0.7))

    main.run_cycle(cfg, now_ts=1718530000, publisher=_FakePub(), emitter=em)

    assert em.calls == []                         # non-analog -> no video emit
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd agent/scan && ./.venv/Scripts/python.exe -m pytest tests/test_run_cycle.py -q`
Expected: FAIL (`run_cycle` got an unexpected keyword argument 'emitter').

- [ ] **Step 3: Implement** — in `agent/scan/main.py`.

Change the `run_cycle` signature:

```python
def run_cycle(cfg: Config, now_ts: int, publisher=None, emitter=None) -> dict:
```

Inside the candidate loop, immediately after the `detections.append(Detection(...))` block, add the
analog hook (guarded so a misbehaving emitter can never crash the cycle):

```python
            if emitter is not None and cls == "analog":
                try:
                    emitter.maybe_emit(iq, cfg.dwell_sample_rate_hz, c.center_mhz, now_ts)
                except Exception:
                    LOG.exception("video emit failed")
```

In `main()`, after the publisher is constructed (and after the `publisher = None` fallback path),
build the emitter lazily and pass it into `run_cycle`. Add, right before the `backoff = 1.0` line:

```python
    emitter = None
    try:
        import video_emit                       # adds ../video to sys.path as a side effect
        from vconfig import load_video_config
        vcfg = load_video_config()
        if vcfg.video_enabled:
            emitter = video_emit.VideoEmitter(publisher, vcfg, vcfg.emit_cooldown_s)
            LOG.info("video emitter enabled (cooldown=%.0fs)", vcfg.emit_cooldown_s)
    except Exception:
        LOG.exception("video emitter init failed; continuing without video")
```

And change the `run_cycle` call inside the loop from:

```python
            payload = run_cycle(cfg, now_ts=int(time.time()), publisher=publisher)
```

to:

```python
            payload = run_cycle(cfg, now_ts=int(time.time()), publisher=publisher, emitter=emitter)
```

- [ ] **Step 4: Run the run_cycle suite**

Run: `cd agent/scan && ./.venv/Scripts/python.exe -m pytest tests/test_run_cycle.py -q`
Expected: PASS (the 2 existing tests + 2 new — emitter defaults to None for the existing ones).

- [ ] **Step 5: Run the whole project suite**

Run: `cd agent/scan && ./.venv/Scripts/python.exe -m pytest tests ../video/tests -q`
Expected: PASS (everything green).

- [ ] **Step 6: Commit**

```bash
git add agent/scan/main.py agent/scan/tests/test_run_cycle.py
git commit -m "feat(video): run_cycle emits video for analog candidates; main wires emitter"
```

---

## Self-Review notes (for the implementer)

- **Spec coverage:** Task 1 = `extract_frame` (§4.1); Task 2 = CLI refactor/DRY (§4.2); Task 3 =
  config knobs (§4.5); Task 4 = `publish_video` (§4.3); Task 5 = `VideoEmitter` + cooldown + guard
  (§4.4, §5); Task 6 = `run_cycle` hook + lazy `main()` wiring (§4.6, §5). All confirmed decisions
  (§2) are covered.
- **Commit trailers:** append the repo convention to every commit message:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01Fr3LCjweDyLf1WRPz9PNUX`.
- **Live verification** (after deploy step): with an analog video TX on a known channel, confirm a
  retained `fpv/<id>/video` via `mosquitto_sub -t 'fpv/#' -v`, `fpv/<id>/status` untouched, scan loop
  still publishing spectrum/detection.
- Tests use `fs=8e6`; production reuses the dwell rate (20 Msps) — `extract_frame` is rate-agnostic.
