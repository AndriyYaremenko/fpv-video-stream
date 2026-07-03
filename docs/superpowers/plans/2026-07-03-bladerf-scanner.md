# bladeRF Scanner (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the HackRF SDR backend with a bladeRF 2.0 micro that sweeps 1.2/2.4/5.8 GHz, detects and classifies carriers, and publishes to the existing MQTT topics — no transmit, no video demodulation (that is Phase 2).

**Architecture:** A new `bladerf_source.py` owns the bladeRF device and turns tuned IQ captures into the same `Spectrum` objects and dwell IQ arrays the pipeline already consumes. `main.py` gains a small acquisition seam that picks the backend by `cfg.sdr`. Everything downstream — `detector`, `classifier`, `publisher`, `reporter`, `Rx5808Controller` — is untouched. The device-touching capture is a thin, injectable function so all sweep/dwell logic is unit-tested with synthetic IQ and no hardware.

**Tech Stack:** Python 3.13, NumPy, libbladeRF + its `bladerf` Python bindings, paho-mqtt, pytest. Runs on the Pi 5 node (aarch64, Debian 13).

## Global Constraints

- Python **3.13**; NumPy **>=1.26** (2.5.0 installed); paho-mqtt **>=2.0**. One line each, verbatim from `agent/scan/requirements.txt`.
- Tests run from `agent/scan/` via `pytest` (conftest.py adds the dir to `sys.path`). Unit tests must **not** touch hardware or the network.
- **Reuse** `models.Spectrum`, `detector.find_candidates`, `dweller.compute_features`, `classifier.classify`, `publisher.MqttPublisher`, `reporter.*`, `rx5808*`. Do not modify their behavior.
- New backend must be **hardware-injectable**: all DSP is pure functions; the only bladeRF-API code is one capture function, verified on the Pi.
- Deploy identity on the Pi 5 node: `SCAN_ID=bladerf`, `SCAN_SDR=bladerf`. HackRF path stays intact as reserve (`SCAN_SDR=hackrf`).
- No transmit anywhere in this phase.

---

### Task 1: Config — select SDR backend and bladeRF parameters

**Files:**
- Modify: `agent/scan/config.py` (add fields + env parsing)
- Test: `agent/scan/tests/test_config.py`

**Interfaces:**
- Produces: `Config.sdr: str` (`"bladerf"` default), `Config.bladerf_sample_rate_hz: float`, `Config.bladerf_bandwidth_hz: float`, `Config.bladerf_window_mhz: float`, `Config.bladerf_sweep_samples: int`, `Config.bladerf_gain_db: int`. Env keys: `SCAN_SDR`, `BLADERF_SAMPLE_RATE`, `BLADERF_BANDWIDTH`, `BLADERF_WINDOW_MHZ`, `BLADERF_SWEEP_SAMPLES`, `BLADERF_GAIN`.

- [ ] **Step 1: Write the failing test**

Add to `agent/scan/tests/test_config.py`:

```python
def test_sdr_backend_defaults_and_env():
    c = load_config({})
    assert c.sdr == "bladerf"
    assert c.bladerf_sample_rate_hz == 40_000_000.0
    assert c.bladerf_bandwidth_hz == 40_000_000.0
    assert c.bladerf_window_mhz == 30.0
    assert c.bladerf_sweep_samples == 65_536
    assert c.bladerf_gain_db == 40
    c2 = load_config({
        "SCAN_SDR": "hackrf",
        "BLADERF_SAMPLE_RATE": "20000000",
        "BLADERF_BANDWIDTH": "18000000",
        "BLADERF_WINDOW_MHZ": "15",
        "BLADERF_SWEEP_SAMPLES": "32768",
        "BLADERF_GAIN": "30",
    })
    assert c2.sdr == "hackrf"
    assert c2.bladerf_sample_rate_hz == 20_000_000.0
    assert c2.bladerf_bandwidth_hz == 18_000_000.0
    assert c2.bladerf_window_mhz == 15.0
    assert c2.bladerf_sweep_samples == 32768
    assert c2.bladerf_gain_db == 30
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent/scan && .venv/bin/python -m pytest tests/test_config.py::test_sdr_backend_defaults_and_env -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'sdr'`.

- [ ] **Step 3: Write minimal implementation**

In `agent/scan/config.py`, add fields to the `Config` dataclass (place after `amp_enable`):

