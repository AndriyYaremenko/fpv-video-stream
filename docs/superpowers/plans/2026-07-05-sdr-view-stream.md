# SDR Live-View Stream Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Operator-triggered live demod: `{view:'start', freq_mhz}` on `fpv/<id>/rxcmd` pauses the sweep and streams continuously-demodulated grayscale video (`hackrf_transfer → NumPy demod → ffmpeg x264 → RTSP`) to MediaMTX as the pseudo-camera `hackrf-view`; stop/10-min timeout resumes the sweep.

**Architecture:** View mode lives inside the existing `fpv-scan` service (single HackRF owner). New `agent/video/stream_demod.py` (chunked demod + subprocess pipeline, pure pieces testable), new `agent/scan/view_controller.py` (state machine polled by the main loop), `publisher.py` routes `view` payloads and publishes retained `fpv/<id>/view` state. Dashboard: view row in the scanner block, canvas-click fills the frequency field, ▶ per detection row; the `hackrf-view` tile itself is a normal camera (zero server changes).

**Tech Stack:** Python 3 (numpy, Pillow — already Pi deps), `hackrf_transfer`, `ffmpeg` (libx264), paho-mqtt; dashboard vanilla JS + `node --test`.

**Spec:** `docs/superpowers/specs/2026-07-05-sdr-view-stream-design.md`

**Branch:** `feat/sdr-view-stream` off `main`. The spec and this plan exist only in the old worktree `.claude/worktrees/feat+frames-gallery-filters/docs/superpowers/...` — copy both into the new worktree and commit as Task 1 Step 0.

## Global Constraints

