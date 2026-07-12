# FPV video TX from file (bladeRF) — Phase-0 spike — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A standalone CLI that renders a video file into a loopable SC16_Q11 IQ `.bin` (reusing `agent/video/synth.py`) and loop-streams it via bladeRF TX, so the operator can prove — on the RX5808/grabber — that a synthesized FM FPV-video signal is received.

**Architecture:** Pre-render + loop. `render.py` reuses synth (`make_cvbs`+`fm_modulate`) + a new SC16_Q11 quantizer; `bladerf_tx.py` mirrors the RX radio but on `CHANNEL_TX(0)` with `sync_tx` (plumbing from the `feat/bladerf-video-relay` spike `relay_spike.py`); `main.py` is the CLI. No systemd/UI/sweep-agent integration (that's Phase-A).

**Tech Stack:** Python 3 + numpy, ffmpeg (decode), cffi/libbladeRF (TX), pytest.

## Global Constraints

- **Phase-0 spike only.** New package `agent/tx/` (standalone CLI). NO changes to the sweep agent, dashboard, server, systemd, MQTT, or ACL. Not deployed as a service.
- Reuse `agent/video/synth.py` (`make_cvbs`, `fm_modulate`) — do NOT reimplement the CVBS/FM DSP.
- IQ format = bladeRF **SC16_Q11** (int16, ×2047, interleaved I/Q). The RX `iq_from_sc16q11` (`agent/scan/bladerf_source.py`) divides by 2048 — the round-trip is scale-approximate (2047/2048), tests use a tolerance.
- bladeRF TX plumbing mirrors `open_bladerf_view_radio` but `CHANNEL_TX(0)` / `ChannelLayout.TX_X1` / `sync_tx` / `enable_module(tx,…)` (reference: `feat/bladerf-video-relay:agent/scan/tools/relay_spike.py`). `open_bladerf_tx_radio` is the ONLY function importing `bladerf` and is NOT unit-tested.
- The RX5808/grabber feasibility gate is MANUAL (hardware); it is the acceptance, not an automated test.
- `deviation_hz`, `fs`, `gain`, `vbi_lines`, CVBS sync fidelity are gate-tuning knobs (defaults are starting points).
- pytest for the pure pieces must be green each task: `python -m pytest agent/tx/tests -q` (from repo root).
- Commit footer:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01JLJdXSvLMBojY7w2dcUwuN
  ```
- Branch: `feat/fpv-tx-generator` (spec committed there).

## File structure
- Create `agent/tx/__init__.py` (empty), `agent/tx/conftest.py` (sys.path: tx + ../video + ../scan).
- Create `agent/tx/render.py` — `to_sc16q11`, `frame_to_iq`, `build_ffmpeg_decode_cmd`, `render`.
- Create `agent/tx/bladerf_tx.py` — `transmit_loop`, `BladeRfTxRadio`, `open_bladerf_tx_radio`.
- Create `agent/tx/main.py` — CLI (`render` / `transmit`).
- Tests: `agent/tx/tests/test_render.py`, `agent/tx/tests/test_bladerf_tx.py`.

---

### Task 1: `agent/tx` package + `render.py` (pure DSP glue)

**Files:**
- Create: `agent/tx/__init__.py`, `agent/tx/conftest.py`, `agent/tx/render.py`, `agent/tx/tests/__init__.py`
- Test: `agent/tx/tests/test_render.py`

**Interfaces:**
- Produces: `to_sc16q11(iq)->bytes`; `frame_to_iq(frame, standard, fs, deviation_hz, interlaced=True, vbi_lines=0)->bytes`; `build_ffmpeg_decode_cmd(path, fps, width, height)->list[str]`.

- [ ] **Step 1: conftest (sys.path) — create it first so tests import**

Create `agent/tx/conftest.py`:
```python
import os
import sys
# tx (own modules) + ../video (synth) + ../scan (iq_from_sc16q11 for the round-trip test)
_HERE = os.path.dirname(__file__)
for _p in (_HERE, os.path.join(_HERE, "..", "video"), os.path.join(_HERE, "..", "scan")):
    _ap = os.path.abspath(_p)
    if _ap not in sys.path:
        sys.path.insert(0, _ap)
```
Create empty `agent/tx/__init__.py` and `agent/tx/tests/__init__.py`.

- [ ] **Step 2: Write the failing tests**

Create `agent/tx/tests/test_render.py`:
```python
import numpy as np
from render import to_sc16q11, frame_to_iq, build_ffmpeg_decode_cmd
from bladerf_source import iq_from_sc16q11


def test_to_sc16q11_scales_and_interleaves():
    raw = to_sc16q11(np.array([1 + 0j, 0 + 1j], dtype=np.complex128))
    vals = np.frombuffer(raw, dtype=np.int16)
    assert list(vals) == [2047, 0, 0, 2047]           # ×2047, interleaved I/Q, clipped


def test_to_sc16q11_clips_and_roundtrips():
    iq = np.array([1.5 + 0j, -2.0 + 0.5j], dtype=np.complex128)   # 1.5 clips to 1.0 range
    dec = iq_from_sc16q11(to_sc16q11(iq))
    assert dec.dtype == np.complex64
    assert np.allclose(dec, np.array([1.0 + 0j, -1.0 + 0.5j]), atol=1e-3)  # 2047/2048 tolerance


def test_frame_to_iq_length_and_nonconstant():
    # a 32x32 checker → one PAL frame of IQ; length = round(spl*lines)*4 bytes; not a constant tone
    frame = ((np.indices((32, 32)).sum(axis=0) % 2) * 255).astype(np.uint8)
    raw = frame_to_iq(frame, "PAL", fs=4e6, deviation_hz=1e6, interlaced=True, vbi_lines=2)
    iq = iq_from_sc16q11(raw)
    from standard import LINE_HZ, LINES
    spl = 4e6 / LINE_HZ["PAL"]
    assert len(iq) == int(round(spl * LINES["PAL"]))   # one frame of samples
    assert np.std(np.abs(np.diff(np.angle(iq)))) > 0   # FM phase actually varies (picture modulated)


def test_build_ffmpeg_decode_cmd():
    cmd = build_ffmpeg_decode_cmd("/clip.mp4", fps=25, width=640, height=512)
    assert cmd[0] == "ffmpeg" and cmd[-1] == "-"
    assert "/clip.mp4" in cmd
    vf = cmd[cmd.index("-vf") + 1]
    assert "fps=25" in vf and "scale=640:512" in vf and "format=gray" in vf
    assert "rawvideo" in cmd
```

- [ ] **Step 3: Run to verify failure**

Run: `python -m pytest agent/tx/tests/test_render.py -v`
Expected: FAIL — `No module named 'render'`.

- [ ] **Step 4: Implement `render.py`**

Create `agent/tx/render.py`:
```python
"""Render a video file into a loopable SC16_Q11 IQ .bin for bladeRF FPV-video TX.

Reuses agent/video/synth.py for the CVBS + FM-modulation DSP; the only new DSP here
is the SC16_Q11 quantizer (bladeRF TX wire format)."""
import logging
import subprocess

import numpy as np

from synth import make_cvbs, fm_modulate       # agent/video (on sys.path via conftest / main)

LOG = logging.getLogger("tx.render")


def to_sc16q11(iq) -> bytes:
    """Complex IQ (unit-scale) -> bladeRF SC16_Q11 interleaved int16 bytes (×2047, clipped)."""
    iq = np.asarray(iq, dtype=np.complex128)
    i = np.clip(np.round(iq.real * 2047.0), -2048, 2047).astype(np.int16)
    q = np.clip(np.round(iq.imag * 2047.0), -2048, 2047).astype(np.int16)
    out = np.empty(2 * len(iq), dtype=np.int16)
    out[0::2] = i
    out[1::2] = q
    return out.tobytes()


def frame_to_iq(frame_gray, standard, fs, deviation_hz, interlaced=True, vbi_lines=0) -> bytes:
    """One grayscale frame (uint8 h×w) -> one frame of SC16_Q11 IQ (CVBS -> FM -> quantize)."""
    img = np.asarray(frame_gray, dtype=np.float64) / 255.0
    bb = make_cvbs(standard, img, fs, frames=1, interlaced=interlaced, vbi_lines=vbi_lines)
    iq = fm_modulate(bb, fs, deviation_hz)
    return to_sc16q11(iq)


def build_ffmpeg_decode_cmd(path, fps, width, height):
    """ffmpeg argv: decode <path> to raw gray frames (width×height @ fps) on stdout."""
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", path,
            "-vf", f"fps={fps},scale={width}:{height},format=gray",
            "-f", "rawvideo", "-"]