```python
    sdr: str = "bladerf"                        # "bladerf" | "hackrf"
    bladerf_sample_rate_hz: float = 40_000_000.0
    bladerf_bandwidth_hz: float = 40_000_000.0
    bladerf_window_mhz: float = 30.0            # usable span per tune (< bandwidth for filter margin)
    bladerf_sweep_samples: int = 65_536         # IQ per window for the power spectrum
    bladerf_gain_db: int = 40
```

In `load_config`, add parsing (place after the `SCAN_AMP` block):

```python
    c.sdr = env.get("SCAN_SDR", c.sdr)
    if "BLADERF_SAMPLE_RATE" in env:
        c.bladerf_sample_rate_hz = float(env["BLADERF_SAMPLE_RATE"])
    if "BLADERF_BANDWIDTH" in env:
        c.bladerf_bandwidth_hz = float(env["BLADERF_BANDWIDTH"])
    if "BLADERF_WINDOW_MHZ" in env:
        c.bladerf_window_mhz = float(env["BLADERF_WINDOW_MHZ"])
    if "BLADERF_SWEEP_SAMPLES" in env:
        c.bladerf_sweep_samples = int(env["BLADERF_SWEEP_SAMPLES"])
    if "BLADERF_GAIN" in env:
        c.bladerf_gain_db = int(env["BLADERF_GAIN"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd agent/scan && .venv/bin/python -m pytest tests/test_config.py -v`
Expected: PASS (all config tests, including the new one).

- [ ] **Step 5: Commit**

```bash
git add agent/scan/config.py agent/scan/tests/test_config.py
git commit -m "feat(scan): config selects SDR backend + bladeRF params"
```

---

### Task 2: bladeRF pure DSP layer (windows, IQ→spectrum, assembly)

**Files:**
- Create: `agent/scan/bladerf_source.py`
- Test: `agent/scan/tests/test_bladerf_source.py`

**Interfaces:**
- Produces:
  - `iq_from_sc16q11(raw: bytes) -> np.ndarray` — SC16_Q11 interleaved int16 → normalized complex64.
  - `welch_psd(iq: np.ndarray, seg: int = 1024) -> np.ndarray` — averaged (un-normalized) PSD, fftshifted.
  - `plan_windows(low_mhz: float, high_mhz: float, window_mhz: float) -> list[float]` — window center frequencies (MHz) covering `[low, high]`.
  - `window_spectrum(iq, center_hz, sample_rate_hz, seg=1024) -> tuple[np.ndarray, np.ndarray]` — `(freqs_mhz, power_db)` for one window.
  - `assemble_band_spectrum(parts, band) -> Spectrum` — concatenate/sort windows into one `Spectrum`.
- Consumes: `models.Spectrum`.

- [ ] **Step 1: Write the failing test**

Create `agent/scan/tests/test_bladerf_source.py`:

```python
import numpy as np

from bladerf_source import (
    iq_from_sc16q11, welch_psd, plan_windows, window_spectrum, assemble_band_spectrum,
)
from models import Spectrum


def test_iq_from_sc16q11_scales_and_deinterleaves():
    raw = np.array([2048, 0, 0, -2048], dtype=np.int16).tobytes()
    iq = iq_from_sc16q11(raw)
    assert iq.shape == (2,)
    assert abs(iq[0] - (1.0 + 0.0j)) < 1e-6
    assert abs(iq[1] - (0.0 - 1.0j)) < 1e-6


def test_plan_windows_covers_range():
    centers = plan_windows(5645.0, 5945.0, 30.0)
    assert centers[0] == 5660.0                       # low + window/2
    assert all(round(centers[i+1] - centers[i], 3) == 30.0 for i in range(len(centers) - 1))
    assert centers[-1] + 15.0 >= 5945.0               # last window reaches the top
    assert centers[0] - 15.0 <= 5645.0                # first window reaches the bottom


def test_plan_windows_rejects_bad_input():
    assert plan_windows(100.0, 100.0, 30.0) == []
    assert plan_windows(200.0, 100.0, 30.0) == []
    assert plan_windows(100.0, 200.0, 0.0) == []


def test_window_spectrum_peaks_at_signal_frequency():
    fs = 40_000_000.0
    center = 5_800_000_000.0
    n = 8192
    t = np.arange(n) / fs
    iq = np.exp(2j * np.pi * 5.0e6 * t)               # +5 MHz tone within the window
    freqs_mhz, power_db = window_spectrum(iq, center, fs, seg=1024)
    peak_mhz = freqs_mhz[int(np.argmax(power_db))]
    assert abs(peak_mhz - (center + 5.0e6) / 1e6) < 0.2


def test_assemble_band_spectrum_sorts_and_concatenates():
    a = (np.array([5810.0, 5800.0]), np.array([-40.0, -80.0]))
    b = (np.array([5700.0, 5710.0]), np.array([-70.0, -75.0]))
    spec = assemble_band_spectrum([a, b], "5.8G")
    assert isinstance(spec, Spectrum)
    assert spec.band == "5.8G"
    assert list(spec.freqs_mhz) == sorted(spec.freqs_mhz)
    assert spec.power_dbm[0] == -70.0                 # 5700 MHz bin
    assert spec.power_dbm[list(spec.freqs_mhz).index(5800.0)] == -80.0


def test_assemble_band_spectrum_empty():
    spec = assemble_band_spectrum([], "2.4G")
    assert spec.band == "2.4G"
    assert len(spec.freqs_mhz) == 0 and len(spec.power_dbm) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent/scan && .venv/bin/python -m pytest tests/test_bladerf_source.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bladerf_source'`.