- **No new Python deps** (numpy/Pillow/paho already on the Pi); **no new npm deps**; **no broker/ACL changes** (view commands ride `fpv/<id>/rxcmd`, which the browser's `sub` user may already write).
- View commands are published **NOT retained** (a retained start would replay on every Pi reconnect and re-enter view mode). RX5808 commands stay retained as today.
- `fpv/<id>/view` state payload: `{scanner_id, ts, active, freq_mhz, until_ts, error}` — retained, QoS 1.
- Frequency validation: finite, **100–6000 MHz** (HackRF range); anything else logged and ignored.
- Env (parsed in `agent/video/vconfig.py`): `VIEW_ENABLED` (default off), `VIEW_PUSH_URL` (required when enabled), `VIEW_SAMPLE_RATE_HZ`=8000000, `VIEW_MAX_S`=600, `VIEW_WIDTH`=480, `VIEW_FPS`=15, `VIEW_STANDARD`=auto (auto→detect, fallback **PAL** so noise still streams). Output height fixed by standard: **288 PAL / 240 NTSC**. Gains reuse scan `cfg.lna_gain/vga_gain/amp_enable`.
- Python tests: `cd agent/scan && python -m pytest tests ../video/tests -q` (Windows dev box: create a venv and `pip install -r agent/scan/requirements.txt pytest` if imports fail). Node tests: `npm test` from repo root.
- UI copy Ukrainian: «📺 SDR», «▶ дивитись», «■ свіп», badge «▶ NNNN МГц до HH:MM».
- Pi deploy NEVER overwrites `/etc/systemd/system/fpv-scan.service` (hand-diverged) — env goes into a drop-in.

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `agent/video/bench_stream.py` | create | throughput benchmark (go/no-go gate) |
| `agent/video/vconfig.py` | modify | `view_*` config fields + env parsing |
| `agent/video/stream_demod.py` | create | cmd builders, `pick_standard`, `resize_rows`, `chunk_to_frames`, `select_frames`, `FramePacer`, `run_stream` |
| `agent/video/tests/test_vconfig.py`, `test_stream_demod.py` | modify/create | unit tests |
| `agent/scan/publisher.py` | modify | `view` routing + `publish_view` |
| `agent/scan/view_controller.py` | create | mode state machine |
| `agent/scan/tests/test_publisher.py`, `test_view_controller.py` | modify/create | unit tests |
| `agent/scan/main.py` | modify | init + loop integration |
| `dashboard/public/mqtt-scan.js` | modify | `view` reducer, `buildViewCommand`, `publishView`, subscribe |
| `dashboard/public/spectrum.js` | modify | `viewCaption`, view row, canvas datasets, detection ▶ |
| `dashboard/public/app.js` | modify | click delegates, canvas→field, input preservation |
| `dashboard/public/styles.css` | modify | `.sdr-view-ctl` styles |
| `test/mqtt-scan.test.js`, `test/spectrum.test.js` | modify | node tests |

---

### Task 1: Benchmark the demod chain (go/no-go gate)

**Files:**
- Create: `agent/video/bench_stream.py`

**Interfaces:**
- Consumes: existing `demod.fm_demod/lowpass`, `frame.reconstruct_frames`, `render.normalize_luma`, `synth.make_cvbs/fm_modulate/to_int8`.
- Produces: a printed `×realtime` number. **Gate: on the Pi 5 the chunk must process in ≤ ~1.0× realtime** (ideally ≤0.7). If far above 1× — STOP, report to the operator (SoapySDR escalation is a separate project).

- [ ] **Step 0: Bring the spec + plan into the branch and commit**

Copy `docs/superpowers/specs/2026-07-05-sdr-view-stream-design.md` and
`docs/superpowers/plans/2026-07-05-sdr-view-stream.md` from the old worktree if absent, then:

```bash
git add docs/superpowers/specs/2026-07-05-sdr-view-stream-design.md docs/superpowers/plans/2026-07-05-sdr-view-stream.md
git commit -m "docs: SDR live-view stream design spec + implementation plan"
```

- [ ] **Step 1: Write the benchmark script** — create `agent/video/bench_stream.py`:

```python
"""Benchmark the chunked live-demod chain at the view sample rate.

Local sanity:   python agent/video/bench_stream.py --fs 8e6
On the Pi 5 (no checkout change — the script travels over stdin):
    ssh andriy@192.168.1.204 \
      '/opt/fpv-video-stream/agent/scan/.venv/bin/python - --fs 8e6' \
      < agent/video/bench_stream.py
Gate: "x realtime" <= ~1.0 means the Pi keeps up with live streaming.
"""
import argparse
import os
import sys
import time

for base in ("agent/video", "/opt/fpv-video-stream/agent/video",
             os.path.join(os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else ".", ".")):
    if os.path.isdir(base) and base not in sys.path:
        sys.path.insert(0, base)

import numpy as np
from demod import fm_demod, lowpass
from frame import reconstruct_frames
from render import normalize_luma
from synth import make_cvbs, fm_modulate, to_int8


def iq_from_int8(raw):
    data = np.frombuffer(raw, dtype=np.int8).astype(np.float32)
    return (data[0::2] + 1j * data[1::2]) / 128.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fs", type=float, default=8e6)
    ap.add_argument("--chunk-s", type=float, default=0.5)
    ap.add_argument("--rounds", type=int, default=10)
    ap.add_argument("--width", type=int, default=480)
    args = ap.parse_args()
    fs, chunk_s = args.fs, args.chunk_s

    img = (np.indices((64, 64)).sum(axis=0) % 2).astype(float)      # checkerboard
    bb = make_cvbs("PAL", img, fs, frames=max(1, int(round(chunk_s * 25))))
    raw = to_int8(fm_modulate(bb, fs, 4e6), noise_std=0.05)
    n_bytes = int(fs * 2 * chunk_s)
    raw = (raw * (n_bytes // len(raw) + 1))[:n_bytes]               # tile to one exact chunk

    t_total, frames_out = 0.0, 0
    for _ in range(args.rounds):
        t0 = time.perf_counter()
        iq = iq_from_int8(raw)
        base = lowpass(fm_demod(iq), fs, 5e6)
        frs = reconstruct_frames(base, fs, "PAL", args.width, 0.18)
        for fr in frs:
            normalize_luma(fr)
        t_total += time.perf_counter() - t0
        frames_out += len(frs)

    per_chunk = t_total / args.rounds
    print(f"fs={fs / 1e6:.1f}MS/s chunk={chunk_s}s rounds={args.rounds} width={args.width}")
    print(f"avg chunk time {per_chunk:.3f}s -> {per_chunk / chunk_s:.2f}x realtime (<=1.0 OK); "
          f"frames/chunk={frames_out / args.rounds:.1f}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run locally (sanity — chain works, number is indicative only)**

Run: `python agent/video/bench_stream.py --fs 8e6` (make a venv with `numpy pillow` if needed)
Expected: two lines, e.g. `... 0.4x realtime ...; frames/chunk=12.0` — no exceptions.

- [ ] **Step 3: Run on the Pi 5 and record the number**

Run (access per scanner-pi-deploy notes; if non-interactive SSH fails, ask the operator to run it):
```bash
ssh andriy@192.168.1.204 '/opt/fpv-video-stream/agent/scan/.venv/bin/python - --fs 8e6' < agent/video/bench_stream.py
```
Expected: `X.XXx realtime` ≤ ~1.0. **If > ~1.3: STOP the plan, report.** Retry with `--fs 6e6 --width 360` to find a workable point and note it for the deploy env.

- [ ] **Step 4: Commit (record the Pi number in the message)**

```bash
git add agent/video/bench_stream.py
git commit -m "feat(view): demod throughput benchmark (Pi5 @8MS/s: <measured>x realtime)"
```

---

### Task 2: `vconfig` — view settings

**Files:**
- Modify: `agent/video/vconfig.py`
- Test: `agent/video/tests/test_vconfig.py` (append)

**Interfaces:**
- Produces: `VideoConfig` fields `view_enabled: bool=False`, `view_push_url: str=""`, `view_sample_rate_hz: float=8_000_000.0`, `view_max_s: float=600.0`, `view_width: int=480`, `view_fps: float=15.0`, `view_standard: str="auto"` parsed from `VIEW_*` envs.

- [ ] **Step 1: Write the failing test** — append to `agent/video/tests/test_vconfig.py`:

```python
def test_view_defaults():
    from vconfig import load_video_config
    c = load_video_config(env={})
    assert c.view_enabled is False and c.view_push_url == ""
    assert c.view_sample_rate_hz == 8_000_000.0 and c.view_max_s == 600.0
    assert c.view_width == 480 and c.view_fps == 15.0 and c.view_standard == "auto"


def test_view_env_overrides():
    from vconfig import load_video_config
    c = load_video_config(env={
        "VIEW_ENABLED": "1", "VIEW_PUSH_URL": "rtsp://u:p@10.8.0.1:8554/hackrf-view",
        "VIEW_SAMPLE_RATE_HZ": "10000000", "VIEW_MAX_S": "300",
        "VIEW_WIDTH": "360", "VIEW_FPS": "10", "VIEW_STANDARD": "PAL",
    })
    assert c.view_enabled is True and c.view_push_url.endswith("/hackrf-view")
    assert c.view_sample_rate_hz == 10_000_000.0 and c.view_max_s == 300.0
    assert c.view_width == 360 and c.view_fps == 10.0
    assert c.view_standard == "pal"                     # normalized to lowercase
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd agent/scan && python -m pytest ../video/tests/test_vconfig.py -q`
Expected: FAIL — `VideoConfig` has no `view_enabled`.

- [ ] **Step 3: Implement** — in `agent/video/vconfig.py` add to the dataclass:

```python
    # SDR live-view stream (manual mode)
    view_enabled: bool = False
    view_push_url: str = ""
    view_sample_rate_hz: float = 8_000_000.0
    view_max_s: float = 600.0
    view_width: int = 480
    view_fps: float = 15.0
    view_standard: str = "auto"          # auto | pal | ntsc
```

and to `load_video_config` (before `return c`):

```python
    if "VIEW_ENABLED" in env:
        c.view_enabled = env["VIEW_ENABLED"].strip().lower() not in ("0", "false", "no", "")
    c.view_push_url = env.get("VIEW_PUSH_URL", c.view_push_url)
    if "VIEW_SAMPLE_RATE_HZ" in env:
        c.view_sample_rate_hz = float(env["VIEW_SAMPLE_RATE_HZ"])
    if "VIEW_MAX_S" in env:
        c.view_max_s = float(env["VIEW_MAX_S"])
    if "VIEW_WIDTH" in env:
        c.view_width = int(env["VIEW_WIDTH"])
    if "VIEW_FPS" in env:
        c.view_fps = float(env["VIEW_FPS"])
    if "VIEW_STANDARD" in env:
        c.view_standard = env["VIEW_STANDARD"].strip().lower()
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd agent/scan && python -m pytest ../video/tests/test_vconfig.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/video/vconfig.py agent/video/tests/test_vconfig.py
git commit -m "feat(view): VIEW_* config knobs in vconfig"
```

---

### Task 3: `stream_demod` — pure demod core

**Files:**
- Create: `agent/video/stream_demod.py`
- Test: `agent/video/tests/test_stream_demod.py`

**Interfaces:**
- Consumes: `demod.fm_demod/lowpass`, `standard.detect_standard`, `frame.reconstruct_frames`, `render.normalize_luma` (all existing).
- Produces (Task 4/7 rely on these exact names):
  - `VIEW_HEIGHT = {"PAL": 288, "NTSC": 240}`
  - `build_capture_cmd(freq_hz, sample_rate_hz, lna=40, vga=20, amp=0) -> list[str]` (`hackrf_transfer -r -` …, **no `-n`**)
  - `build_encode_cmd(push_url, width, height, fps) -> list[str]`
  - `pick_standard(baseband, fs, forced="auto", line_snr_db=10.0, harm_snr_db=6.0) -> "PAL"|"NTSC"` (noise → PAL fallback)
  - `resize_rows(img, height) -> np.ndarray` (rows→height, cols preserved)
  - `chunk_to_frames(iq, fs, standard, width, height, lpf_cutoff_hz=5e6, blank_frac=0.18) -> list[np.uint8 (height,width)]`

- [ ] **Step 1: Write the failing tests** — create `agent/video/tests/test_stream_demod.py`:

```python
import numpy as np

from stream_demod import (VIEW_HEIGHT, build_capture_cmd, build_encode_cmd,
                          pick_standard, resize_rows, chunk_to_frames)
from synth import make_cvbs, fm_modulate
from demod import fm_demod, lowpass


def test_capture_cmd_streams_to_stdout():
    cmd = build_capture_cmd(5865e6, 8e6, lna=32, vga=22, amp=1)
    assert cmd[:3] == ["hackrf_transfer", "-r", "-"]
    assert "-n" not in cmd                                  # continuous, not fixed-count
    assert "5865000000" in cmd and "8000000" in cmd
    assert cmd[cmd.index("-l") + 1] == "32" and cmd[cmd.index("-a") + 1] == "1"


def test_encode_cmd_rawgray_to_rtsp():
    cmd = build_encode_cmd("rtsp://u:p@10.8.0.1:8554/hackrf-view", 480, 288, 15)
    assert cmd[0] == "ffmpeg" and cmd[-1].endswith("/hackrf-view")
    assert "480x288" in cmd and "gray" in cmd
    assert "zerolatency" in cmd and "rtsp" in cmd and "yuv420p" in cmd


def test_pick_standard_forced_and_noise_fallback():
    noise = np.random.default_rng(1).normal(0, 1, 200_000)
    assert pick_standard(noise, 8e6, forced="ntsc") == "NTSC"
    assert pick_standard(noise, 8e6, forced="pal") == "PAL"
    assert pick_standard(noise, 8e6, forced="auto") == "PAL"     # gate rejects -> fallback


def test_pick_standard_detects_real_pal():
    fs = 8e6
    img = np.tile(np.linspace(0, 1, 64), (64, 1))
    bb = make_cvbs("PAL", img, fs, frames=8)
    base = lowpass(fm_demod(fm_modulate(bb, fs, 4e6)), fs, 5e6)
    assert pick_standard(base, fs, forced="auto") == "PAL"


def test_resize_rows_shapes():
    img = np.arange(20, dtype=np.uint8).reshape(10, 2)
    assert resize_rows(img, 4).shape == (4, 2)
    assert resize_rows(img, 25).shape == (25, 2)
    assert resize_rows(np.zeros((0, 2), dtype=np.uint8), 4).shape == (4, 2)


def test_chunk_to_frames_fixed_size_uint8():
    fs = 4e6                                    # cheaper than 8e6; same code path
    img = (np.indices((48, 48)).sum(axis=0) % 2).astype(float)
    bb = make_cvbs("PAL", img, fs, frames=6)
    iq = fm_modulate(bb, fs, 2e6)
    frames = chunk_to_frames(iq, fs, "PAL", width=320, height=VIEW_HEIGHT["PAL"],
                             lpf_cutoff_hz=2.5e6)
    assert len(frames) >= 5
    for fr in frames:
        assert fr.shape == (288, 320) and fr.dtype == np.uint8
    assert frames[0].std() > 5                  # picture content, not a flat field
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd agent/scan && python -m pytest ../video/tests/test_stream_demod.py -q`
Expected: FAIL — `No module named 'stream_demod'`.

- [ ] **Step 3: Implement** — create `agent/video/stream_demod.py`:

```python
"""Continuous IQ -> grayscale frames for the SDR live-view stream.

Pure pieces (unit-tested): command builders, standard pick with PAL fallback,
row resize, chunk->frames. The subprocess pipeline (run_stream) is added on top
and kept as thin as possible."""
import logging

import numpy as np

from demod import fm_demod, lowpass
from standard import detect_standard
from frame import reconstruct_frames
from render import normalize_luma

LOG = logging.getLogger("video.stream")

VIEW_HEIGHT = {"PAL": 288, "NTSC": 240}


def build_capture_cmd(freq_hz, sample_rate_hz, lna=40, vga=20, amp=0):
    """hackrf_transfer argv streaming int8 IQ to stdout (no -n: runs until killed)."""
    return ["hackrf_transfer", "-r", "-", "-f", str(int(freq_hz)),
            "-s", str(int(sample_rate_hz)),
            "-l", str(int(lna)), "-g", str(int(vga)), "-a", str(int(amp))]


def build_encode_cmd(push_url, width, height, fps):
    """ffmpeg argv: raw gray frames on stdin -> low-latency H.264 RTSP push."""
    return ["ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{width}x{height}",
            "-r", str(fps), "-i", "-",
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
            "-pix_fmt", "yuv420p", "-f", "rtsp", "-rtsp_transport", "tcp", push_url]


def pick_standard(baseband, fs, forced="auto", line_snr_db=10.0, harm_snr_db=6.0):
    """'pal'/'ntsc' forced -> that standard; otherwise detect, falling back to
    PAL so the stream still shows *something* on pure noise."""
    if forced in ("pal", "ntsc"):
        return forced.upper()
    res = detect_standard(baseband, fs, line_snr_db=line_snr_db, harm_snr_db=harm_snr_db)
    return res.standard or "PAL"


def resize_rows(img, height):
    """Nearest-row resample of a (rows, w) image to (height, w)."""
    if img.shape[0] == 0:
        return np.zeros((height, img.shape[1]), dtype=img.dtype)
    idx = np.clip(np.round(np.linspace(0, img.shape[0] - 1, height)).astype(int),
                  0, img.shape[0] - 1)
    return img[idx, :]


def chunk_to_frames(iq, fs, standard, width, height, lpf_cutoff_hz=5e6, blank_frac=0.18):
    """One IQ chunk -> list of fixed-size uint8 gray frames (height x width)."""
    bb = lowpass(fm_demod(iq), fs, lpf_cutoff_hz)
    out = []
    for fr in reconstruct_frames(bb, fs, standard, width, blank_frac):
        if fr.size == 0:
            continue
        out.append(resize_rows(normalize_luma(fr), height))
    return out
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd agent/scan && python -m pytest ../video/tests/test_stream_demod.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add agent/video/stream_demod.py agent/video/tests/test_stream_demod.py
git commit -m "feat(view): stream demod core — builders, standard pick, chunked frames"
```

---

### Task 4: `FramePacer`, `select_frames` + `run_stream`

**Files:**
- Modify: `agent/video/stream_demod.py` (append)
- Test: `agent/video/tests/test_stream_demod.py` (append)

**Interfaces:**
- Produces (Task 6/7 rely on):
  - `select_frames(frames, chunk_s, fps) -> list` — even subsample so one chunk never emits more than `chunk_s*fps` frames.
  - `class FramePacer(fps, write, clock=None, sleep=None)` with `.tick(frame_bytes)` — blocks to the next 1/fps slot, then writes once.
  - `run_stream(vcfg, freq_mhz, stop_event, max_s, lna=40, vga=20, amp=0, popen=None, clock=None, sleep=None) -> str|None` — blocking; `None` = clean stop/timeout, string = error. Kills both subprocesses on exit. `freq_mhz` in MHz.
  - `CHUNK_S = 0.5`.

- [ ] **Step 1: Write the failing tests** — append to `agent/video/tests/test_stream_demod.py`:

```python
import io
import threading

from stream_demod import CHUNK_S, FramePacer, select_frames, run_stream
from synth import to_int8
from vconfig import VideoConfig


def test_select_frames_caps_to_fps_budget():
    frames = list(range(12))
    sel = select_frames(frames, 0.5, 15)          # budget = 8
    assert len(sel) == 8 and sel[0] == 0 and sel[-1] == 11
    assert select_frames([1, 2], 0.5, 15) == [1, 2]
    assert select_frames([], 0.5, 15) == []


def test_frame_pacer_spaces_writes():
    t = [0.0]
    slept = []
    out = []
    pacer = FramePacer(10, out.append, clock=lambda: t[0],
                       sleep=lambda s: (slept.append(s), t.__setitem__(0, t[0] + s)))
    pacer.tick(b"a")                               # first write: immediate
    pacer.tick(b"b")                               # second: sleeps ~0.1s
    assert out == [b"a", b"b"]
    assert len(slept) == 1 and abs(slept[0] - 0.1) < 1e-6


class _FakeProc:
    def __init__(self, stdout=None):
        self.stdout = stdout
        self.stdin = io.BytesIO()
        self.killed = False

    def poll(self):
        if self.stdout is None:
            return None
        return 1 if self.stdout.tell() >= len(self.stdout.getbuffer()) else None

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return 0


def _vcfg():
    c = VideoConfig()
    c.view_push_url = "rtsp://u:p@10.8.0.1:8554/hackrf-view"
    c.view_sample_rate_hz = 4e6
    c.view_width = 320
    c.view_fps = 10.0
    c.view_standard = "pal"                        # skip detection: deterministic
    c.lpf_cutoff_hz = 2.5e6
    return c


def _chunk_bytes(fs, seconds=CHUNK_S):
    img = (np.indices((32, 32)).sum(axis=0) % 2).astype(float)
    bb = make_cvbs("PAL", img, fs, frames=max(1, int(round(seconds * 25))))
    raw = to_int8(fm_modulate(bb, fs, 2e6))
    need = int(fs * 2 * seconds)
    return (raw * (need // len(raw) + 1))[:need]


def test_run_stream_reports_capture_death_and_kills_procs():
    fs = 4e6
    procs = []

    def popen(cmd, **kw):
        p = _FakeProc(stdout=io.BytesIO(_chunk_bytes(fs) * 2)) if cmd[0] == "hackrf_transfer" else _FakeProc()
        procs.append(p)
        return p

    t = [0.0]
    err = run_stream(_vcfg(), 947.0, threading.Event(), max_s=60.0, popen=popen,
                     clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + max(s, 0.01)))
    assert err == "hackrf_transfer exited"          # finite stdout -> EOF -> death detected
    assert all(p.killed for p in procs)
    enc = procs[1]                                   # frames actually reached ffmpeg stdin
    frame_size = 320 * 288
    assert len(enc.stdin.getvalue()) >= frame_size
    assert len(enc.stdin.getvalue()) % frame_size == 0


def test_run_stream_stops_cleanly_on_stop_event():
    stop = threading.Event()
    stop.set()

    def popen(cmd, **kw):
        return _FakeProc(stdout=io.BytesIO(b"")) if cmd[0] == "hackrf_transfer" else _FakeProc()

    err = run_stream(_vcfg(), 947.0, stop, max_s=60.0, popen=popen,
                     clock=lambda: 0.0, sleep=lambda s: None)
    assert err is None


def test_run_stream_times_out():
    fs = 4e6
    chunk = _chunk_bytes(fs)

    def popen(cmd, **kw):
        return _FakeProc(stdout=io.BytesIO(chunk * 1000)) if cmd[0] == "hackrf_transfer" else _FakeProc()

    t = [0.0]
    err = run_stream(_vcfg(), 947.0, threading.Event(), max_s=1.0, popen=popen,
                     clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + max(s, 0.05)))
    assert err is None                               # deadline reached = clean exit
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd agent/scan && python -m pytest ../video/tests/test_stream_demod.py -q`
Expected: FAIL — `select_frames`/`FramePacer`/`run_stream` not defined.

- [ ] **Step 3: Implement** — append to `agent/video/stream_demod.py`:

```python
import subprocess
import threading
import time

from dweller import iq_from_int8            # agent/scan flat module (conftest/sys.path shared)

CHUNK_S = 0.5


def select_frames(frames, chunk_s, fps):
    """Even subsample so one chunk emits at most chunk_s*fps frames (pacing budget)."""
    want = max(1, int(round(chunk_s * fps)))
    if len(frames) <= want:
        return list(frames)
    idx = np.round(np.linspace(0, len(frames) - 1, want)).astype(int)
    return [frames[i] for i in idx]


class FramePacer:
    """Writes frames at a fixed fps so ffmpeg's rawvideo timeline stays real-time."""

    def __init__(self, fps, write, clock=None, sleep=None):
        self._period = 1.0 / fps
        self._write = write
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._next = None

    def tick(self, frame_bytes):
        now = self._clock()
        if self._next is None:
            self._next = now
        if self._next > now:
            self._sleep(self._next - now)
        self._write(frame_bytes)
        self._next = max(self._next + self._period, self._clock() - self._period)


def run_stream(vcfg, freq_mhz, stop_event, max_s, lna=40, vga=20, amp=0,
               popen=None, clock=None, sleep=None):
    """Blocking capture->demod->encode loop for one view session.

    Returns None on clean stop/timeout, or an error string when a subprocess
    died. Always kills both subprocesses before returning. A dedicated reader
    thread drains hackrf stdout into a single-slot mailbox (dropping backlog)
    so the USB stream never stalls on a full pipe.
    """
    popen = popen or subprocess.Popen
    clock = clock or time.monotonic
    sleep = sleep or time.sleep
    fs = vcfg.view_sample_rate_hz
    chunk_bytes = int(fs * 2 * CHUNK_S)
    cap = popen(build_capture_cmd(freq_mhz * 1e6, fs, lna, vga, amp),
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=chunk_bytes)
    enc = None
    pacer = None
    standard = None
    height = None
    error = None
    mailbox = {}
    lock = threading.Lock()

    def _reader():
        while not stop_event.is_set():
            try:
                buf = cap.stdout.read(chunk_bytes)
            except Exception:
                return
            if not buf or len(buf) < chunk_bytes:
                return                                   # EOF: capture died
            with lock:
                mailbox["chunk"] = buf                   # drop any unconsumed chunk

    threading.Thread(target=_reader, daemon=True).start()
    t_end = clock() + max_s
    try:
        while not stop_event.is_set() and clock() < t_end:
            with lock:
                buf = mailbox.pop("chunk", None)
            if buf is None:
                if cap.poll() is not None:
                    error = "hackrf_transfer exited"
                    break
                sleep(0.05)
                continue
            iq = iq_from_int8(buf)
            if standard is None:
                bb = lowpass(fm_demod(iq), fs, vcfg.lpf_cutoff_hz)
                standard = pick_standard(bb, fs, vcfg.view_standard,
                                         vcfg.line_snr_db, vcfg.harm_snr_db)
                height = VIEW_HEIGHT[standard]
                enc = popen(build_encode_cmd(vcfg.view_push_url, vcfg.view_width,
                                             height, vcfg.view_fps),
                            stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
                pacer = FramePacer(vcfg.view_fps, enc.stdin.write, clock=clock, sleep=sleep)
                LOG.info("view stream: %s %dx%d @%.0ffps", standard, vcfg.view_width,
                         height, vcfg.view_fps)
            frames = select_frames(
                chunk_to_frames(iq, fs, standard, vcfg.view_width, height,
                                vcfg.lpf_cutoff_hz, vcfg.blank_frac),
                CHUNK_S, vcfg.view_fps)
            for fr in frames:
                if stop_event.is_set() or clock() >= t_end:
                    break
                try:
                    pacer.tick(fr.tobytes())
                except (BrokenPipeError, OSError):
                    error = "ffmpeg pipe closed"
                    break
            if error:
                break
            if enc is not None and enc.poll() is not None:
                error = "ffmpeg exited"
                break
    finally:
        for proc in (cap, enc):
            if proc is None:
                continue
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass
    return error
```

Note: `from dweller import iq_from_int8` works because `agent/video/conftest.py` puts
`agent/scan` on the path for tests, and on the Pi `main.py` runs from `agent/scan` (same dir).

- [ ] **Step 4: Run to verify they pass**

Run: `cd agent/scan && python -m pytest ../video/tests/test_stream_demod.py -q`
Expected: PASS (10 tests).

- [ ] **Step 5: Run both python suites, commit**

Run: `cd agent/scan && python -m pytest tests ../video/tests -q` — all pass.

```bash
git add agent/video/stream_demod.py agent/video/tests/test_stream_demod.py
git commit -m "feat(view): pacer + run_stream subprocess pipeline (fake-popen tested)"
```

---

### Task 5: publisher — `view` command routing + `publish_view`

**Files:**
- Modify: `agent/scan/publisher.py`
- Test: `agent/scan/tests/test_publisher.py` (append)

**Interfaces:**
- Produces: `MqttPublisher.on_view_command` (callback `fn(dict)`), payloads with a `view` key are routed there and NEVER reach `on_command`; `publish_view(ts, active, freq_mhz=None, until_ts=None, error=None)` → retained QoS1 `fpv/<id>/view` `{scanner_id, ts, active, freq_mhz, until_ts, error}`.

- [ ] **Step 1: Write the failing tests** — append to `agent/scan/tests/test_publisher.py`:

```python
import json as _json

from publisher import MqttPublisher


class _Msg:
    def __init__(self, payload):
        self.payload = payload


def test_on_message_routes_view_and_legacy_separately():
    p = MqttPublisher("h", 1883, "", "", "scan-01")
    seen = {}
    p.on_command = lambda mode, ch: seen.setdefault("rx", (mode, ch))
    p.on_view_command = lambda d: seen.setdefault("view", d)
    p._on_message(None, None, _Msg(b'{"view":"start","freq_mhz":5865}'))
    p._on_message(None, None, _Msg(b'{"mode":"manual","channel":"F4"}'))
    assert seen["view"] == {"view": "start", "freq_mhz": 5865}
    assert seen["rx"] == ("manual", "F4")


def test_view_payload_never_reaches_legacy_handler():
    p = MqttPublisher("h", 1883, "", "", "scan-01")
    calls = []
    p.on_command = lambda mode, ch: calls.append((mode, ch))
    p.on_view_command = None                       # even with no view handler wired
    p._on_message(None, None, _Msg(b'{"view":"stop"}'))
    assert calls == []


class _FakeClient:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, _json.loads(payload), qos, retain))