def render(path, out_bin, standard="PAL", fs=20_000_000.0, deviation_hz=4_000_000.0,
           width=640, height=512, fps=25, max_secs=3.0, interlaced=True, vbi_lines=6,
           popen=None):
    """Decode `path` with ffmpeg and write frame-by-frame SC16_Q11 IQ into `out_bin`,
    up to max_secs of the clip. Returns (frames_written, bytes_written)."""
    popen = popen or subprocess.Popen
    frame_bytes = width * height
    max_frames = int(round(max_secs * fps))
    cap = popen(build_ffmpeg_decode_cmd(path, fps, width, height),
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    frames = 0
    written = 0
    try:
        with open(out_bin, "wb") as out:
            while frames < max_frames:
                raw = cap.stdout.read(frame_bytes)
                if not raw or len(raw) < frame_bytes:
                    break                                    # EOF / short clip
                frame = np.frombuffer(raw, dtype=np.uint8).reshape(height, width)
                iqb = frame_to_iq(frame, standard, fs, deviation_hz, interlaced, vbi_lines)
                out.write(iqb)
                frames += 1
                written += len(iqb)
    finally:
        try:
            cap.kill(); cap.wait(timeout=5)
        except Exception:
            pass
    LOG.info("render: %d frames, %d bytes -> %s", frames, written, out_bin)
    return frames, written
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest agent/tx/tests/test_render.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add agent/tx/__init__.py agent/tx/conftest.py agent/tx/render.py agent/tx/tests/
git commit -m "feat(tx): render video->SC16_Q11 IQ (reuses synth CVBS+FM)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JLJdXSvLMBojY7w2dcUwuN"
```

---

### Task 2: `bladerf_tx.py` (TX radio + loop streamer)

**Files:**
- Create: `agent/tx/bladerf_tx.py`
- Test: `agent/tx/tests/test_bladerf_tx.py`

**Interfaces:**
- Produces: `transmit_loop(radio, iq_path, block_bytes, stop_check)`; `BladeRfTxRadio` (`write`/`set_frequency`/`close`); `open_bladerf_tx_radio(freq_hz, fs_hz, gain_db, bandwidth_hz)` (bladeRF-touching, not unit-tested).

- [ ] **Step 1: Write the failing tests**

Create `agent/tx/tests/test_bladerf_tx.py`:
```python
from bladerf_tx import transmit_loop


class _FakeRadio:
    def __init__(self):
        self.writes = []
    def write(self, b):
        self.writes.append(bytes(b))


def test_transmit_loop_streams_blocks_then_wraps_and_stops(tmp_path):
    p = tmp_path / "iq.bin"
    p.write_bytes(bytes(range(12)))              # 12 bytes = 3 blocks of 4
    radio = _FakeRadio()
    calls = {"n": 0}
    def stop():
        calls["n"] += 1
        return calls["n"] > 5                    # allow 5 blocks
    transmit_loop(radio, str(p), block_bytes=4, stop_check=stop)
    assert len(radio.writes) == 5
    assert radio.writes[0] == bytes([0, 1, 2, 3])
    assert radio.writes[1] == bytes([4, 5, 6, 7])
    assert radio.writes[2] == bytes([8, 9, 10, 11])
    assert radio.writes[3] == bytes([0, 1, 2, 3])   # wrapped to file start (seamless loop)
    assert radio.writes[4] == bytes([4, 5, 6, 7])


def test_transmit_loop_seamless_wrap_across_a_block(tmp_path):
    p = tmp_path / "iq.bin"
    p.write_bytes(bytes(range(10)))              # 10 bytes, block 4 -> tail of 2 wraps
    radio = _FakeRadio()
    calls = {"n": 0}
    def stop():
        calls["n"] += 1
        return calls["n"] > 3
    transmit_loop(radio, str(p), block_bytes=4, stop_check=stop)
    assert radio.writes[0] == bytes([0, 1, 2, 3])
    assert radio.writes[1] == bytes([4, 5, 6, 7])
    assert radio.writes[2] == bytes([8, 9, 0, 1])   # tail(8,9) + wrap fill(0,1): no gap
    assert len(radio.writes) == 3


def test_transmit_loop_empty_file_returns(tmp_path):
    p = tmp_path / "iq.bin"; p.write_bytes(b"")
    radio = _FakeRadio()
    transmit_loop(radio, str(p), block_bytes=4, stop_check=lambda: False)
    assert radio.writes == []                    # empty file: no writes, no infinite loop
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest agent/tx/tests/test_bladerf_tx.py -v`
Expected: FAIL — `No module named 'bladerf_tx'`.

- [ ] **Step 3: Implement `bladerf_tx.py`**

Create `agent/tx/bladerf_tx.py`:
```python
"""bladeRF TX for the FPV-video generator: loop-stream a pre-rendered SC16_Q11 IQ .bin.

open_bladerf_tx_radio is the only bladeRF-touching code (mirrors open_bladerf_view_radio,
but CHANNEL_TX + sync_tx; plumbing from feat/bladerf-video-relay:relay_spike.py)."""
import logging

LOG = logging.getLogger("tx.bladerf")

TX_STREAM_TIMEOUT_MS = 3500


def transmit_loop(radio, iq_path, block_bytes, stop_check):
    """Stream iq_path to radio.write() in block_bytes chunks, looping seamlessly at EOF
    (a short tail is filled from the file start — no gap). Runs until stop_check() is True.
    An empty file returns immediately."""
    with open(iq_path, "rb") as f:
        while not stop_check():
            buf = f.read(block_bytes)
            if not buf:
                f.seek(0)
                buf = f.read(block_bytes)
                if not buf:
                    return                       # empty file
            if len(buf) < block_bytes:
                f.seek(0)
                buf = buf + f.read(block_bytes - len(buf))   # seamless wrap fill
            radio.write(buf)


class BladeRfTxRadio:
    """Open TX handle: write(bytes) -> sync_tx one block; set_frequency; close. Radio/channel
    injected (open_bladerf_tx_radio) so this class imports nothing from `bladerf`."""

    def __init__(self, radio, channel, block_samples):
        self._radio = radio
        self._ch = channel
        self._block_samples = int(block_samples)

    def write(self, buf):
        # buf is block_samples*4 SC16_Q11 bytes; sync_tx wants a mutable buffer + sample count.
        self._radio.sync_tx(bytearray(buf), len(buf) // 4)

    def set_frequency(self, hz):
        self._radio.set_frequency(self._ch, int(hz))

    def close(self):
        try:
            self._radio.enable_module(self._ch, False)
        except Exception:
            LOG.exception("bladeRF TX disable failed")
        finally:
            try:
                self._radio.close()
            except Exception:
                LOG.exception("bladeRF TX close failed")


def open_bladerf_tx_radio(freq_hz, fs_hz, gain_db, bandwidth_hz, block_samples=32768) -> BladeRfTxRadio:
    """Open the first bladeRF configured for continuous TX (SC16_Q11). Only bladeRF-touching fn."""
    import bladerf
    from bladerf import _bladerf
    radio = bladerf.BladeRF()
    ch = bladerf.CHANNEL_TX(0)
    radio.set_sample_rate(ch, int(fs_hz))
    radio.set_bandwidth(ch, int(bandwidth_hz))
    radio.set_frequency(ch, int(freq_hz))
    radio.set_gain(ch, int(gain_db))
    radio.sync_config(
        layout=_bladerf.ChannelLayout.TX_X1, fmt=_bladerf.Format.SC16_Q11,
        num_buffers=16, buffer_size=8192, num_transfers=8, stream_timeout=TX_STREAM_TIMEOUT_MS,
    )
    radio.enable_module(ch, True)
    return BladeRfTxRadio(radio, ch, block_samples)
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest agent/tx/tests/test_bladerf_tx.py -v`
Expected: PASS (3 tests). Then `python -m pytest agent/tx/tests -q` (both files green).

- [ ] **Step 5: Commit**

```bash
git add agent/tx/bladerf_tx.py agent/tx/tests/test_bladerf_tx.py
git commit -m "feat(tx): bladeRF TX radio + seamless loop streamer

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JLJdXSvLMBojY7w2dcUwuN"
```

---

### Task 3: `main.py` CLI (render / transmit)

**Files:**
- Create: `agent/tx/main.py`
- Test: (none — argparse wiring; verified by `python -m py_compile` + `--help` smoke)

**Interfaces:**
- Consumes: `render` (Task 1), `open_bladerf_tx_radio` + `transmit_loop` (Task 2).

- [ ] **Step 1: Implement `main.py`**

Create `agent/tx/main.py`:
```python
"""FPV video-from-file TX generator (bladeRF), Phase-0 spike CLI.

  render:   python -m main render <file> <out.bin> [--standard PAL] [--fs 20e6] [--dev 4e6]
                                  [--w 640] [--h 512] [--fps 25] [--secs 3] [--vbi 6]
  transmit: python -m main transmit <iq.bin> <freq_mhz> [--fs 20e6] [--gain 30]  (Ctrl-C stops)

Reuses agent/video/synth via render.py. TX loops the .bin seamlessly on <freq_mhz>."""
import argparse
import logging
import os
import signal
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "video"))