- [ ] **Step 3: Write minimal implementation**

Create `agent/scan/bladerf_source.py`:

```python
import logging

import numpy as np

from models import Spectrum

LOG = logging.getLogger("scan.bladerf")
_EPS = 1e-12


def iq_from_sc16q11(raw: bytes) -> np.ndarray:
    """bladeRF SC16_Q11 (interleaved int16, 11 fractional bits) -> normalized complex64."""
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    return ((x[0::2] + 1j * x[1::2]) / 2048.0).astype(np.complex64)


def welch_psd(iq: np.ndarray, seg: int = 1024) -> np.ndarray:
    """Welch-style averaged, fftshifted power spectral density (NOT normalized)."""
    n = len(iq)
    if n < seg:
        seg = n
    if seg <= 0:
        return np.zeros(0)
    win = np.hanning(seg)
    nseg = max(1, n // seg)
    acc = np.zeros(seg)
    used = 0
    for k in range(nseg):
        chunk = iq[k * seg:(k + 1) * seg]
        if len(chunk) < seg:
            break
        acc += np.abs(np.fft.fftshift(np.fft.fft(chunk * win))) ** 2
        used += 1
    return acc / used if used else np.zeros(seg)


def plan_windows(low_mhz: float, high_mhz: float, window_mhz: float) -> list:
    """Window center frequencies (MHz) that tile [low, high] in `window_mhz` steps."""
    if high_mhz <= low_mhz or window_mhz <= 0:
        return []
    centers = []
    c = low_mhz + window_mhz / 2.0
    while c - window_mhz / 2.0 < high_mhz:
        centers.append(round(c, 3))
        c += window_mhz
    return centers


def window_spectrum(iq: np.ndarray, center_hz: float, sample_rate_hz: float, seg: int = 1024):
    """One tuned window -> (freqs_mhz, power_db) with absolute frequency axis."""
    psd = welch_psd(iq, seg)
    n = len(psd)
    if n == 0:
        return np.zeros(0), np.zeros(0)
    offsets = (np.arange(n) - n // 2) * (sample_rate_hz / n)
    freqs_mhz = (center_hz + offsets) / 1e6
    power_db = 10.0 * np.log10(psd + _EPS)
    return freqs_mhz, power_db


def assemble_band_spectrum(parts, band: str) -> Spectrum:
    """Concatenate per-window (freqs_mhz, power_db) parts into one sorted Spectrum."""
    if not parts:
        return Spectrum(band=band, freqs_mhz=np.zeros(0), power_dbm=np.zeros(0))
    f = np.concatenate([p[0] for p in parts])
    p = np.concatenate([p[1] for p in parts])
    order = np.argsort(f)
    return Spectrum(band=band, freqs_mhz=f[order], power_dbm=p[order])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd agent/scan && .venv/bin/python -m pytest tests/test_bladerf_source.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add agent/scan/bladerf_source.py agent/scan/tests/test_bladerf_source.py
git commit -m "feat(scan): bladeRF pure DSP layer (windows, IQ->spectrum, assembly)"
```

---