def test_publish_view_contract():
    p = MqttPublisher("h", 1883, "", "", "scan-01")
    p._client = _FakeClient()
    p.publish_view(123, True, freq_mhz=5865.0, until_ts=723)
    topic, data, qos, retain = p._client.published[0]
    assert topic == "fpv/scan-01/view" and qos == 1 and retain is True
    assert data == {"scanner_id": "scan-01", "ts": 123, "active": True,
                    "freq_mhz": 5865.0, "until_ts": 723, "error": None}
    p.publish_view(124, False, error="ffmpeg exited")
    data2 = p._client.published[1][1]
    assert data2["active"] is False and data2["error"] == "ffmpeg exited"
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd agent/scan && python -m pytest tests/test_publisher.py -q`
Expected: FAIL — no `on_view_command` / `publish_view`.

- [ ] **Step 3: Implement** — in `agent/scan/publisher.py`:

In `__init__`, after `self._t_rxcmd = ...` add:

```python
        self._t_view = f"fpv/{scanner_id}/view"
        self.on_view_command = None     # set by the caller: fn(dict) — SDR view start/stop
```

Replace the body of `_on_message` after the `isinstance` guard with routing (full method):

```python
    def _on_message(self, client, userdata, msg, *args):
        # Inbound dashboard command on fpv/<id>/rxcmd. Fully guarded — never disturbs the loop.
        try:
            data = json.loads(msg.payload)
        except Exception:
            return
        if not isinstance(data, dict):
            return
        if "view" in data:              # SDR view command — never routed to the RX5808 handler
            if self.on_view_command is not None:
                try:
                    self.on_view_command(data)
                except Exception:
                    LOG.exception("on_view_command handler failed")
            return
        if self.on_command is None:
            return
        try:
            self.on_command(data.get("mode"), data.get("channel"))
        except Exception:
            LOG.exception("on_command handler failed")