from render import render                       # noqa: E402
from bladerf_tx import open_bladerf_tx_radio, transmit_loop   # noqa: E402


def _render(a):
    frames, nbytes = render(a.file, a.out, standard=a.standard, fs=a.fs, deviation_hz=a.dev,
                            width=a.w, height=a.h, fps=a.fps, max_secs=a.secs, vbi_lines=a.vbi)
    secs = (nbytes / 4) / a.fs if a.fs else 0
    print(f"rendered {frames} frames, {nbytes} bytes ({secs:.2f}s of IQ @ {a.fs/1e6:.1f} MS/s) -> {a.out}")


def _transmit(a):
    print(f"⚠ TX on {a.freq_mhz} MHz @ {a.fs/1e6:.1f} MS/s gain={a.gain} — ensure you are authorized "
          f"(power/antenna/shielded bench). Ctrl-C to stop.")
    radio = open_bladerf_tx_radio(int(a.freq_mhz * 1e6), int(a.fs), a.gain, int(a.fs))
    stop = {"v": False}
    signal.signal(signal.SIGINT, lambda *_: stop.__setitem__("v", True))
    try:
        transmit_loop(radio, a.iq, block_bytes=32768 * 4, stop_check=lambda: stop["v"])
    finally:
        radio.close()
    print("TX stopped.")


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="fpv-tx")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("render")
    r.add_argument("file"); r.add_argument("out")
    r.add_argument("--standard", default="PAL"); r.add_argument("--fs", type=float, default=20e6)
    r.add_argument("--dev", type=float, default=4e6); r.add_argument("--w", type=int, default=640)
    r.add_argument("--h", type=int, default=512); r.add_argument("--fps", type=int, default=25)
    r.add_argument("--secs", type=float, default=3.0); r.add_argument("--vbi", type=int, default=6)
    r.set_defaults(fn=_render)

    t = sub.add_parser("transmit")
    t.add_argument("iq"); t.add_argument("freq_mhz", type=float)
    t.add_argument("--fs", type=float, default=20e6); t.add_argument("--gain", type=int, default=30)
    t.set_defaults(fn=_transmit)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke-check**