### Task 3: BladerfBackend — sweep_band + dwell over an injectable capture

**Files:**
- Modify: `agent/scan/bladerf_source.py` (add the class)
- Test: `agent/scan/tests/test_bladerf_source.py` (add cases)

**Interfaces:**
- Produces: `class BladerfBackend` with:
  - `__init__(self, sample_rate_hz, window_mhz, sweep_samples, capture)` where `capture(center_hz, sample_rate_hz, num_samples) -> np.ndarray` (complex IQ).
  - `sweep_band(self, low_mhz, high_mhz, band) -> Spectrum`.
  - `dwell(self, center_mhz, sample_rate_hz, num_samples) -> np.ndarray`.
- Consumes: `plan_windows`, `window_spectrum`, `assemble_band_spectrum` (Task 2).

- [ ] **Step 1: Write the failing test**

Add to `agent/scan/tests/test_bladerf_source.py`:

```python
from bladerf_source import BladerfBackend


def _fake_capture_factory(fs):
    # Returns a capture() that emits a +6 MHz tone only when the window covers 5800 MHz.
    def capture(center_hz, sample_rate_hz, num_samples):
        t = np.arange(num_samples) / sample_rate_hz
        near_5800 = abs(center_hz - 5_800_000_000.0) <= (sample_rate_hz / 2.0)
        amp = 1.0 if near_5800 else 0.001
        return (amp * np.exp(2j * np.pi * 3.0e6 * t)).astype(np.complex64)
    return capture


def test_backend_sweep_band_finds_bump():
    fs = 40_000_000.0
    calls = []
    cap = _fake_capture_factory(fs)
    def counting_cap(c, s, n):
        calls.append((c, s, n))
        return cap(c, s, n)
    be = BladerfBackend(sample_rate_hz=fs, window_mhz=30.0, sweep_samples=8192, capture=counting_cap)
    spec = be.sweep_band(5645.0, 5945.0, "5.8G")
    assert spec.band == "5.8G"
    assert len(spec.freqs_mhz) > 0
    # every window captured at the configured sweep sample rate + sample count
    assert all(s == fs and n == 8192 for _, s, n in calls)
    # the strongest bin sits near the injected signal (5800 + 3 MHz)
    peak_mhz = spec.freqs_mhz[int(np.argmax(spec.power_dbm))]
    assert abs(peak_mhz - 5803.0) < 1.0


def test_backend_dwell_passes_through_capture():
    seen = {}
    def cap(center_hz, sample_rate_hz, num_samples):
        seen.update(center_hz=center_hz, sr=sample_rate_hz, n=num_samples)
        return np.ones(num_samples, dtype=np.complex64)
    be = BladerfBackend(sample_rate_hz=40e6, window_mhz=30.0, sweep_samples=8192, capture=cap)
    iq = be.dwell(5800.0, 20_000_000.0, 4096)
    assert len(iq) == 4096
    assert seen == {"center_hz": 5_800_000_000.0, "sr": 20_000_000.0, "n": 4096}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent/scan && .venv/bin/python -m pytest tests/test_bladerf_source.py::test_backend_sweep_band_finds_bump -v`
Expected: FAIL — `ImportError: cannot import name 'BladerfBackend'`.

- [ ] **Step 3: Write minimal implementation**

Append to `agent/scan/bladerf_source.py`:

```python
class BladerfBackend:
    """Turns tuned IQ captures into Spectrum sweeps and dwell IQ. `capture` is injected so
    the sweep/dwell logic is fully testable without hardware; production passes the real
    bladeRF capture (see open_bladerf_capture)."""

    def __init__(self, sample_rate_hz, window_mhz, sweep_samples, capture):
        self.sample_rate_hz = float(sample_rate_hz)
        self.window_mhz = float(window_mhz)
        self.sweep_samples = int(sweep_samples)
        self._capture = capture

    def sweep_band(self, low_mhz, high_mhz, band) -> Spectrum:
        parts = []
        for c_mhz in plan_windows(low_mhz, high_mhz, self.window_mhz):
            iq = self._capture(c_mhz * 1e6, self.sample_rate_hz, self.sweep_samples)
            parts.append(window_spectrum(iq, c_mhz * 1e6, self.sample_rate_hz))
        return assemble_band_spectrum(parts, band)

    def dwell(self, center_mhz, sample_rate_hz, num_samples) -> np.ndarray:
        return self._capture(center_mhz * 1e6, float(sample_rate_hz), int(num_samples))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd agent/scan && .venv/bin/python -m pytest tests/test_bladerf_source.py -v`