```

After `publish_rxtune` add:

```python
    def publish_view(self, ts, active, freq_mhz=None, until_ts=None, error=None):
        self._publish(
            self._t_view,
            {"scanner_id": self.scanner_id, "ts": ts, "active": bool(active),
             "freq_mhz": freq_mhz, "until_ts": until_ts, "error": error},
            self.QOS_DETECTION,
        )
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd agent/scan && python -m pytest tests/test_publisher.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/scan/publisher.py agent/scan/tests/test_publisher.py
git commit -m "feat(view): rxcmd view-routing + retained fpv/<id>/view state"
```

---

### Task 6: `ViewController`

**Files:**
- Create: `agent/scan/view_controller.py`
- Test: `agent/scan/tests/test_view_controller.py`

**Interfaces:**
- Consumes: `publisher.publish_view` (Task 5), a `run_stream`-shaped callable `fn(freq_mhz, stop_event, max_s) -> str|None` (Task 4, partially applied in Task 7), injected `reset` (=`device.reset_hackrf`).
- Produces (Task 7 relies on): `ViewController(publisher, run_stream, max_s=600.0, reset=None, clock=None)` with `set_command(dict)` (MQTT thread), `pending() -> float|None` (consume-once), `run_view(freq_mhz) -> str|None` (blocking).

- [ ] **Step 1: Write the failing tests** — create `agent/scan/tests/test_view_controller.py`:

```python
from view_controller import ViewController