Run: `python -m py_compile agent/tx/main.py agent/tx/render.py agent/tx/bladerf_tx.py`
Expected: no output.
Run: `cd agent/tx && python main.py --help && python main.py render --help && cd ../..`
Expected: help text (no import error; the bladeRF import is lazy inside `open_bladerf_tx_radio`, so `--help` and `render` work without hardware).

- [ ] **Step 3: Full tx-suite green**

Run: `python -m pytest agent/tx/tests -q`
Expected: PASS (7 tests).

- [ ] **Step 4: Commit**

```bash
git add agent/tx/main.py
git commit -m "feat(tx): CLI — render + loop-transmit FPV video (Phase-0 spike)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JLJdXSvLMBojY7w2dcUwuN"
```

---

## Phase-0 hardware gate (MANUAL — the acceptance; not automated)
On the Pi (bladeRF), with the sweep unit stopped so the bladeRF is free:
1. `git pull`; `sudo systemctl stop fpv-scan` (free the bladeRF).
2. Render a short clip (use the venv python): `python -m tx.main render <clip.mp4> /tmp/iq.bin --secs 2` (or run from `agent/tx`).
3. Transmit on a 5.8 GHz FPV channel the RX5808 can tune: `sudo <venv>/python -m tx.main transmit /tmp/iq.bin 5865 --gain 30`.
4. Tune the RX5808 to 5865; watch the grabber stream. **Success = the clip's picture appears on the grabber** (even if rough).
5. If nothing: sweep `--dev` (≈2–8 MHz), `--fs`, `--gain`, `--vbi`, and CVBS sync fidelity, until it locks. If it never locks, document as a dead-end (like [[bladerf-relay-not-viable]]) with the deviation/sync/power findings.
6. Restore: Ctrl-C TX, `sudo systemctl start fpv-scan`.

## Self-review (spec coverage)
- ✅ Render video→SC16_Q11 IQ reusing synth (Task 1); SC16_Q11 quantizer + round-trip test.
- ✅ bladeRF TX radio (CHANNEL_TX/TX_X1/sync_tx, relay-spike plumbing) + seamless loop streamer (Task 2).
- ✅ CLI render/transmit with the gate-tuning knobs (Task 3); TX authorization warning.
- ✅ Standalone; no sweep-agent/dashboard/server/systemd/MQTT/ACL changes.
- ✅ pytest for pure pieces; the RX5808/grabber gate is the manual acceptance.
- ✅ Phase-A (mode/command/UI/arbitration) explicitly deferred.