Expected: PASS (8 tests total).

- [ ] **Step 5: Commit**

```bash
git add agent/scan/bladerf_source.py agent/scan/tests/test_bladerf_source.py
git commit -m "feat(scan): BladerfBackend sweep_band + dwell over injectable capture"
```

---

### Task 4: Real libbladeRF capture + device lifecycle (hardware)

**Files:**
- Modify: `agent/scan/bladerf_source.py` (add `open_bladerf_capture`, `BladerfDevice`)
- Test: `agent/scan/tests/test_bladerf_source.py` (config-plumbing test only; the RF path is verified on the Pi)
- Modify: `agent/scan/requirements.txt` (document the `bladerf` binding install)

**Interfaces:**
- Produces:
  - `class BladerfDevice` — holds an open bladeRF RX channel; `capture(center_hz, sample_rate_hz, num_samples) -> np.ndarray`; `close()`. Takes the radio handle, channel, and libbladeRF enums as **constructor arguments** (no `bladerf` import inside), so it is testable with a fake radio on a box where `bladerf` is not installed.
  - `open_bladerf_capture(gain_db, bandwidth_hz) -> BladerfDevice` — the **only** function that imports `bladerf`: opens the first device, resolves the channel/enums, constructs `BladerfDevice`.
- Consumes: `iq_from_sc16q11` (Task 2). The `capture` passed to `BladerfBackend` is the device's **bound method** `dev.capture` (see Task 5), which matches `capture(center_hz, sample_rate_hz, num_samples)`.

- [ ] **Step 1: Write the failing test** (fake radio — no `bladerf` install needed, since enums are injected)

Add to `agent/scan/tests/test_bladerf_source.py`:

```python
def test_bladerf_device_retunes_and_converts():
    import bladerf_source as bs

    events = []

    class _FakeRadio:
        def set_sample_rate(self, ch, v): events.append(("sr", int(v)))
        def set_bandwidth(self, ch, v): events.append(("bw", int(v)))
        def set_frequency(self, ch, v): events.append(("freq", int(v)))
        def set_gain_mode(self, ch, m): events.append(("gainmode", m))
        def set_gain(self, ch, v): events.append(("gain", int(v)))
        def sync_config(self, **kw): events.append(("sync_config", kw.get("num_buffers")))
        def enable_module(self, ch, on): events.append(("enable", on))
        def sync_rx(self, buf, n):
            # two samples: (2048,0) -> 1+0j, (0,2048) -> 0+1j
            buf[:] = np.array([2048, 0, 0, 2048], dtype="int16").tobytes()

    dev = bs.BladerfDevice(_FakeRadio(), channel="RX0", gain_db=30, bandwidth_hz=18_000_000.0,
                           gain_mode="manual", layout="RX_X1", fmt="SC16_Q11")
    iq = dev.capture(5_800_000_000.0, 40_000_000.0, 2)

    assert ("freq", 5_800_000_000) in events
    assert ("sr", 40_000_000) in events
    assert ("gain", 30) in events
    assert len(iq) == 2
    assert abs(iq[0] - (1 + 0j)) < 1e-6 and abs(iq[1] - (0 + 1j)) < 1e-6
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent/scan && .venv/bin/python -m pytest tests/test_bladerf_source.py::test_bladerf_device_retunes_and_converts -v`
Expected: FAIL — `AttributeError: module 'bladerf_source' has no attribute 'BladerfDevice'`.

- [ ] **Step 3: Write minimal implementation**

Append to `agent/scan/bladerf_source.py`:

```python
class BladerfDevice:
    """Holds an open bladeRF RX channel and captures a block of IQ per call, retuning as needed.
    The radio handle, channel object, and libbladeRF enums are injected (see open_bladerf_capture)
    so this class imports nothing from `bladerf` and is fully testable with a fake radio."""

    def __init__(self, radio, channel, gain_db, bandwidth_hz, gain_mode, layout, fmt):
        self._radio = radio
        self._ch = channel
        self._enabled = False
        self._sr = None
        radio.set_gain_mode(channel, gain_mode)
        radio.set_gain(channel, int(gain_db))
        radio.set_bandwidth(channel, int(bandwidth_hz))
        radio.sync_config(
            layout=layout, fmt=fmt,
            num_buffers=16, buffer_size=8192, num_transfers=8, stream_timeout=3500,
        )

    def capture(self, center_hz, sample_rate_hz, num_samples) -> np.ndarray:
        sr = int(sample_rate_hz)
        if sr != self._sr:
            self._radio.set_sample_rate(self._ch, sr)
            self._sr = sr
        self._radio.set_frequency(self._ch, int(center_hz))
        if not self._enabled:
            self._radio.enable_module(self._ch, True)
            self._enabled = True
        buf = bytearray(int(num_samples) * 4)          # SC16_Q11 = 2 x int16 per sample
        self._radio.sync_rx(buf, int(num_samples))
        return iq_from_sc16q11(bytes(buf))

    def close(self):
        try:
            if self._enabled:
                self._radio.enable_module(self._ch, False)
        except Exception:
            LOG.exception("bladeRF disable failed")


def open_bladerf_capture(gain_db, bandwidth_hz) -> BladerfDevice:
    """Open the first bladeRF, resolve channel/enums, and return a configured BladerfDevice.
    The only function that imports `bladerf`. Raises on no device."""
    import bladerf
    radio = bladerf.BladeRF()
    return BladerfDevice(
        radio, bladerf.CHANNEL_RX(0), gain_db, bandwidth_hz,
        gain_mode=bladerf.GainMode.Manual,
        layout=bladerf.ChannelLayout.RX_X1,
        fmt=bladerf.Format.SC16_Q11,
    )
```

Note in `agent/scan/requirements.txt`, append a comment block (the binding is not vendored):

```python
# bladeRF host tools + Python bindings (Pi only, not pip-resolvable as a wheel on aarch64):
#   sudo apt-get install -y bladerf libbladerf-dev python3-bladerf
#   # if python3-bladerf is absent on Debian 13, install the binding from the bladeRF source
#   # tree (host/libraries/libbladeRF_bindings/python) into the venv, like lgpio.
# bladerf_source imports `bladerf` lazily inside BladerfDevice, so its absence only disables
# the bladeRF backend (SCAN_SDR=hackrf still works with no bladerf installed).
```

- [ ] **Step 4: Run test + verify import laziness**

Run: `cd agent/scan && .venv/bin/python -m pytest tests/test_bladerf_source.py -v`
Expected: PASS (9 tests). The suite must pass on this dev box where `bladerf` is **not** installed — proving the import is lazy (only `BladerfDevice.__init__`/`open_bladerf_capture` import it).

- [ ] **Step 5: On-Pi hardware verification** (run during deploy; not a unit test)

On the Pi 5 (`andriy@192.168.1.204`), after installing the binding:

```bash
bladeRF-cli -e info                     # device present, FPGA loaded
/opt/fpv-video-stream/agent/scan/.venv/bin/python - <<'PY'
import bladerf_source as bs
dev = bs.open_bladerf_capture(gain_db=40, bandwidth_hz=40_000_000.0)
iq = dev.capture(5_800_000_000.0, 40_000_000.0, 65536)
dev.close()
print("captured", iq.shape, "mean|iq|", float(abs(iq).mean()))
PY
```
Expected: `captured (65536,) mean|iq| <nonzero>`. If the `bladerf` API differs from the calls above (method names/enums), adjust `BladerfDevice`/`open_bladerf_capture` to match `python -c "import bladerf; help(bladerf)"` and re-run — the fake-radio unit test locks the intended call sequence.

- [ ] **Step 6: Commit**

```bash
git add agent/scan/bladerf_source.py agent/scan/tests/test_bladerf_source.py agent/scan/requirements.txt
git commit -m "feat(scan): real libbladeRF capture + device lifecycle"
```

---

### Task 5: Wire the bladeRF backend into main.py's acquisition seam

**Files:**
- Modify: `agent/scan/main.py` (`_get_spectrum`, `_get_iq`, a lazy backend accessor, `reset_hackrf` guard)
- Test: `agent/scan/tests/test_run_cycle.py` (add a bladeRF-path case)

**Interfaces:**
- Consumes: `BladerfBackend`, `open_bladerf_capture` (Tasks 3-4); `Config.sdr`, `Config.bladerf_*` (Task 1).
- Produces: `main._get_bladerf_backend(cfg) -> BladerfBackend` (module-level, lazily constructed singleton).