class _Pub:
    def __init__(self):
        self.calls = []

    def publish_view(self, ts, active, freq_mhz=None, until_ts=None, error=None):
        self.calls.append({"ts": ts, "active": active, "freq_mhz": freq_mhz,
                           "until_ts": until_ts, "error": error})


def test_set_command_validates_and_pending_consumes_once():
    vc = ViewController(None, run_stream=lambda *a: None)
    vc.set_command({"view": "start", "freq_mhz": 5865})
    assert vc.pending() == 5865.0
    assert vc.pending() is None
    for bad in ({"view": "start"}, {"view": "start", "freq_mhz": "x"},
                {"view": "start", "freq_mhz": 50}, {"view": "start", "freq_mhz": 9000},
                {"view": "wat"}, {}):
        vc.set_command(bad)
        assert vc.pending() is None


def test_run_view_lifecycle_publishes_and_resets():
    pub = _Pub()
    resets = []

    def stream(freq, stop, max_s):
        assert freq == 5865.0 and max_s == 60.0 and not stop.is_set()
        return None

    vc = ViewController(pub, stream, max_s=60.0,
                        reset=lambda: resets.append(1), clock=lambda: 1000.0)
    assert vc.run_view(5865.0) is None
    start, end = pub.calls
    assert start["active"] is True and start["freq_mhz"] == 5865.0 and start["until_ts"] == 1060
    assert end["active"] is False and end["error"] is None and end["freq_mhz"] is None
    assert resets == [1]


