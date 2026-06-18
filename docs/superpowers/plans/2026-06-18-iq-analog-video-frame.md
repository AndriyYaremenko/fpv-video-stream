# Analog FPV Video — IQ → Frame → MQTT (`agent/video/`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A one-shot Pi CLI that loads a HackRF int8 IQ capture, confirms it is analog FPV video (PAL/NTSC), reconstructs a luma frame, and publishes a PNG thumbnail to `fpv/<scanner_id>/video` over MQTT.

**Architecture:** New flat-module package `agent/video/` that bootstraps the sibling `agent/scan/` onto `sys.path` to reuse `iq_from_int8` (IQ read) and the `MqttPublisher` machinery. Vectorized numpy DSP pipeline: `load_iq → fm_demod → lowpass → detect_standard (gate) → slice_lines → build_frame → pick_sharpest → render → publish_video_once`. A synthetic CVBS generator drives all tests with no hardware.

**Tech Stack:** Python 3, numpy, Pillow, paho-mqtt, pytest. Runs in the shared `agent/scan/.venv`. Reuses `agent/scan/{config,dweller,publisher}.py`.

**Reference spec:** `docs/superpowers/specs/2026-06-18-iq-analog-video-frame-design.md`

**Conventions for every task:**
- Flat imports (e.g. `from standard import LINE_HZ`) — the `conftest.py` from Task 1 puts both `agent/video` and `agent/scan` on `sys.path`.
- Run tests from the scan venv: `cd agent/scan && python -m pytest ../video/tests -q` (on the dev machine substitute the venv's python).
- Commit after each task with the shown message.

---

### Task 1: Scaffold the package, path shim, and dependency

**Files:**
- Create: `agent/video/__init__.py` (empty)
- Create: `agent/video/conftest.py`
- Create: `agent/video/tests/__init__.py` (empty)
- Create: `agent/video/tests/test_scaffold.py`
- Modify: `agent/scan/requirements.txt`

- [ ] **Step 1: Create the empty package markers**

Create `agent/video/__init__.py` and `agent/video/tests/__init__.py` as empty files.

- [ ] **Step 2: Create the path shim** (`agent/video/conftest.py`)

```python
import os
import sys

# Put agent/video (own flat modules) and agent/scan (reused config/dweller/publisher)
# on sys.path so flat imports work under pytest. Mirrors agent/scan/conftest.py.
_HERE = os.path.dirname(__file__)
for _p in (_HERE, os.path.join(_HERE, "..", "scan")):
    _ap = os.path.abspath(_p)
    if _ap not in sys.path:
        sys.path.insert(0, _ap)
```

- [ ] **Step 3: Add Pillow to the scan requirements** (`agent/scan/requirements.txt`)

The file currently is:
```
numpy>=1.26
paho-mqtt>=2.0
pytest>=8.0
```
Add a Pillow line so it becomes:
```
numpy>=1.26
paho-mqtt>=2.0
Pillow>=10.0
pytest>=8.0
```

- [ ] **Step 4: Write the scaffold test** (`agent/video/tests/test_scaffold.py`)

```python
def test_can_import_reused_scan_modules():
    # The conftest path shim must expose agent/scan's flat modules to agent/video.
    from dweller import iq_from_int8   # reused IQ reader
    import publisher                   # reused MqttPublisher home
    assert callable(iq_from_int8)
    assert hasattr(publisher, "MqttPublisher")
```

- [ ] **Step 5: Run the test**

Run: `cd agent/scan && python -m pytest ../video/tests/test_scaffold.py -q`
Expected: PASS (1 passed).

- [ ] **Step 6: Commit**

```bash
git add agent/video/__init__.py agent/video/conftest.py agent/video/tests/__init__.py agent/video/tests/test_scaffold.py agent/scan/requirements.txt
git commit -m "feat(video): scaffold agent/video package + path shim + Pillow dep"
```

---

### Task 2: `iqio.load_iq` — read an IQ file (reuse `iq_from_int8`)

**Files:**
- Create: `agent/video/iqio.py`
- Test: `agent/video/tests/test_iqio.py`

- [ ] **Step 1: Write the failing test** (`agent/video/tests/test_iqio.py`)

```python
import numpy as np
import pytest

from iqio import load_iq


def test_load_iq_reads_interleaved_int8(tmp_path):
    # I=64,Q=-64 repeated -> complex (0.5 - 0.5j) after /128 normalization.
    raw = np.array([64, -64, 64, -64], dtype=np.int8).tobytes()
    f = tmp_path / "cap.iq"
    f.write_bytes(raw)
    iq = load_iq(str(f))
    assert iq.shape == (2,)
    np.testing.assert_allclose(iq.real, [0.5, 0.5], atol=1e-6)
    np.testing.assert_allclose(iq.imag, [-0.5, -0.5], atol=1e-6)


def test_load_iq_rejects_empty_file(tmp_path):
    f = tmp_path / "empty.iq"
    f.write_bytes(b"")
    with pytest.raises(ValueError):
        load_iq(str(f))
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd agent/scan && python -m pytest ../video/tests/test_iqio.py -q`
Expected: FAIL (ModuleNotFoundError: No module named 'iqio').

- [ ] **Step 3: Implement** (`agent/video/iqio.py`)

```python
from dweller import iq_from_int8   # reuse the scan service's int8 IQ reader


def load_iq(path):
    """Load a HackRF int8 interleaved (I,Q,I,Q...) capture as a complex array."""
    with open(path, "rb") as f:
        raw = f.read()
    if not raw:
        raise ValueError(f"empty IQ file: {path}")
    return iq_from_int8(raw)
```

- [ ] **Step 4: Run it to confirm it passes**

Run: `cd agent/scan && python -m pytest ../video/tests/test_iqio.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add agent/video/iqio.py agent/video/tests/test_iqio.py
git commit -m "feat(video): load_iq reuses iq_from_int8"
```

---

### Task 3: `demod.fm_demod` + `lowpass`

**Files:**
- Create: `agent/video/demod.py`
- Test: `agent/video/tests/test_demod.py`

- [ ] **Step 1: Write the failing test** (`agent/video/tests/test_demod.py`)

```python
import numpy as np

from demod import fm_demod, lowpass


def test_fm_demod_recovers_constant_tone():
    # A complex tone at frequency f has constant instantaneous frequency.
    fs = 1_000_000.0
    f = 50_000.0
    n = 4096
    t = np.arange(n) / fs
    iq = np.exp(1j * 2 * np.pi * f * t)
    bb = fm_demod(iq)
    # After de-carrier (median subtract) a pure tone is ~flat near zero.
    assert bb.shape == (n - 1,)
    assert np.std(bb) < 1e-3


def test_fm_demod_tracks_changing_frequency():
    # A signal whose frequency steps up halfway should demod higher in the 2nd half.
    fs = 1_000_000.0
    n = 8000
    t = np.arange(n) / fs
    inst = np.where(t < t[n // 2], 40_000.0, 120_000.0)
    phase = 2 * np.pi * np.cumsum(inst) / fs
    iq = np.exp(1j * phase)
    bb = fm_demod(iq)
    assert bb[: n // 2 - 10].mean() < bb[n // 2 + 10 :].mean()


def test_lowpass_attenuates_high_frequency():
    fs = 1_000_000.0
    n = 8192
    t = np.arange(n) / fs
    dc = 0.7
    hf = np.sin(2 * np.pi * 200_000.0 * t)   # well above a 20 kHz cutoff
    x = dc + hf
    y = lowpass(x, fs, cutoff_hz=20_000.0)
    assert y.shape == x.shape
    assert abs(y.mean() - dc) < 0.05          # DC preserved
    assert np.std(y) < 0.3 * np.std(hf)       # HF strongly attenuated
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd agent/scan && python -m pytest ../video/tests/test_demod.py -q`
Expected: FAIL (No module named 'demod').

- [ ] **Step 3: Implement** (`agent/video/demod.py`)

```python
import numpy as np


def fm_demod(iq):
    """FM-demodulate: instantaneous frequency via phase differencing, de-carriered.

    angle(iq[n] * conj(iq[n-1])) is the per-sample phase advance; subtracting the
    median removes the residual carrier offset. Returns a real baseband (len N-1).
    """
    if len(iq) < 2:
        return np.zeros(0, dtype=np.float64)
    inst = np.angle(iq[1:] * np.conj(iq[:-1]))
    return inst - np.median(inst)


def lowpass(x, fs, cutoff_hz):
    """Moving-average low-pass (cumsum), length-preserving, edge-padded.

    Window ~ fs/cutoff. Vectorized; no per-sample Python loop.
    """
    x = np.asarray(x, dtype=np.float64)
    win = int(round(fs / cutoff_hz)) if cutoff_hz > 0 else 1
    if win <= 1 or win >= len(x):
        return x.copy()
    c = np.cumsum(np.insert(x, 0, 0.0))
    ma = (c[win:] - c[:-win]) / win            # length len(x) - win + 1
    pad_total = len(x) - len(ma)
    pad_left = pad_total // 2
    pad_right = pad_total - pad_left
    return np.concatenate([np.full(pad_left, ma[0]), ma, np.full(pad_right, ma[-1])])
```

- [ ] **Step 4: Run it to confirm it passes**

Run: `cd agent/scan && python -m pytest ../video/tests/test_demod.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add agent/video/demod.py agent/video/tests/test_demod.py
git commit -m "feat(video): fm_demod + cumsum lowpass"
```

---

### Task 4: `standard.detect_standard` + format constants (gate)

**Files:**
- Create: `agent/video/standard.py`
- Test: `agent/video/tests/test_standard.py`

**Note:** `LINE_HZ` uses the *real* NTSC line rate 15734 (525 lines at 29.97 fps), not 525×30=15750 — this is the single source of truth shared by the synth (Task 5) and slicer (Task 6).

- [ ] **Step 1: Write the failing test** (`agent/video/tests/test_standard.py`)

```python
import numpy as np

from standard import detect_standard, LINE_HZ, LINES


def _tone(line_hz, fs, n, noise=0.02, seed=0):
    # Line-rate fundamental + 2nd harmonic (mimics sync pulse train) + noise.
    rng = np.random.default_rng(seed)
    t = np.arange(n) / fs
    sig = np.sin(2 * np.pi * line_hz * t) + 0.5 * np.sin(2 * np.pi * 2 * line_hz * t)
    return sig + rng.normal(0, noise, n)


def test_detects_pal():
    fs = 8_000_000.0
    bb = _tone(LINE_HZ["PAL"], fs, 400_000)
    res = detect_standard(bb, fs)
    assert res.standard == "PAL"
    assert res.line_hz == LINE_HZ["PAL"]
    assert res.sync_snr_db >= 10.0


def test_detects_ntsc():
    fs = 8_000_000.0
    bb = _tone(LINE_HZ["NTSC"], fs, 400_000)
    res = detect_standard(bb, fs)
    assert res.standard == "NTSC"
    assert res.line_hz == LINE_HZ["NTSC"]


def test_pure_noise_is_not_video():
    fs = 8_000_000.0
    rng = np.random.default_rng(1)
    bb = rng.normal(0, 1.0, 400_000)
    res = detect_standard(bb, fs)
    assert res.standard is None


def test_forced_standard_skips_autoselect():
    fs = 8_000_000.0
    bb = _tone(LINE_HZ["PAL"], fs, 400_000)
    res = detect_standard(bb, fs, forced="pal")
    assert res.standard == "PAL"


def test_format_tables_are_consistent():
    assert set(LINE_HZ) == set(LINES) == {"PAL", "NTSC"}
    assert LINE_HZ["PAL"] == 15625 and LINES["PAL"] == 625
    assert LINE_HZ["NTSC"] == 15734 and LINES["NTSC"] == 525
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd agent/scan && python -m pytest ../video/tests/test_standard.py -q`
Expected: FAIL (No module named 'standard').

- [ ] **Step 3: Implement** (`agent/video/standard.py`)

```python
from dataclasses import dataclass

import numpy as np

# Single source of truth for line rates / line counts (shared by synth + slicer).
# NTSC uses the real 15734 Hz line rate (525 lines @ 29.97 fps), not 525*30.
LINE_HZ = {"PAL": 15625, "NTSC": 15734}
LINES = {"PAL": 625, "NTSC": 525}

_EPS = 1e-12


@dataclass
class StdResult:
    standard: object        # "PAL" | "NTSC" | None
    line_hz: int
    sync_snr_db: float      # SNR of the line fundamental (best measured, even if rejected)
    harm_snr_db: float      # SNR of the 2nd harmonic


def _tone_snr_db(spec, freqs, fs, n, f0):
    """Peak-vs-local-floor SNR (dB) of a tone at f0 in an rfft magnitude spectrum."""
    bin_hz = fs / n
    k = int(round(f0 / bin_hz))
    if k <= 0 or k >= len(spec):
        return -np.inf
    lo, hi = max(0, k - 2), min(len(spec), k + 3)
    peak = float(spec[lo:hi].max())
    flo, fhi = max(0, k - 250), min(len(spec), k + 250)
    floor = float(np.median(spec[flo:fhi])) + _EPS
    return 20.0 * np.log10((peak + _EPS) / floor)


def detect_standard(baseband, fs, forced=None, line_snr_db=10.0, harm_snr_db=6.0):
    """Gate on the line-sync tone. Returns StdResult; standard=None means not_video."""
    bb = np.asarray(baseband, dtype=np.float64)
    n = len(bb)
    if n < 1024:
        return StdResult(None, 0, -np.inf, -np.inf)
    spec = np.abs(np.fft.rfft(bb * np.hanning(n)))
    freqs = np.fft.rfftfreq(n, 1.0 / fs)
    candidates = [forced.upper()] if forced else ["PAL", "NTSC"]
    best = None
    best_any = StdResult(None, 0, -np.inf, -np.inf)
    for std in candidates:
        f0 = LINE_HZ[std]
        s1 = _tone_snr_db(spec, freqs, fs, n, f0)
        s2 = _tone_snr_db(spec, freqs, fs, n, 2 * f0)
        if s1 > best_any.sync_snr_db:
            best_any = StdResult(None, f0, s1, s2)   # track strongest for logging
        if s1 >= line_snr_db and s2 >= harm_snr_db:
            if best is None or s1 > best.sync_snr_db:
                best = StdResult(std, f0, s1, s2)
    return best if best is not None else best_any
```

- [ ] **Step 4: Run it to confirm it passes**

Run: `cd agent/scan && python -m pytest ../video/tests/test_standard.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add agent/video/standard.py agent/video/tests/test_standard.py
git commit -m "feat(video): detect_standard line-sync gate + format tables"
```

---

### Task 5: `synth` — synthetic CVBS → FM → int8 (test fixtures)

**Files:**
- Create: `agent/video/synth.py`
- Test: `agent/video/tests/test_synth.py`

- [ ] **Step 1: Write the failing test** (`agent/video/tests/test_synth.py`)

```python
import numpy as np

from synth import make_cvbs, fm_modulate, to_int8
from standard import detect_standard, LINE_HZ
from demod import fm_demod, lowpass
from iqio import load_iq


def _gradient(h=64, w=64):
    col = np.linspace(0.0, 1.0, h)[:, None]
    return np.tile(col, (1, w))            # vertical gradient (brightness by row)


def test_make_cvbs_has_line_rate_tone():
    fs = 8_000_000.0
    bb = make_cvbs("PAL", _gradient(), fs, frames=2)
    res = detect_standard(bb, fs)
    assert res.standard == "PAL"


def test_modulate_then_demod_roundtrips_through_pipeline():
    fs = 8_000_000.0
    bb = make_cvbs("NTSC", _gradient(), fs, frames=2)
    iq = fm_modulate(bb, fs, deviation_hz=2_000_000.0)
    rec = lowpass(fm_demod(iq), fs, cutoff_hz=5_000_000.0)
    # Demodulated baseband still carries the NTSC line tone.
    assert detect_standard(rec, fs).standard == "NTSC"


def test_to_int8_roundtrips_via_file(tmp_path):
    fs = 8_000_000.0
    bb = make_cvbs("PAL", _gradient(), fs, frames=1)
    iq = fm_modulate(bb, fs, deviation_hz=2_000_000.0)
    raw = to_int8(iq, noise_std=0.0)
    f = tmp_path / "cap.iq"
    f.write_bytes(raw)
    back = load_iq(str(f))
    assert back.shape == iq.shape
    # int8 quantization keeps the unit-circle samples close.
    assert np.mean(np.abs(back - iq)) < 0.02
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd agent/scan && python -m pytest ../video/tests/test_synth.py -q`
Expected: FAIL (No module named 'synth').

- [ ] **Step 3: Implement** (`agent/video/synth.py`)

```python
import numpy as np

from standard import LINE_HZ, LINES

# Composite levels (normalized): sync tip lowest, blanking, then active video.
_SYNC = 0.0
_BLANK = 0.30
_SYNC_FRAC = 0.075       # fraction of a line that is the sync pulse
_ACTIVE_START = 0.18     # picture starts after sync + back porch
_ACTIVE_END = 0.98


def make_cvbs(standard, image, fs, frames=1):
    """Build a real luma CVBS baseband (sync train + image) for `frames` frames.

    Vectorized: a phase accumulator over the whole signal places the sync pulse at
    the start of each line and maps the active window to image columns/rows.
    """
    image = np.asarray(image, dtype=np.float64)
    img_h, img_w = image.shape
    line_hz = LINE_HZ[standard]
    lines = LINES[standard]
    spl = fs / line_hz                       # samples per line (fractional)
    total = int(round(spl * lines * frames))

    n = np.arange(total)
    line_pos = n / spl                       # fractional line index over the whole run
    line_idx = np.floor(line_pos).astype(np.int64)
    phase = line_pos - line_idx              # 0..1 within the current line

    sig = np.full(total, _BLANK)
    sig[phase < _SYNC_FRAC] = _SYNC

    active = (phase >= _ACTIVE_START) & (phase < _ACTIVE_END)
    col_frac = (phase - _ACTIVE_START) / (_ACTIVE_END - _ACTIVE_START)
    col = np.clip((col_frac * img_w).astype(np.int64), 0, img_w - 1)
    row_in_frame = line_idx % lines
    vrow = np.clip((row_in_frame / lines * img_h).astype(np.int64), 0, img_h - 1)
    px = image[vrow, col]
    sig[active] = _BLANK + (1.0 - _BLANK) * px[active]
    return sig


def fm_modulate(baseband, fs, deviation_hz):
    """FM-modulate a real baseband to a complex IQ signal on the unit circle."""
    bb = np.asarray(baseband, dtype=np.float64)
    bb = bb - np.mean(bb)
    phase = 2 * np.pi * deviation_hz * np.cumsum(bb) / fs
    return np.exp(1j * phase)


def to_int8(iq, noise_std=0.0, seed=0):
    """Quantize complex IQ to interleaved int8 bytes, with optional AWGN."""
    iq = np.asarray(iq, dtype=np.complex128)
    if noise_std > 0:
        rng = np.random.default_rng(seed)
        iq = iq + rng.normal(0, noise_std, len(iq)) + 1j * rng.normal(0, noise_std, len(iq))
    i = np.clip(np.round(iq.real * 127.0), -128, 127).astype(np.int8)
    q = np.clip(np.round(iq.imag * 127.0), -128, 127).astype(np.int8)
    out = np.empty(2 * len(iq), dtype=np.int8)
    out[0::2] = i
    out[1::2] = q
    return out.tobytes()
```

- [ ] **Step 4: Run it to confirm it passes**

Run: `cd agent/scan && python -m pytest ../video/tests/test_synth.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add agent/video/synth.py agent/video/tests/test_synth.py
git commit -m "test(video): synthetic CVBS + FM modulator + int8 quantizer"
```

---

### Task 6: `frame` — slice_lines, build_frame, reconstruct_frames, pick_sharpest

**Files:**
- Create: `agent/video/frame.py`
- Test: `agent/video/tests/test_frame.py`

- [ ] **Step 1: Write the failing test** (`agent/video/tests/test_frame.py`)

```python
import numpy as np

from frame import slice_lines, build_frame, reconstruct_frames, pick_sharpest, laplacian_var
from synth import make_cvbs
from standard import LINES


def _gradient(h=120, w=120):
    return np.tile(np.linspace(0.0, 1.0, h)[:, None], (1, w))


def test_slice_lines_shapes_and_sync_at_col0():
    fs = 8_000_000.0
    bb = make_cvbs("PAL", _gradient(), fs, frames=1)
    rows = slice_lines(bb, fs, "PAL")
    assert rows.ndim == 2
    assert rows.shape[0] >= LINES["PAL"] - 2
    # Sync (lowest level) is rolled to the start: col 0 mean is the minimum.
    col_mean = rows.mean(axis=0)
    assert int(np.argmin(col_mean)) <= 2


def test_build_frame_width_and_no_nan():
    fs = 8_000_000.0
    bb = make_cvbs("PAL", _gradient(), fs, frames=1)
    rows = slice_lines(bb, fs, "PAL")
    frame = build_frame(rows, width=360)
    assert frame.shape[1] == 360
    assert np.isfinite(frame).all()


def test_reconstructed_frame_correlates_with_source():
    fs = 8_000_000.0
    src = _gradient(120, 120)
    bb = make_cvbs("PAL", src, fs, frames=2)
    frame = pick_sharpest(reconstruct_frames(bb, fs, "PAL", width=120))
    # Compare vertical structure: per-row brightness should ramp like the source.
    rec_profile = frame.mean(axis=1)
    rec_profile = np.interp(np.linspace(0, 1, 120),
                            np.linspace(0, 1, len(rec_profile)), rec_profile)
    src_profile = src.mean(axis=1)
    corr = np.corrcoef(rec_profile, src_profile)[0, 1]
    assert corr > 0.7


def test_pick_sharpest_prefers_high_variance():
    flat = np.full((40, 40), 0.5)
    sharp = np.random.default_rng(0).random((40, 40))
    assert pick_sharpest([flat, sharp]) is sharp
    assert laplacian_var(sharp) > laplacian_var(flat)
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd agent/scan && python -m pytest ../video/tests/test_frame.py -q`
Expected: FAIL (No module named 'frame').

- [ ] **Step 3: Implement** (`agent/video/frame.py`)

```python
import numpy as np

from standard import LINE_HZ, LINES


def slice_lines(baseband, fs, standard):
    """Slice the baseband into sync-aligned line rows: shape (n_lines, samples_per_line)."""
    bb = np.asarray(baseband, dtype=np.float64)
    spl = fs / LINE_HZ[standard]
    spl_i = int(round(spl))
    n = int(len(bb) // spl)
    if n < 2 or spl_i < 4:
        return np.zeros((0, max(spl_i, 1)))
    starts = (np.arange(n) * spl)[:, None]
    cols = np.arange(spl_i)[None, :]
    pos = (starts + cols).ravel()
    rows = np.interp(pos, np.arange(len(bb)), bb).reshape(n, spl_i)
    # Align sync (lowest mean column) to col 0.
    sync_col = int(np.argmin(rows.mean(axis=0)))
    return np.roll(rows, -sync_col, axis=1)


def build_frame(rows, width=720, blank_frac=0.18):
    """Drop sync+blanking, resample each active line to `width` px (vectorized)."""
    if rows.shape[0] == 0:
        return np.zeros((0, width))
    start = int(rows.shape[1] * blank_frac)
    active = rows[:, start:]
    src_w = active.shape[1]
    if src_w < 2:
        return np.zeros((rows.shape[0], width))
    ratio = (src_w - 1) / (width - 1) if width > 1 else 0.0
    pos = np.arange(width) * ratio
    lo = np.floor(pos).astype(int)
    hi = np.minimum(lo + 1, src_w - 1)
    frac = pos - lo
    return active[:, lo] * (1.0 - frac) + active[:, hi] * frac


def reconstruct_frames(baseband, fs, standard, width=720, blank_frac=0.18):
    """Slice into lines, chunk into frames of LINES[standard], build each frame."""
    rows = slice_lines(baseband, fs, standard)
    lines = LINES[standard]
    frames = []
    n_frames = rows.shape[0] // lines
    for f in range(n_frames):
        frames.append(build_frame(rows[f * lines:(f + 1) * lines], width, blank_frac))
    if not frames:                       # fewer than one full frame of lines
        frames.append(build_frame(rows, width, blank_frac))
    return frames


def laplacian_var(img):
    """Sharpness metric: variance of a 4-neighbour Laplacian."""
    lap = (-4.0 * img
           + np.roll(img, 1, 0) + np.roll(img, -1, 0)
           + np.roll(img, 1, 1) + np.roll(img, -1, 1))
    return float(lap.var())


def pick_sharpest(frames):
    """Return the frame with the highest Laplacian variance."""
    return max(frames, key=laplacian_var)
```

- [ ] **Step 4: Run it to confirm it passes**

Run: `cd agent/scan && python -m pytest ../video/tests/test_frame.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add agent/video/frame.py agent/video/tests/test_frame.py
git commit -m "feat(video): slice_lines + build_frame + reconstruct_frames + pick_sharpest"
```

---

### Task 7: `render` — normalize, save PNG, base64 thumbnail

**Files:**
- Create: `agent/video/render.py`
- Test: `agent/video/tests/test_render.py`

- [ ] **Step 1: Write the failing test** (`agent/video/tests/test_render.py`)

```python
import base64
import io

import numpy as np
from PIL import Image

from render import normalize_luma, save_full_png, thumbnail_b64


def test_normalize_luma_stretches_to_full_range():
    arr = np.linspace(0.4, 0.6, 100).reshape(10, 10)   # narrow band
    u8 = normalize_luma(arr)
    assert u8.dtype == np.uint8
    assert u8.min() == 0 and u8.max() == 255


def test_save_full_png_writes_grayscale(tmp_path):
    u8 = (np.random.default_rng(0).random((32, 48)) * 255).astype(np.uint8)
    path = tmp_path / "frames" / "1718700000.png"   # dir does not exist yet
    save_full_png(u8, str(path))
    img = Image.open(str(path))
    assert img.mode == "L"
    assert img.size == (48, 32)                       # PIL is (width, height)


def test_thumbnail_b64_is_decodable_and_bounded():
    u8 = (np.random.default_rng(1).random((480, 640)) * 255).astype(np.uint8)
    b64 = thumbnail_b64(u8, max_width=320)
    img = Image.open(io.BytesIO(base64.b64decode(b64)))
    assert img.format == "PNG"
    assert img.width <= 320
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd agent/scan && python -m pytest ../video/tests/test_render.py -q`
Expected: FAIL (No module named 'render').

- [ ] **Step 3: Implement** (`agent/video/render.py`)

```python
import base64
import io
import os

import numpy as np
from PIL import Image


def normalize_luma(frame, lo=2.0, hi=98.0):
    """Percentile-stretch a float luma frame to a uint8 0..255 image."""
    a = np.asarray(frame, dtype=np.float64)
    if a.size == 0:
        return np.zeros(a.shape, dtype=np.uint8)
    plo, phi = np.percentile(a, [lo, hi])
    if phi <= plo:
        phi = plo + 1e-9
    out = np.clip((a - plo) / (phi - plo), 0.0, 1.0)
    return (out * 255.0).astype(np.uint8)


def save_full_png(luma_u8, path):
    """Write a full-resolution grayscale PNG, creating parent dirs as needed."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    Image.fromarray(luma_u8, mode="L").save(path, format="PNG")


def thumbnail_b64(luma_u8, max_width=320):
    """Return a base64 PNG thumbnail (width <= max_width) of a grayscale frame."""
    img = Image.fromarray(luma_u8, mode="L")
    if img.width > max_width:
        h = max(1, int(round(img.height * max_width / img.width)))
        img = img.resize((max_width, h))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")
```

- [ ] **Step 4: Run it to confirm it passes**

Run: `cd agent/scan && python -m pytest ../video/tests/test_render.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add agent/video/render.py agent/video/tests/test_render.py
git commit -m "feat(video): normalize_luma + save_full_png + thumbnail_b64"
```

---

### Task 8: `publisher` additions — `build_video_payload` + `publish_video_once`

**Files:**
- Modify: `agent/scan/publisher.py` (append new builder + one-shot publisher)
- Test: `agent/video/tests/test_publish_video.py`

- [ ] **Step 1: Write the failing test** (`agent/video/tests/test_publish_video.py`)

```python
import json

import publisher


def test_build_video_payload_shape():
    p = publisher.build_video_payload(
        "scan-01", 1718700000.0, 5800.0, "PAL", 15625, 18.3, "QUJD"
    )
    assert p == {
        "scanner_id": "scan-01", "ts": 1718700000.0, "center_mhz": 5800.0,
        "standard": "PAL", "line_hz": 15625, "sync_snr_db": 18.3,
        "frame_png_b64": "QUJD",
    }


class _Info:
    def __init__(self, ok=True):
        self._ok = ok
    def wait_for_publish(self, timeout=None):
        pass
    def is_published(self):
        return self._ok


class FakeClient:
    def __init__(self):
        self.published = []
        self.creds = None
        self.connected_to = None
        self.disconnected = False
    def username_pw_set(self, u, p):
        self.creds = (u, p)
    def connect(self, host, port, keepalive=60):
        self.connected_to = (host, port, keepalive)
    def loop_start(self):
        pass
    def loop_stop(self):
        pass
    def disconnect(self):
        self.disconnected = True
    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))
        return _Info(True)


def test_publish_video_once_topic_qos_retain_no_status():
    fake = FakeClient()
    ok = publisher.publish_video_once(
        "10.8.0.1", 1883, "pub", "pw", "scan-01",
        {"ts": 1, "frame_png_b64": "x"}, client_factory=lambda cid: fake,
    )
    assert ok is True
    assert fake.creds == ("pub", "pw")
    assert fake.connected_to == ("10.8.0.1", 1883, 60)
    assert len(fake.published) == 1
    topic, payload, qos, retain = fake.published[0]
    assert topic == "fpv/scan-01/video"
    assert qos == 1 and retain is True
    assert json.loads(payload)["frame_png_b64"] == "x"
    # Must NOT touch the presence/status topic owned by the scan service.
    assert all(t != "fpv/scan-01/status" for (t, *_rest) in fake.published)
    assert fake.disconnected is True


def test_publish_video_once_returns_false_when_broker_down():
    class BoomClient(FakeClient):
        def connect(self, *a, **k):
            raise OSError("connection refused")
    ok = publisher.publish_video_once(
        "10.8.0.1", 1883, "pub", "pw", "scan-01",
        {"ts": 1}, client_factory=lambda cid: BoomClient(),
    )
    assert ok is False
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd agent/scan && python -m pytest ../video/tests/test_publish_video.py -q`
Expected: FAIL (AttributeError: module 'publisher' has no attribute 'build_video_payload').

- [ ] **Step 3: Implement** — append to `agent/scan/publisher.py`

Add at the end of the file (after the `MqttPublisher` class):

```python
def build_video_payload(scanner_id, ts, center_mhz, standard, line_hz, sync_snr_db, frame_png_b64):
    """Pure builder for the fpv/<id>/video contract (analog video frame event)."""
    return {
        "scanner_id": scanner_id,
        "ts": ts,
        "center_mhz": center_mhz,
        "standard": standard,
        "line_hz": line_hz,
        "sync_snr_db": sync_snr_db,
        "frame_png_b64": frame_png_b64,
    }


def publish_video_once(host, port, user, password, scanner_id, payload,
                       keepalive=60, client_factory=None, timeout=10.0):
    """One-shot publish of a video frame to fpv/<id>/video (QoS1, retained).

    Deliberately sets NO will/LWT and never writes fpv/<id>/status, so it cannot
    clobber the long-running scan service's retained presence. Returns True on a
    confirmed publish, False (no raise) if the broker is unreachable.
    """
    factory = client_factory or _default_client_factory
    client = factory(f"video-{scanner_id}")
    topic = f"fpv/{scanner_id}/video"
    try:
        if user:
            client.username_pw_set(user, password)
        client.connect(host, port, keepalive=keepalive)
        client.loop_start()
        info = client.publish(topic, json.dumps(payload), qos=1, retain=True)
        info.wait_for_publish(timeout)
        ok = bool(info.is_published())
        client.loop_stop()
        client.disconnect()
        return ok
    except Exception:
        LOG.warning("video publish to %s failed", topic, exc_info=True)
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass
        return False
```

- [ ] **Step 4: Run it to confirm it passes**

Run: `cd agent/scan && python -m pytest ../video/tests/test_publish_video.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Confirm the existing publisher tests still pass**

Run: `cd agent/scan && python -m pytest tests/test_publisher.py -q`
Expected: PASS (existing suite unchanged).

- [ ] **Step 6: Commit**

```bash
git add agent/scan/publisher.py agent/video/tests/test_publish_video.py
git commit -m "feat(video): build_video_payload + publish_video_once (no status/LWT)"
```

---

### Task 9: `vconfig` — VideoConfig + load_video_config

**Files:**
- Create: `agent/video/vconfig.py`
- Test: `agent/video/tests/test_vconfig.py`

- [ ] **Step 1: Write the failing test** (`agent/video/tests/test_vconfig.py`)

```python
from vconfig import VideoConfig, load_video_config


def test_defaults():
    c = load_video_config(env={})
    assert isinstance(c, VideoConfig)
    assert c.frames_dir == "/var/lib/fpv/frames"
    assert c.frame_width == 720
    assert c.thumb_max_width == 320
    assert c.lpf_cutoff_hz == 5_000_000.0
    assert c.line_snr_db == 10.0 and c.harm_snr_db == 6.0


def test_env_overrides():
    c = load_video_config(env={
        "FPV_FRAMES_DIR": "/tmp/frames",
        "FPV_FRAME_WIDTH": "640",
        "FPV_THUMB_MAX_WIDTH": "240",
        "FPV_LPF_CUTOFF_HZ": "4e6",
        "FPV_LINE_SNR_DB": "12",
        "FPV_HARM_SNR_DB": "7",
    })
    assert c.frames_dir == "/tmp/frames"
    assert c.frame_width == 640
    assert c.thumb_max_width == 240
    assert c.lpf_cutoff_hz == 4_000_000.0
    assert c.line_snr_db == 12.0 and c.harm_snr_db == 7.0
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd agent/scan && python -m pytest ../video/tests/test_vconfig.py -q`
Expected: FAIL (No module named 'vconfig').

- [ ] **Step 3: Implement** (`agent/video/vconfig.py`)

```python
import os
from dataclasses import dataclass


@dataclass
class VideoConfig:
    frames_dir: str = "/var/lib/fpv/frames"
    lpf_cutoff_hz: float = 5_000_000.0
    frame_width: int = 720
    thumb_max_width: int = 320
    line_snr_db: float = 10.0
    harm_snr_db: float = 6.0
    blank_frac: float = 0.18
    default_fs: float = 16_000_000.0


def load_video_config(env=None):
    """Build a VideoConfig from env (DSP/IO knobs). MQTT creds come from the reused
    agent/scan config.load_config(), not from here."""
    env = os.environ if env is None else env
    c = VideoConfig()
    c.frames_dir = env.get("FPV_FRAMES_DIR", c.frames_dir)
    if "FPV_LPF_CUTOFF_HZ" in env:
        c.lpf_cutoff_hz = float(env["FPV_LPF_CUTOFF_HZ"])
    if "FPV_FRAME_WIDTH" in env:
        c.frame_width = int(env["FPV_FRAME_WIDTH"])
    if "FPV_THUMB_MAX_WIDTH" in env:
        c.thumb_max_width = int(env["FPV_THUMB_MAX_WIDTH"])
    if "FPV_LINE_SNR_DB" in env:
        c.line_snr_db = float(env["FPV_LINE_SNR_DB"])
    if "FPV_HARM_SNR_DB" in env:
        c.harm_snr_db = float(env["FPV_HARM_SNR_DB"])
    if "FPV_BLANK_FRAC" in env:
        c.blank_frac = float(env["FPV_BLANK_FRAC"])
    if "FPV_DEFAULT_FS" in env:
        c.default_fs = float(env["FPV_DEFAULT_FS"])
    return c
```

- [ ] **Step 4: Run it to confirm it passes**

Run: `cd agent/scan && python -m pytest ../video/tests/test_vconfig.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add agent/video/vconfig.py agent/video/tests/test_vconfig.py
git commit -m "feat(video): VideoConfig + load_video_config (env knobs)"
```

---

### Task 10: `iq_video.py` CLI orchestrator + end-to-end tests

**Files:**
- Create: `agent/video/iq_video.py`
- Test: `agent/video/tests/test_cli.py`

- [ ] **Step 1: Write the failing test** (`agent/video/tests/test_cli.py`)

```python
import os

import numpy as np

import iq_video
from synth import make_cvbs, fm_modulate, to_int8


def _gradient(h=120, w=120):
    return np.tile(np.linspace(0.0, 1.0, h)[:, None], (1, w))


def _write_iq(tmp_path, standard, fs, frames=2, noise_std=0.0):
    bb = make_cvbs(standard, _gradient(), fs, frames=frames)
    iq = fm_modulate(bb, fs, deviation_hz=2_000_000.0)
    raw = to_int8(iq, noise_std=noise_std)
    p = tmp_path / "cap.iq"
    p.write_bytes(raw)
    return str(p)


def _run(monkeypatch, tmp_path, argv):
    # Capture publishes instead of hitting a broker; isolate the frames dir.
    sent = {}
    monkeypatch.setattr(
        iq_video, "publish_video_once",
        lambda *a, **k: (sent.update(payload=a[5]) or True),
    )
    monkeypatch.setenv("FPV_FRAMES_DIR", str(tmp_path / "frames"))
    return iq_video.main(argv), sent


def test_pal_capture_publishes_and_saves(monkeypatch, tmp_path):
    fs = 8_000_000.0
    iq_path = _write_iq(tmp_path, "PAL", fs)
    code, sent = _run(monkeypatch, tmp_path,
                      ["--iq", iq_path, "--fs", str(fs), "--center", "5800e6"])
    assert code == 0
    assert sent["payload"]["standard"] == "PAL"
    assert sent["payload"]["center_mhz"] == 5800.0
    assert sent["payload"]["frame_png_b64"]
    # A full-resolution PNG was saved locally.
    frames = os.listdir(str(tmp_path / "frames"))
    assert len(frames) == 1 and frames[0].endswith(".png")


def test_pure_noise_returns_not_video(monkeypatch, tmp_path):
    fs = 8_000_000.0
    rng = np.random.default_rng(3)
    raw = (rng.integers(-128, 128, 2 * 300_000)).astype(np.int8).tobytes()
    p = tmp_path / "noise.iq"
    p.write_bytes(raw)
    code, sent = _run(monkeypatch, tmp_path,
                      ["--iq", str(p), "--fs", str(fs), "--center", "5800e6"])
    assert code == 2
    assert sent == {}                     # nothing published
    assert not os.path.isdir(str(tmp_path / "frames")) or \
        os.listdir(str(tmp_path / "frames")) == []


def test_broker_down_saves_locally_exit_1(monkeypatch, tmp_path):
    fs = 8_000_000.0
    iq_path = _write_iq(tmp_path, "PAL", fs)
    monkeypatch.setattr(iq_video, "publish_video_once", lambda *a, **k: False)
    monkeypatch.setenv("FPV_FRAMES_DIR", str(tmp_path / "frames"))
    code = iq_video.main(["--iq", iq_path, "--fs", str(fs), "--center", "5800e6"])
    assert code == 1
    assert len(os.listdir(str(tmp_path / "frames"))) == 1   # frame still saved


def test_std_auto_distinguishes_ntsc(monkeypatch, tmp_path):
    fs = 8_000_000.0
    iq_path = _write_iq(tmp_path, "NTSC", fs)
    code, sent = _run(monkeypatch, tmp_path,
                      ["--iq", iq_path, "--fs", str(fs), "--center", "1200e6", "--std", "auto"])
    assert code == 0
    assert sent["payload"]["standard"] == "NTSC"
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd agent/scan && python -m pytest ../video/tests/test_cli.py -q`
Expected: FAIL (No module named 'iq_video').

- [ ] **Step 3: Implement** (`agent/video/iq_video.py`)

```python
import argparse
import logging
import os
import sys
import time

# Allow running as a script: expose agent/scan's flat modules (config, publisher).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scan")))

from config import load_config                              # noqa: E402  (reused scan config)
from publisher import build_video_payload, publish_video_once  # noqa: E402
from iqio import load_iq                                    # noqa: E402
from demod import fm_demod, lowpass                         # noqa: E402
from standard import detect_standard                        # noqa: E402
from frame import reconstruct_frames, pick_sharpest         # noqa: E402
from render import normalize_luma, save_full_png, thumbnail_b64  # noqa: E402
from vconfig import load_video_config                       # noqa: E402

LOG = logging.getLogger("video")

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOT_VIDEO = 2


def process(iq_path, fs, center_hz, std, vcfg, scfg, now_ts):
    iq = load_iq(iq_path)
    bb = lowpass(fm_demod(iq), fs, vcfg.lpf_cutoff_hz)
    forced = None if std == "auto" else std
    res = detect_standard(bb, fs, forced=forced,
                          line_snr_db=vcfg.line_snr_db, harm_snr_db=vcfg.harm_snr_db)
    center_mhz = center_hz / 1e6
    if res.standard is None:
        LOG.info("status=not_video center_mhz=%.3f sync_snr_db=%.1f",
                 center_mhz, res.sync_snr_db)
        return EXIT_NOT_VIDEO

    frame = pick_sharpest(
        reconstruct_frames(bb, fs, res.standard, vcfg.frame_width, vcfg.blank_frac)
    )
    luma = normalize_luma(frame)
    frame_path = os.path.join(vcfg.frames_dir, f"{now_ts}.png")
    save_full_png(luma, frame_path)
    thumb = thumbnail_b64(luma, vcfg.thumb_max_width)

    payload = build_video_payload(
        scfg.scanner_id, float(now_ts), center_mhz, res.standard, res.line_hz,
        round(float(res.sync_snr_db), 1), thumb,
    )
    ok = publish_video_once(
        scfg.mqtt_host, scfg.mqtt_port, scfg.mqtt_user, scfg.mqtt_pass,
        scfg.scanner_id, payload, scfg.mqtt_keepalive,
    )
    LOG.info("status=%s center_mhz=%.3f standard=%s sync_snr_db=%.1f frame_path=%s mqtt=%s",
             "published" if ok else "local_only", center_mhz, res.standard,
             res.sync_snr_db, frame_path, ok)
    return EXIT_OK if ok else EXIT_ERROR


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Analog FPV IQ -> luma frame -> MQTT")
    ap.add_argument("--iq", required=True, help="path to HackRF int8 IQ capture")
    ap.add_argument("--fs", type=float, default=None, help="sample rate Hz (default: config)")
    ap.add_argument("--center", type=float, required=True, help="center frequency Hz (metadata)")
    ap.add_argument("--std", choices=["auto", "pal", "ntsc"], default="auto")
    args = ap.parse_args(argv)

    vcfg = load_video_config()
    scfg = load_config()
    fs = args.fs if args.fs is not None else vcfg.default_fs
    now_ts = int(time.time())
    try:
        return process(args.iq, fs, args.center, args.std, vcfg, scfg, now_ts)
    except Exception:
        LOG.exception("processing failed for %s", args.iq)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run it to confirm it passes**

Run: `cd agent/scan && python -m pytest ../video/tests/test_cli.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add agent/video/iq_video.py agent/video/tests/test_cli.py
git commit -m "feat(video): iq_video CLI orchestrator + end-to-end tests"
```

---

### Task 11: README + full-suite green + timing note

**Files:**
- Create: `agent/video/README.md`

- [ ] **Step 1: Run the full video suite**

Run: `cd agent/scan && python -m pytest ../video/tests -q`
Expected: PASS (all tasks' tests green, ~21 tests).

- [ ] **Step 2: Confirm the scan suite still passes (publisher change)**

Run: `cd agent/scan && python -m pytest tests -q`
Expected: PASS (existing scan suite unaffected).

- [ ] **Step 3: Write the README** (`agent/video/README.md`)

```markdown
# agent/video — analog FPV IQ → luma frame → MQTT

One-shot CLI: take a HackRF int8 IQ capture of a suspected analog FPV video carrier,
confirm it is PAL/NTSC, reconstruct a monochrome (luma) frame, and publish a PNG
thumbnail to `fpv/<scanner_id>/video` over MQTT (WireGuard). Only a PNG (tens of KB)
crosses the tunnel — never raw IQ.

Digital FPV (DJI/HDZero/Walksnail OFDM) and color decode are out of scope.

## Run

    # from the pipeline, right after hackrf_transfer wrote cap.iq:
    cd agent/scan && .venv/bin/python ../video/iq_video.py \
        --iq cap.iq --fs 16e6 --center 5800e6 --std auto

- `--fs` defaults to `FPV_DEFAULT_FS` (16e6). `--center` is metadata only.
- `--std auto` (default) picks PAL/NTSC by the measured line rate; force with `pal`/`ntsc`.
- Degraded mode (Pi Zero 2): `--fs 10e6` and one short capture.

### Exit codes
- `0` — frame published to MQTT.
- `2` — `not_video` (no line-sync gate): nothing published, no PNG written.
- `1` — error, **or** broker unreachable after the frame was built (the full-res PNG
  is still saved to `FPV_FRAMES_DIR`, a warning is logged).

## MQTT contract

Topic `fpv/<scanner_id>/video`, QoS 1, retained:

    { "scanner_id": "scan-01", "ts": 1718700000.0, "center_mhz": 5800.0,
      "standard": "PAL", "line_hz": 15625, "sync_snr_db": 18.3,
      "frame_png_b64": "<base64 PNG thumbnail, <=320 px>" }

The one-shot publisher never writes `fpv/<id>/status`, so it does not disturb the
scan service's presence for the same `scanner_id`.

## Config

MQTT host/port/creds + `scanner_id` are reused from the scan service
(`agent/scan/config.py`: `SCAN_ID`, `SCAN_MQTT_HOST`, `MQTT_PUB_USER`, `MQTT_PUB_PASS`).
Video DSP/IO knobs via env: `FPV_FRAMES_DIR` (default `/var/lib/fpv/frames`),
`FPV_FRAME_WIDTH`, `FPV_THUMB_MAX_WIDTH`, `FPV_LPF_CUTOFF_HZ`, `FPV_LINE_SNR_DB`,
`FPV_HARM_SNR_DB`, `FPV_DEFAULT_FS`.

## Install

    pip install -r agent/scan/requirements.txt   # adds Pillow to the shared scan venv

Ensure `FPV_FRAMES_DIR` is writable by the service user.

## Test (no hardware)

    cd agent/scan && python -m pytest ../video/tests -q

`synth.py` generates synthetic PAL/NTSC CVBS → FM → int8 IQ to drive the whole
pipeline end-to-end.

## Pipeline integration (out of scope here)

Wiring this CLI into `main.py`'s scan loop (writing `cap.iq` per candidate and shelling
out to `iq_video.py`), and dashboard consumption of `fpv/<id>/video`, are separate tasks.
```

- [ ] **Step 4: Commit**

```bash
git add agent/video/README.md
git commit -m "docs(video): README — run, exit codes, MQTT contract, config"
```

---

## Self-Review notes (for the implementer)

- **Performance acceptance** (spec §7: < ~1 s on Pi 4 for 16 Msps / 0.2 s = 3.2 M samples) is not unit-tested (no Pi in CI). After deploy, time one real capture: `time .venv/bin/python ../video/iq_video.py --iq cap.iq --fs 16e6 --center 5800e6`. All DSP stages are vectorized (`fm_demod` one complex multiply + `np.angle`; `lowpass` cumsum; `slice_lines`/`build_frame` broadcasting), so the budget is expected to hold; if not, the first lever is `slice_lines`' `np.interp` grid.
- **Live MQTT verification** (spec §7): with the broker up, `mosquitto_sub -t 'fpv/#' -v` over WG should show a retained `fpv/<id>/video` after a real video capture, and the scan service's `fpv/<id>/status` must stay untouched.
- Tests use `fs=8e6` and 2 frames for speed; the production default is 16e6 via `FPV_DEFAULT_FS`.