- [ ] **Step 1: Write the failing test**

Add to `agent/scan/tests/test_run_cycle.py`:

```python
def test_run_cycle_uses_bladerf_backend(tmp_path, monkeypatch):
    # Live mode with the bladeRF backend: inject a fake capture so no hardware is needed.
    cfg = Config()
    cfg.source = "live"
    cfg.sdr = "bladerf"
    cfg.state_path = str(tmp_path / "scan.json")
    cfg.bands = {"5.8G": (5645.0, 5945.0)}
    cfg.bladerf_sample_rate_hz = 40_000_000.0
    cfg.bladerf_window_mhz = 30.0
    cfg.bladerf_sweep_samples = 8192

    from bladerf_source import BladerfBackend

    def fake_capture(center_hz, sample_rate_hz, num_samples):
        t = np.arange(num_samples) / sample_rate_hz
        near = abs(center_hz - 5_800_000_000.0) <= (sample_rate_hz / 2.0)
        amp = 1.0 if near else 0.001
        return (amp * np.exp(2j * np.pi * 2.0e6 * t)).astype(np.complex64)

    backend = BladerfBackend(cfg.bladerf_sample_rate_hz, cfg.bladerf_window_mhz,
                             cfg.bladerf_sweep_samples, capture=fake_capture)
    monkeypatch.setattr(main, "_get_bladerf_backend", lambda c: backend)

    payload = main.run_cycle(cfg, now_ts=1718530000, publisher=_FakePub())

    assert payload["occupancy"]["5.8G"] > 0.0
    assert len(payload["detections"]) >= 1
    assert abs(payload["detections"][0]["center_mhz"] - 5802.0) < 2.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent/scan && .venv/bin/python -m pytest tests/test_run_cycle.py::test_run_cycle_uses_bladerf_backend -v`
Expected: FAIL — `AttributeError: <module 'main'> does not have the attribute '_get_bladerf_backend'`.

- [ ] **Step 3: Write minimal implementation**

In `agent/scan/main.py`, add a lazy backend accessor near the top (after imports):

```python
_BLADERF_BACKEND = None


def _get_bladerf_backend(cfg: Config):
    global _BLADERF_BACKEND
    if _BLADERF_BACKEND is None:
        from bladerf_source import BladerfBackend, open_bladerf_capture
        dev = open_bladerf_capture(cfg.bladerf_gain_db, cfg.bladerf_bandwidth_hz)
        # dev.capture is a bound method (center_hz, sample_rate_hz, num_samples) -> iq.
        # The backend keeps a reference to it, which keeps `dev` alive (no premature close).
        _BLADERF_BACKEND = BladerfBackend(
            cfg.bladerf_sample_rate_hz, cfg.bladerf_window_mhz, cfg.bladerf_sweep_samples,
            capture=dev.capture,
        )
    return _BLADERF_BACKEND
```

Replace `_get_spectrum` and `_get_iq` bodies:

```python
def _get_spectrum(cfg: Config, band: str, brange) -> Spectrum:
    if cfg.source == "replay":
        path = os.path.join(cfg.fixtures_dir, f"sweep_{band}.csv")
        return sweep_replay(path, band)
    if cfg.sdr == "bladerf":
        return _get_bladerf_backend(cfg).sweep_band(brange[0], brange[1], band)
    lines = sweep_live(brange[0], brange[1], cfg.sweep_bin_hz, cfg.lna_gain, cfg.vga_gain, cfg.amp_enable)
    return parse_sweep_output(lines, band)


def _get_iq(cfg: Config, cand: Candidate) -> np.ndarray:
    if cfg.source == "replay":
        path = os.path.join(cfg.fixtures_dir, f"iq_{cand.band}.bin")
        return dwell_replay(path)
    if cfg.sdr == "bladerf":
        return _get_bladerf_backend(cfg).dwell(cand.center_mhz, cfg.dwell_sample_rate_hz, cfg.dwell_num_samples)
    return dwell_live(cand.center_mhz, cfg.dwell_sample_rate_hz, cfg.dwell_num_samples,
                      cfg.lna_gain, cfg.vga_gain, cfg.amp_enable)
```

Guard the HackRF reset in `main()` (only reset when the HackRF is actually the SDR):