def test_run_view_reports_error_and_crash():
    pub = _Pub()
    vc = ViewController(pub, lambda f, s, m: "ffmpeg exited", max_s=60.0, reset=lambda: None)
    assert vc.run_view(5000.0) == "ffmpeg exited"
    assert pub.calls[-1]["error"] == "ffmpeg exited"

    def boom(f, s, m):
        raise RuntimeError("boom")

    vc2 = ViewController(pub, boom, max_s=60.0, reset=lambda: None)
    assert "boom" in vc2.run_view(5000.0)
    assert "boom" in pub.calls[-1]["error"]


def test_stale_stop_is_cleared_before_a_new_session():
    seen = {}

    def stream(freq, stop, max_s):
        seen["preset"] = stop.is_set()
        return None

    vc = ViewController(_Pub(), stream, max_s=60.0, reset=lambda: None)
    vc.set_command({"view": "stop"})               # stop arrives while idle
    vc.run_view(5000.0)
    assert seen["preset"] is False                 # run_view cleared it
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd agent/scan && python -m pytest tests/test_view_controller.py -q`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement** — create `agent/scan/view_controller.py`:

```python
import logging
import threading
import time

LOG = logging.getLogger("scan.view")

FREQ_MIN_MHZ = 100.0
FREQ_MAX_MHZ = 6000.0        # HackRF tuning range


class ViewController:
    """Manual SDR live-view mode. The MQTT thread calls set_command(); the scan
    loop polls pending() between cycles and calls run_view(), which blocks until
    the stop command, the max_s deadline, or a streamer error — then the sweep
    resumes. Never raises into callers."""

    def __init__(self, publisher, run_stream, max_s=600.0, reset=None, clock=None):
        self._publisher = publisher
        self._run_stream = run_stream        # fn(freq_mhz, stop_event, max_s) -> error|None
        self._max_s = max_s
        self._reset = reset or (lambda: None)
        self._clock = clock or time.time
        self._lock = threading.Lock()
        self._pending = None
        self._stop = threading.Event()

    def set_command(self, data):
        action = data.get("view")
        if action == "stop":
            self._stop.set()
            return
        if action != "start":
            LOG.warning("view: ignoring unknown action %r", action)
            return
        freq = data.get("freq_mhz")
        if not isinstance(freq, (int, float)) or not (FREQ_MIN_MHZ <= float(freq) <= FREQ_MAX_MHZ):
            LOG.warning("view: ignoring start with bad freq_mhz %r", freq)
            return
        with self._lock:
            self._pending = float(freq)

    def pending(self):
        with self._lock:
            p, self._pending = self._pending, None
        return p

    def run_view(self, freq_mhz):
        self._stop.clear()                   # a stale idle-time stop must not kill this session
        ts = int(self._clock())
        self._pub(ts, True, freq_mhz, ts + int(self._max_s))
        error = None
        try:
            error = self._run_stream(freq_mhz, self._stop, self._max_s)
        except Exception as e:
            LOG.exception("view stream crashed")
            error = str(e)
        finally:
            self._pub(int(self._clock()), False, None, None, error)
            try:
                self._reset()                # leave the device clean for the next sweep
            except Exception:
                LOG.exception("view: device reset failed")
            self._stop.clear()
        return error

    def _pub(self, ts, active, freq_mhz, until_ts, error=None):
        if self._publisher is None:
            return
        try:
            self._publisher.publish_view(ts, active, freq_mhz=freq_mhz,
                                         until_ts=until_ts, error=error)
        except Exception:
            LOG.exception("view state publish failed")
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd agent/scan && python -m pytest tests/test_view_controller.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add agent/scan/view_controller.py agent/scan/tests/test_view_controller.py
git commit -m "feat(view): ViewController state machine (validate, run, reset, resume)"
```

---

### Task 7: `main.py` wiring

**Files:**
- Modify: `agent/scan/main.py`

**Interfaces:**
- Consumes: everything above. No unit tests (`main()` is untested in this codebase); the deliverable is both python suites staying green + the live acceptance in Deploy.

- [ ] **Step 1: Init the view controller** — in `main()`, right after the `controller` init block (before the `publisher.on_command` wiring), add:

```python
    view = None
    try:
        if publisher is not None:
            import video_emit                    # noqa: F401  (side effect: ../video on sys.path)
            from vconfig import load_video_config
            viewcfg = load_video_config()
            if viewcfg.view_enabled and viewcfg.view_push_url:
                import stream_demod
                from view_controller import ViewController
                view = ViewController(
                    publisher,
                    run_stream=lambda freq, stop, max_s: stream_demod.run_stream(
                        viewcfg, freq, stop, max_s,
                        lna=cfg.lna_gain, vga=cfg.vga_gain, amp=cfg.amp_enable),
                    max_s=viewcfg.view_max_s,
                    reset=reset_hackrf,
                )
                publisher.on_view_command = view.set_command
                LOG.info("SDR view mode enabled (push=%s max=%.0fs)",
                         viewcfg.view_push_url.split("@")[-1], viewcfg.view_max_s)
    except Exception:
        LOG.exception("view mode init failed; continuing without it")
```

(Wired before `publisher.connect(...)`, like the RX5808 command hookup, so nothing delivered at
subscribe time is dropped.)

- [ ] **Step 2: Integrate into the loop** — in the `while True:` body, before `payload = run_cycle(...)`, add:

```python
            req = view.pending() if view is not None else None
            if req is not None:
                LOG.info("entering SDR view @ %.1f MHz (sweep paused)", req)
                view.run_view(req)
                LOG.info("SDR view ended; sweep resumes")
                continue
```

(Latency note: a start command lands after the current sweep cycle finishes — seconds, accepted.)

- [ ] **Step 3: Run both python suites**

Run: `cd agent/scan && python -m pytest tests ../video/tests -q`
Expected: all pass (no new tests; regression gate).

- [ ] **Step 4: Commit**

```bash
git add agent/scan/main.py
git commit -m "feat(view): pause sweep and run the SDR view session from the scan loop"
```

---

### Task 8: `mqtt-scan.js` — view state + command

**Files:**
- Modify: `dashboard/public/mqtt-scan.js`
- Test: `test/mqtt-scan.test.js` (append)

**Interfaces:**
- Produces (Task 9 relies on): `store[id].view = {ts, active, freq_mhz, until_ts, error}`; pure `buildViewCommand(action, freqMhz)`; `MqttScanClient.publishView(id, action, freqMhz)` → `fpv/<id>/rxcmd`, **retain:false**.

- [ ] **Step 1: Write the failing tests** — append to `test/mqtt-scan.test.js` (extend its import line with `buildViewCommand`):

```js
test('reduce: fpv/<id>/view updates the view state', () => {
  const s = reduce(emptyStore(), 'fpv/hackrf/view', JSON.stringify({
    scanner_id: 'hackrf', ts: 5, active: true, freq_mhz: 5865, until_ts: 605, error: null,
  }));
  assert.deepEqual(s.hackrf.view, { ts: 5, active: true, freq_mhz: 5865, until_ts: 605, error: null });
  reduce(s, 'fpv/hackrf/view', JSON.stringify({ ts: 6, active: false, error: 'ffmpeg exited' }));
  assert.equal(s.hackrf.view.active, false);
  assert.equal(s.hackrf.view.freq_mhz, null);
  assert.equal(s.hackrf.view.error, 'ffmpeg exited');
});