```python
            if cfg.source == "live" and cfg.sdr == "hackrf":
                try:
                    reset_hackrf()
                except Exception:
                    LOG.exception("device reset failed")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent/scan && .venv/bin/python -m pytest tests/ -v`
Expected: PASS — the new bladeRF case plus **all existing tests** (replay tests are unaffected because `source=="replay"` short-circuits before the `sdr` branch).

- [ ] **Step 5: Commit**

```bash
git add agent/scan/main.py agent/scan/tests/test_run_cycle.py
git commit -m "feat(scan): select bladeRF backend in the acquisition seam"
```

---

### Task 6: Deploy config — systemd unit reference + Pi rollout

**Files:**
- Modify: `systemd/fpv-scan.service` (repo reference unit)
- Modify: `README` or `docs/` deploy note (bladeRF install steps) — append to the existing scan-deploy docs if present, else a short section in `agent/scan/README` (create if absent)

**Interfaces:**
- Consumes: `SCAN_SDR`, `SCAN_ID` (Task 1); the on-Pi hand-installed unit already sets `SCAN_ID`/`SCAN_AMP`/`RX5808_ENABLED` and must gain `SCAN_SDR=bladerf`.

- [ ] **Step 1: Update the repo reference unit**

In `systemd/fpv-scan.service`, add under the existing `Environment=` lines:

```ini
# SDR backend: "bladerf" (default) uses the bladeRF 2.0 micro; "hackrf" falls back to hackrf_sweep.
Environment=SCAN_SDR=bladerf
```

- [ ] **Step 2: Document the Pi install** (append a "bladeRF backend" note to the scan deploy docs)

```markdown
### bladeRF backend (Phase 1)
Install host tools + Python bindings on the scan Pi:
    sudo apt-get install -y bladerf libbladerf-dev python3-bladerf
    # link the binding into the venv if apt puts it in dist-packages (like lgpio):
    SP=/opt/fpv-video-stream/agent/scan/.venv/lib/python3.13/site-packages
    ln -sf /usr/lib/python3/dist-packages/bladerf "$SP/" 2>/dev/null || true
Enable it in /etc/systemd/system/fpv-scan.service:  Environment=SCAN_SDR=bladerf
Verify:  bladeRF-cli -e info   then restart:  sudo systemctl restart fpv-scan
```

- [ ] **Step 3: Commit**

```bash
git add systemd/fpv-scan.service docs/ agent/scan/README* 2>/dev/null
git commit -m "docs(scan): bladeRF backend systemd + deploy notes"
```

- [ ] **Step 4: Deploy + integration smoke on the Pi 5** (run, capture evidence)

1. `git pull` the branch on the Pi (`/opt/fpv-video-stream`), install the bladeRF binding (Step 2), `.venv/bin/pip` unchanged.
2. Run Task 4 Step 5's capture probe — confirm nonzero IQ.
3. Set `SCAN_SDR=bladerf` + `SCAN_ID=bladerf` in the Pi unit; register a `bladerf` scanner on the dashboard; `systemctl restart fpv-scan`.
4. Subscribe on the broker: expect `fpv/bladerf/spectrum` + `fpv/bladerf/detection` flowing, scanner online on the dashboard.
5. With a known 5.8 VTX on air, confirm it is detected, classified `analog`, and the RX5808 auto-tunes to it (OSD channel matches). Retune `SCAN_LNA`-equivalent `BLADERF_GAIN` and `snr_threshold_db` if detections are noisy.

Expected: bladeRF-sourced spectrum/detections on the dashboard; RX5808 still auto-tunes; HackRF unplugged (freed USB/power) or left idle.

- [ ] **Step 5: Commit any threshold retune**

```bash
git add agent/scan/config.py systemd/fpv-scan.service
git commit -m "tune(scan): bladeRF gain/threshold defaults from on-air test"
```

---

## Notes for the implementer

- **Do not** re-tune the detector/classifier math; if bladeRF SNR differs from HackRF, adjust `BLADERF_GAIN` and the existing `snr_threshold_db` env, not the algorithms.
- **Power:** the bladeRF draws from the Pi's USB3 port; if `vcgencmd get_throttled` shows live under-voltage under load, that is the node's PSU/hub budget (see the deploy notes), not a code bug.
- **Phase 2 hook:** `BladerfDevice.capture` already yields raw complex IQ — the Phase-2 demod will reuse it directly; keep that method general (no spectrum-only assumptions).