test('buildViewCommand: start carries freq, stop does not', () => {
  assert.deepEqual(buildViewCommand('start', 5865), { view: 'start', freq_mhz: 5865 });
  assert.deepEqual(buildViewCommand('stop'), { view: 'stop' });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `node --test test/mqtt-scan.test.js`
Expected: FAIL — no `buildViewCommand` export / `view` not reduced.

- [ ] **Step 3: Implement** — in `dashboard/public/mqtt-scan.js`:

- `ensure()`: add `view: null` to the initial object.
- `reduce()` regex: `(spectrum|detection|status|video|rxtune|view)`.
- Add a reducer branch after `rxtune`:

```js
  } else if (kind === 'view') {
    s.view = {
      ts: data.ts || 0,
      active: !!data.active,
      freq_mhz: data.freq_mhz == null ? null : Number(data.freq_mhz),
      until_ts: data.until_ts == null ? null : Number(data.until_ts),
      error: data.error || null,
    };
  }
```

- Pure builder next to `buildCommand`:

```js
// Build an SDR view command for fpv/<id>/rxcmd ({view:'start',freq_mhz} | {view:'stop'}).
export function buildViewCommand(action, freqMhz) {
  const cmd = { view: action === 'stop' ? 'stop' : 'start' };
  if (cmd.view === 'start') cmd.freq_mhz = Number(freqMhz);
  return cmd;
}
```

- Subscribe list: add `'fpv/+/view'`.
- Client method after `publishCommand`:

```js
  // SDR view command — same rxcmd topic (ACL already allows it), but NOT retained:
  // a retained start would replay and re-enter view mode on every Pi reconnect.
  publishView(id, action, freqMhz) {
    if (!this.client || !id) return;
    if (action === 'start' && !Number.isFinite(Number(freqMhz))) return;
    this.client.publish(
      `fpv/${id}/rxcmd`, JSON.stringify(buildViewCommand(action, freqMhz)),
      { qos: 1, retain: false },
    );
  }
```

- [ ] **Step 4: Run to verify they pass**

Run: `node --test test/mqtt-scan.test.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/public/mqtt-scan.js test/mqtt-scan.test.js
git commit -m "feat(view): view state reducer + non-retained view commands"
```

---

### Task 9: dashboard UI — view row, canvas→field, detection ▶, styles

**Files:**
- Modify: `dashboard/public/spectrum.js`, `dashboard/public/app.js`, `dashboard/public/styles.css`
- Test: `test/spectrum.test.js` (append)

**Interfaces:**
- Consumes: `store[id].view`, `scanClient.publishView` (Task 8).
- Produces: `.sdr-view-ctl` row with `.view-freq` input + `[data-viewact="start"|"stop"]` buttons + badge; every band canvas carries `data-low-mhz/high-mhz`; detection rows carry `[data-viewfreq]` ▶ buttons; pure `viewCaption(view)` export.

- [ ] **Step 1: Write the failing test** — append to `test/spectrum.test.js` (extend its import with `viewCaption`):

```js
test('viewCaption: active shows freq and until, inactive empty', () => {
  assert.equal(viewCaption(null), '');
  assert.equal(viewCaption({ active: false, freq_mhz: 5865 }), '');
  assert.match(viewCaption({ active: true, freq_mhz: 5865, until_ts: 1783111800 }),
    /^▶ 5865 МГц до \d{2}:\d{2}$/);
  assert.equal(viewCaption({ active: true, freq_mhz: 5865 }), '▶ 5865 МГц');
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test test/spectrum.test.js`
Expected: FAIL — no `viewCaption` export.

- [ ] **Step 3: Implement `spectrum.js`**

After `rxtuneCaption` add:

```js
// Caption for the live SDR view badge: "▶ 5865 МГц до 18:45".
export function viewCaption(view) {
  if (!view || !view.active) return '';
  const until = view.until_ts
    ? ` до ${new Date(view.until_ts * 1000).toTimeString().slice(0, 5)}` : '';
  return `▶ ${fmtFreq(view.freq_mhz)}${until}`;
}
```

In `scannerBlock`, after the `rx5808Controls` append, add:

```js
  block.appendChild(viewControls(live && live.view));
```

New function next to `rx5808Controls`:

```js
// SDR live-view controls: frequency field + start/stop + live badge.
function viewControls(view) {
  const active = !!(view && view.active);
  const row = el('div', 'sdr-view-ctl', `
    <span class="view-label">📺 SDR</span>
    <input class="view-freq" type="number" min="100" max="6000" step="1" placeholder="МГц" />
    <button data-viewact="start">▶ дивитись</button>
    <button data-viewact="stop"${active ? '' : ' disabled'}>■ свіп</button>
    <span class="view-badge">${escapeHtml(viewCaption(view))}</span>
    ${view && view.error ? `<span class="view-err">${escapeHtml(view.error)}</span>` : ''}`);
  return row;
}
```

In `bandCell`, replace the `if (tunable && range.low_mhz != null) { ... }` block with (every band
canvas gets the range; only 5.8G keeps the RX5808 `tunable` marker):

```js
  if (range.low_mhz != null) {
    line.dataset.lowMhz = range.low_mhz;
    line.dataset.highMhz = range.high_mhz;
    line.classList.add('freqpick');                  // click -> fill the SDR view field
    if (tunable) line.classList.add('tunable');      // 5.8G: click also tunes the RX5808
  }
```

In `detectionTable`, change the first row cell from `<td>${isNew ? '⚠' : ''}</td>` to:

```js
      <td>${isNew ? '⚠' : ''}<button class="tile-btn" data-viewfreq="${Number(d.center_mhz)}" title="Дивитись цю частоту (SDR)">▶</button></td>
```

- [ ] **Step 4: Wire `app.js`**

In the `spectrumPanel` click handler, REPLACE the `canvas.tunable` block with:

```js
  // Click a spectrum chart -> fill the SDR view frequency field; on 5.8G also
  // tune the RX5808 to the nearest channel (previous behavior kept).
  const canvas = e.target.closest('canvas.freqpick');
  if (canvas && sid) {
    const rect = canvas.getBoundingClientRect();
    const lo = Number(canvas.dataset.lowMhz);
    const hi = Number(canvas.dataset.highMhz);
    const x = Math.min(rect.width, Math.max(0, e.clientX - rect.left));
    const freq = lo + (x / rect.width) * (hi - lo);
    const inp = scanBlock.querySelector('.view-freq');
    if (inp) inp.value = String(Math.round(freq));
    if (canvas.classList.contains('tunable')) {
      const ch = nearestRxChannel(freq);
      if (ch) scanClient.publishCommand(sid, { mode: 'manual', channel: ch.name });
    }
    return;
  }
  // SDR view start/stop buttons
  const vbtn = e.target.closest('[data-viewact]');
  if (vbtn && sid) {
    if (vbtn.dataset.viewact === 'stop') {
      scanClient.publishView(sid, 'stop');
    } else {
      const inp = scanBlock.querySelector('.view-freq');
      const f = Number(inp && inp.value);
      if (Number.isFinite(f) && f >= 100 && f <= 6000) scanClient.publishView(sid, 'start', f);
    }
    return;
  }
  // ▶ on a detection row -> start the SDR view at that frequency
  const vfreq = e.target.closest('[data-viewfreq]');
  if (vfreq && sid) {
    scanClient.publishView(sid, 'start', Number(vfreq.dataset.viewfreq));
    return;
  }
```

In `renderScan()`, wrap the `renderSpectrum(...)` call to preserve typed frequencies across the
full re-render (the panel is rebuilt on every SSE/MQTT tick):

```js
  const typed = {};
  for (const inp of spectrumPanel.querySelectorAll('[data-scanner-id] .view-freq')) {
    const b = inp.closest('[data-scanner-id]');
    if (b && inp.value) typed[b.dataset.scannerId] = inp.value;
  }
  renderSpectrum(spectrumPanel, scanners, store, new Set(newKeys));
  for (const [vid, val] of Object.entries(typed)) {
    const inp = spectrumPanel.querySelector(`[data-scanner-id="${vid}"] .view-freq`);
    if (inp && !inp.value) inp.value = val;
  }
```

- [ ] **Step 5: Styles** — append to `dashboard/public/styles.css`:

```css
/* SDR live-view controls (scanner block) */
.sdr-view-ctl { display:flex; gap:.4rem; align-items:center; margin:.3rem 0; font-size:.8rem; color:#cbd5e1; flex-wrap:wrap; }
.sdr-view-ctl .view-freq { width:5.5rem; background:#0c1118; color:#cfd6e0; border:1px solid var(--line); border-radius:4px; font:inherit; padding:.15rem .3rem; }
.sdr-view-ctl button { font-size:.75rem; background:#0c1118; color:#cfd6e0; border:1px solid var(--line); border-radius:4px; cursor:pointer; padding:.15rem .5rem; }
.sdr-view-ctl button:disabled { opacity:.4; cursor:default; }
.sdr-view-ctl .view-badge { color:#39d0ff; font-weight:600; }
.sdr-view-ctl .view-err { color:#f87171; }
.band-cell .chart-line.freqpick { cursor:crosshair; }
```

- [ ] **Step 6: Syntax checks + full node suite**

Run: `node --check dashboard/public/app.js && node --check dashboard/public/spectrum.js && npm test`
Expected: node suite all green (105 → 108).

- [ ] **Step 7: Commit**

```bash
git add dashboard/public/spectrum.js dashboard/public/app.js dashboard/public/styles.css test/spectrum.test.js
git commit -m "feat(view): scanner-block SDR view controls, canvas freq-pick, detection ▶"
```

---

## Deploy & live acceptance (after merge)

1. **Server (dashboard UI):** surgical update — `sudo -u andriy git -C /home/andriy/fpv-video-stream pull --ff-only`, `docker compose build dashboard`, `docker compose up -d --no-deps dashboard`. (If pull fails with `failed to write object`: `chown -R andriy:andriy .git` — known gotcha.)
2. **Register the pseudo-camera:** dashboard ➕ → id `hackrf-view`, тип «Камера», назва «SDR перегляд (hackrf)» → copy the RTSP push command/URL.
3. **Pi 5** (`andriy@192.168.1.204`): `git -C /opt/fpv-video-stream pull`; `command -v ffmpeg || sudo apt install -y ffmpeg`; drop-in `/etc/systemd/system/fpv-scan.service.d/view.conf`:
   ```ini
   [Service]
   Environment=VIEW_ENABLED=1
   Environment=VIEW_PUSH_URL=rtsp://hackrf-view:<publish_pass>@10.8.0.1:8554/hackrf-view
   ```
   (never overwrite the main unit file — it is hand-diverged), `sudo systemctl daemon-reload && sudo systemctl restart fpv-scan`; журнал має показати `SDR view mode enabled`.
4. **Acceptance:** dashboard scanner block → поле `947` → «▶ дивитись» → журнал Pi: `entering SDR view @ 947.0 MHz`; плитка `hackrf-view` стає ONLINE і показує живий демод (шум/GSM-смуги — антена 5.8 поки бачить лише шум); спектр сканера завмирає + бейдж «▶ 947 МГц до HH:MM». «■ свіп» → плитка OFFLINE, спектр оновлюється знову. Перевірити таймаут: тимчасово `VIEW_MAX_S=60` у drop-in → рестарт → view сам гасне за хвилину.

## Spec-coverage self-check (done while writing this plan)

- Benchmark gate first — Task 1 ✓ (STOP-умова прописана)
- `VIEW_*` env, PAL/NTSC heights, gains reuse — Tasks 2–4 ✓
- rxcmd `view` routing + retained `fpv/<id>/view`, non-retained commands — Tasks 5, 8 ✓
- ViewController: validation 100–6000, stop/timeout/error, reset_hackrf, sweep resume — Tasks 6, 7 ✓
- Reader-thread mailbox (no USB stall), best-effort fps (`select_frames` + `FramePacer`), PAL fallback on noise — Tasks 3, 4 ✓
- Pseudo-camera path (zero server code) — Deploy §2 ✓
- UI: view row, canvas fills field (5.8 keeps RX5808 tune), detection ▶, badge — Task 9 ✓
- Tests: python (routing, state machine, builders, synth-IQ chunks, fake-popen run_stream) + node (reducer, builder, caption) ✓; live acceptance in Deploy ✓
