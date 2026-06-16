# HackRF Scan Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Python daemon on a Raspberry Pi that sweeps 1.2/2.4/5.8 GHz with a HackRF One, detects active video carriers, classifies each as analog / digital / unknown, and reports detections to the existing dashboard telemetry hook plus a local state file.

**Architecture:** One process owns the single half-duplex HackRF. Each cycle: `hackrf_sweep` → parse spectrum → detect candidate carriers → for each (strongest-first) capture a short IQ block via `hackrf_transfer` → compute PSD features → classify. Pure modules (parser, detector, features, classifier, channel map, reporter) are hardware-free and TDD'd on the dev box; a built-in **replay mode** feeds recorded fixtures so the whole pipeline runs without a HackRF.

**Tech Stack:** Python 3.11+, numpy, requests; pytest for tests; stdlib `subprocess`/`http`/`json`; `hackrf` CLI tools (`hackrf_sweep`, `hackrf_transfer`, `hackrf_info`) on the Pi; systemd.

Spec: `docs/superpowers/specs/2026-06-16-hackrf-scan-service-design.md`

---

## File Structure

```
agent/scan/
  __init__.py
  conftest.py          # puts agent/scan on sys.path so tests use flat imports
  requirements.txt     # numpy, requests, pytest
  models.py            # dataclasses: Spectrum, Candidate, Features, Detection
  config.py            # Config + Thresholds + band plan; load_config() from env
  sweeper.py           # parse_sweep_output, build_sweep_cmd, sweep_live, sweep_replay
  detector.py          # find_candidates
  dweller.py           # iq_from_int8, compute_features, build_transfer_cmd, dwell_live, dwell_replay
  classifier.py        # classify
  channel_map.py       # nearest_channel + channel tables
  reporter.py          # build_payload, write_state, post_telemetry
  main.py              # run_cycle, main loop, HackRF ownership, backoff
  tests/
    test_models.py
    test_config.py
    test_sweeper.py
    test_detector.py
    test_dweller.py
    test_classifier.py
    test_channel_map.py
    test_reporter.py
    test_live_cmds.py
    test_replay.py
    test_run_cycle.py
systemd/
  fpv-scan.service
README.md              # add a "Scan service" section
```

All modules in `agent/scan/` import each other flat (`from models import Detection`). `conftest.py` makes that work under pytest. Run tests from `agent/scan/`.

---

## Task 1: Scaffold + data models

**Files:**
- Create: `agent/scan/__init__.py` (empty)
- Create: `agent/scan/conftest.py`
- Create: `agent/scan/requirements.txt`
- Create: `agent/scan/models.py`
- Test: `agent/scan/tests/test_models.py`

- [ ] **Step 1: Create the package skeleton**

Create `agent/scan/__init__.py` (empty file).

Create `agent/scan/conftest.py`:

```python
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
```

Create `agent/scan/requirements.txt`:

```
numpy>=1.26
requests>=2.31
pytest>=8.0
```

- [ ] **Step 2: Create the Python venv and install deps**

Run (from `agent/scan/`, PowerShell on the Windows dev box):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Expected: numpy, requests, pytest install cleanly. (On the Pi later: `python3 -m venv` + same.)

- [ ] **Step 3: Write the failing test**

Create `agent/scan/tests/test_models.py`:

```python
from models import Detection


def test_detection_to_dict_serializes_class_key():
    d = Detection(
        ts=1718530000,
        band="5.8G",
        center_mhz=5800.0,
        bandwidth_mhz=22.0,
        power_dbm=-47.0,
        snr_db=28.0,
        signal_class="analog",
        confidence=0.82,
        channel="F4",
    )
    out = d.to_dict()
    assert out["class"] == "analog"          # python keyword -> json "class"
    assert "signal_class" not in out
    assert out["center_mhz"] == 5800.0
    assert out["channel"] == "F4"
```

- [ ] **Step 4: Run test to verify it fails**

Run: `python -m pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'models'`.

- [ ] **Step 5: Write minimal implementation**

Create `agent/scan/models.py`:

```python
from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class Spectrum:
    band: str
    freqs_mhz: np.ndarray
    power_dbm: np.ndarray


@dataclass
class Candidate:
    band: str
    center_mhz: float
    bandwidth_mhz: float
    power_dbm: float
    snr_db: float


@dataclass
class Features:
    occupied_bw_mhz: float
    spectral_flatness: float
    carrier_spike_ratio: float


@dataclass
class Detection:
    ts: int
    band: str
    center_mhz: float
    bandwidth_mhz: float
    power_dbm: float
    snr_db: float
    signal_class: str            # "analog" | "digital" | "unknown"
    confidence: float
    channel: Optional[str]

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "band": self.band,
            "center_mhz": self.center_mhz,
            "bandwidth_mhz": self.bandwidth_mhz,
            "power_dbm": self.power_dbm,
            "snr_db": self.snr_db,
            "class": self.signal_class,
            "confidence": self.confidence,
            "channel": self.channel,
        }
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/test_models.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add agent/scan/__init__.py agent/scan/conftest.py agent/scan/requirements.txt agent/scan/models.py agent/scan/tests/test_models.py
git commit -m "feat(scan): package scaffold + data models" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Config + thresholds + band plan

**Files:**
- Create: `agent/scan/config.py`
- Test: `agent/scan/tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Create `agent/scan/tests/test_config.py`:

```python
from config import load_config, Config


def test_defaults():
    c = load_config({})
    assert c.scanner_id == "scan-01"
    assert c.source == "live"
    assert set(c.bands.keys()) == {"1.2G", "2.4G", "5.8G"}
    assert c.bands["5.8G"] == (5645.0, 5945.0)
    assert c.thresholds.snr_threshold_db == 20.0


def test_env_overrides():
    env = {
        "SCAN_ID": "scan-09",
        "SCAN_SOURCE": "replay",
        "SCAN_SERVER_URL": "http://10.8.0.1:8080",
        "SCAN_FIXTURES_DIR": "/tmp/fx",
    }
    c = load_config(env)
    assert c.scanner_id == "scan-09"
    assert c.source == "replay"
    assert c.fixtures_dir == "/tmp/fx"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'config'`.

- [ ] **Step 3: Write minimal implementation**

Create `agent/scan/config.py`:

```python
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple


@dataclass
class Thresholds:
    snr_threshold_db: float = 20.0
    min_bandwidth_mhz: float = 5.0
    flatness_thresh: float = 0.4
    spike_thresh: float = 50.0
    analog_bw_min_mhz: float = 10.0
    analog_bw_max_mhz: float = 30.0
    digital_bw_min_mhz: float = 15.0
    occupancy_snr_db: float = 10.0


@dataclass
class Config:
    scanner_id: str = "scan-01"
    server_url: str = "http://10.8.0.1:8080"
    server_token: str = ""
    source: str = "live"                       # "live" | "replay"
    fixtures_dir: str = ""
    state_path: str = "/run/fpv-scan/scan.json"
    dwell_sample_rate_hz: float = 20_000_000.0
    dwell_num_samples: int = 2_000_000
    max_dwells_per_cycle: int = 12
    sweep_bin_hz: float = 100_000.0
    bands: Dict[str, Tuple[float, float]] = field(default_factory=lambda: {
        "1.2G": (1080.0, 1360.0),
        "2.4G": (2370.0, 2510.0),
        "5.8G": (5645.0, 5945.0),
    })
    thresholds: Thresholds = field(default_factory=Thresholds)


def load_config(env: Optional[dict] = None) -> Config:
    import os
    env = os.environ if env is None else env
    c = Config()
    c.scanner_id = env.get("SCAN_ID", c.scanner_id)
    c.server_url = env.get("SCAN_SERVER_URL", c.server_url)
    c.server_token = env.get("SCAN_SERVER_TOKEN", c.server_token)
    c.source = env.get("SCAN_SOURCE", c.source)
    c.fixtures_dir = env.get("SCAN_FIXTURES_DIR", c.fixtures_dir)
    c.state_path = env.get("SCAN_STATE_PATH", c.state_path)
    return c
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add agent/scan/config.py agent/scan/tests/test_config.py
git commit -m "feat(scan): config, thresholds, band plan" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Sweep CSV parser

**Files:**
- Create: `agent/scan/sweeper.py` (parser only in this task)
- Test: `agent/scan/tests/test_sweeper.py`

Background: `hackrf_sweep` prints CSV rows of the form
`date, time, hz_low, hz_high, hz_bin_width, num_samples, dB, dB, …` where each trailing dB is one
FFT bin from `hz_low`. Bin `i` center = `hz_low + bin_width * (i + 0.5)`.

- [ ] **Step 1: Write the failing test**

Create `agent/scan/tests/test_sweeper.py`:

```python
import numpy as np

from sweeper import parse_sweep_output


def test_parse_sweep_output_basic():
    lines = [
        "2024-01-01, 12:00:00.0, 5645000000, 5650000000, 1000000.0, 20, -90.0, -88.0, -50.0, -89.0, -90.0",
    ]
    spec = parse_sweep_output(lines, "5.8G")
    assert spec.band == "5.8G"
    assert len(spec.freqs_mhz) == 5
    assert abs(spec.freqs_mhz[0] - 5645.5) < 1e-6     # first bin center
    assert spec.power_dbm[2] == -50.0


def test_parse_sweep_output_sorts_and_skips_blank():
    lines = [
        "",
        "2024-01-01, 12:00:00.0, 5650000000, 5652000000, 1000000.0, 20, -70.0, -71.0",
        "2024-01-01, 12:00:00.0, 5645000000, 5647000000, 1000000.0, 20, -90.0, -91.0",
    ]
    spec = parse_sweep_output(lines, "5.8G")
    assert len(spec.freqs_mhz) == 4
    assert list(spec.freqs_mhz) == sorted(spec.freqs_mhz)
    assert spec.freqs_mhz[0] < spec.freqs_mhz[-1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sweeper.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sweeper'`.

- [ ] **Step 3: Write minimal implementation**

Create `agent/scan/sweeper.py`:

```python
from typing import Iterable, List

import numpy as np

from models import Spectrum


def parse_sweep_output(lines: Iterable[str], band: str) -> Spectrum:
    freqs: List[float] = []
    powers: List[float] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 7:
            continue
        hz_low = float(parts[2])
        bin_w = float(parts[4])
        db_vals = [float(x) for x in parts[6:]]
        for i, db in enumerate(db_vals):
            center_hz = hz_low + bin_w * (i + 0.5)
            freqs.append(center_hz / 1e6)
            powers.append(db)
    f = np.array(freqs, dtype=float)
    p = np.array(powers, dtype=float)
    order = np.argsort(f)
    return Spectrum(band=band, freqs_mhz=f[order], power_dbm=p[order])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_sweeper.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add agent/scan/sweeper.py agent/scan/tests/test_sweeper.py
git commit -m "feat(scan): hackrf_sweep CSV parser" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Detector (spectrum → candidate carriers)

**Files:**
- Create: `agent/scan/detector.py`
- Test: `agent/scan/tests/test_detector.py`

- [ ] **Step 1: Write the failing test**

Create `agent/scan/tests/test_detector.py`:

```python
import numpy as np

from models import Spectrum
from detector import find_candidates


def _spectrum_with_bump():
    freqs = np.arange(5645.0, 5945.0, 0.1)
    power = np.full(freqs.shape, -90.0)
    bump = (freqs >= 5789.0) & (freqs <= 5811.0)   # ~22 MHz wide
    power[bump] = -50.0
    return Spectrum(band="5.8G", freqs_mhz=freqs, power_dbm=power)


def test_find_single_candidate():
    cands = find_candidates(_spectrum_with_bump(), snr_threshold_db=20.0, min_bandwidth_mhz=5.0)
    assert len(cands) == 1
    c = cands[0]
    assert abs(c.center_mhz - 5800.0) < 1.0
    assert 20.0 < c.bandwidth_mhz < 24.0
    assert c.power_dbm == -50.0
    assert c.snr_db == 40.0
    assert c.band == "5.8G"


def test_noise_only_yields_nothing():
    freqs = np.arange(5645.0, 5945.0, 0.1)
    power = np.full(freqs.shape, -90.0)
    spec = Spectrum(band="5.8G", freqs_mhz=freqs, power_dbm=power)
    assert find_candidates(spec, snr_threshold_db=20.0, min_bandwidth_mhz=5.0) == []


def test_narrow_blip_below_min_bandwidth_is_ignored():
    freqs = np.arange(5645.0, 5945.0, 0.1)
    power = np.full(freqs.shape, -90.0)
    power[(freqs >= 5800.0) & (freqs <= 5801.0)] = -40.0   # ~1 MHz
    spec = Spectrum(band="5.8G", freqs_mhz=freqs, power_dbm=power)
    assert find_candidates(spec, snr_threshold_db=20.0, min_bandwidth_mhz=5.0) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_detector.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'detector'`.

- [ ] **Step 3: Write minimal implementation**

Create `agent/scan/detector.py`:

```python
from typing import List

import numpy as np

from models import Spectrum, Candidate


def find_candidates(
    spectrum: Spectrum,
    snr_threshold_db: float,
    min_bandwidth_mhz: float,
    noise_percentile: float = 50.0,
) -> List[Candidate]:
    power = spectrum.power_dbm
    freqs = spectrum.freqs_mhz
    if len(power) == 0:
        return []
    noise_floor = float(np.percentile(power, noise_percentile))
    mask = power > (noise_floor + snr_threshold_db)

    candidates: List[Candidate] = []
    n = len(mask)
    idx = 0
    while idx < n:
        if not mask[idx]:
            idx += 1
            continue
        start = idx
        while idx < n and mask[idx]:
            idx += 1
        end = idx - 1
        lo = float(freqs[start])
        hi = float(freqs[end])
        bw = hi - lo
        if bw >= min_bandwidth_mhz:
            run_power = power[start:end + 1]
            peak = float(np.max(run_power))
            candidates.append(Candidate(
                band=spectrum.band,
                center_mhz=(lo + hi) / 2.0,
                bandwidth_mhz=bw,
                power_dbm=peak,
                snr_db=peak - noise_floor,
            ))
    return candidates
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_detector.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add agent/scan/detector.py agent/scan/tests/test_detector.py
git commit -m "feat(scan): carrier detector" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Dweller (IQ decode + PSD features)

**Files:**
- Create: `agent/scan/dweller.py` (decode + features in this task; live/replay capture in Tasks 9–10)
- Test: `agent/scan/tests/test_dweller.py`

- [ ] **Step 1: Write the failing test**

Create `agent/scan/tests/test_dweller.py`:

```python
import numpy as np

from dweller import iq_from_int8, compute_features


def test_iq_from_int8_decodes_pairs():
    raw = bytes([127, 0, 0, 127])          # I,Q interleaved int8
    iq = iq_from_int8(raw)
    assert iq.shape == (2,)
    assert abs(iq[0] - (127 / 128.0)) < 1e-6
    assert abs(iq[1].imag - (127 / 128.0)) < 1e-6


def test_features_tone_is_peaky():
    fs = 20_000_000.0
    n = 40_000
    t = np.arange(n) / fs
    tone = np.exp(2j * np.pi * 1.0e6 * t)
    f = compute_features(tone, fs)
    assert f.spectral_flatness < 0.15        # very peaky
    assert f.carrier_spike_ratio > 50.0


def test_features_noise_is_flat():
    fs = 20_000_000.0
    n = 40_000
    rng = np.random.default_rng(0)
    noise = rng.standard_normal(n) + 1j * rng.standard_normal(n)
    f = compute_features(noise, fs)
    assert f.spectral_flatness > 0.25        # noise-like
    assert f.carrier_spike_ratio < 50.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_dweller.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dweller'`.

- [ ] **Step 3: Write minimal implementation**

Create `agent/scan/dweller.py`:

```python
import numpy as np

from models import Features

_EPS = 1e-12


def iq_from_int8(raw: bytes) -> np.ndarray:
    data = np.frombuffer(raw, dtype=np.int8).astype(np.float32)
    i = data[0::2]
    q = data[1::2]
    return (i + 1j * q) / 128.0


def _averaged_psd(iq: np.ndarray, seg: int = 512) -> np.ndarray:
    n = len(iq)
    if n < seg:
        seg = n
    nseg = max(1, n // seg)
    win = np.hanning(seg)
    acc = np.zeros(seg)
    for k in range(nseg):
        chunk = iq[k * seg:(k + 1) * seg]
        if len(chunk) < seg:
            break
        spec = np.abs(np.fft.fftshift(np.fft.fft(chunk * win))) ** 2
        acc += spec
    psd = acc / nseg
    peak = np.max(psd)
    if peak <= 0:
        return psd
    return psd / peak


def compute_features(iq: np.ndarray, sample_rate_hz: float) -> Features:
    psd = _averaged_psd(iq)
    seg = len(psd)
    gmean = float(np.exp(np.mean(np.log(psd + _EPS))))
    amean = float(np.mean(psd + _EPS))
    flatness = gmean / amean
    spike = float(np.max(psd) / (np.median(psd) + _EPS))
    occ_frac = float(np.sum(psd >= 0.01)) / seg     # bins within -20 dB of peak
    occupied_bw_mhz = occ_frac * (sample_rate_hz / 1e6)
    return Features(
        occupied_bw_mhz=occupied_bw_mhz,
        spectral_flatness=flatness,
        carrier_spike_ratio=spike,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_dweller.py -v`
Expected: PASS (3 tests). If a numeric bound is marginal on your machine, widen the gap (tone vs noise are clearly separated); do not move both bounds to the same value.

- [ ] **Step 5: Commit**

```bash
git add agent/scan/dweller.py agent/scan/tests/test_dweller.py
git commit -m "feat(scan): IQ decode + PSD features" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Classifier

**Files:**
- Create: `agent/scan/classifier.py`
- Test: `agent/scan/tests/test_classifier.py`

- [ ] **Step 1: Write the failing test**

Create `agent/scan/tests/test_classifier.py`:

```python
from config import Thresholds
from models import Features
from classifier import classify


T = Thresholds()


def test_analog_signature():
    f = Features(occupied_bw_mhz=22.0, spectral_flatness=0.05, carrier_spike_ratio=200.0)
    cls, conf = classify(f, T)
    assert cls == "analog"
    assert 0.0 < conf <= 1.0


def test_digital_signature():
    f = Features(occupied_bw_mhz=30.0, spectral_flatness=0.8, carrier_spike_ratio=5.0)
    cls, conf = classify(f, T)
    assert cls == "digital"
    assert 0.0 < conf <= 1.0


def test_unknown_signature():
    f = Features(occupied_bw_mhz=3.0, spectral_flatness=0.3, carrier_spike_ratio=15.0)
    cls, conf = classify(f, T)
    assert cls == "unknown"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_classifier.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'classifier'`.

- [ ] **Step 3: Write minimal implementation**

Create `agent/scan/classifier.py`:

```python
from typing import Tuple

from config import Thresholds
from models import Features


def _confidence(ratio: float) -> float:
    # ratio >= 1 means the feature clears its threshold; map to (0.5, 1.0]
    return float(min(1.0, 0.5 + 0.25 * min(max(ratio, 0.0), 2.0)))


def classify(features: Features, t: Thresholds) -> Tuple[str, float]:
    f = features
    is_peaky = f.spectral_flatness < t.flatness_thresh
    has_spike = f.carrier_spike_ratio > t.spike_thresh
    analog_bw = t.analog_bw_min_mhz <= f.occupied_bw_mhz <= t.analog_bw_max_mhz

    if is_peaky and has_spike and analog_bw:
        return "analog", _confidence(f.carrier_spike_ratio / t.spike_thresh)

    is_flat = f.spectral_flatness >= t.flatness_thresh
    no_spike = f.carrier_spike_ratio <= t.spike_thresh
    digital_bw = f.occupied_bw_mhz >= t.digital_bw_min_mhz

    if is_flat and no_spike and digital_bw:
        return "digital", _confidence(f.spectral_flatness / t.flatness_thresh)

    return "unknown", 0.4
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_classifier.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add agent/scan/classifier.py agent/scan/tests/test_classifier.py
git commit -m "feat(scan): analog/digital/unknown classifier" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: Channel map

**Files:**
- Create: `agent/scan/channel_map.py`
- Test: `agent/scan/tests/test_channel_map.py`

- [ ] **Step 1: Write the failing test**

Create `agent/scan/tests/test_channel_map.py`:

```python
from channel_map import nearest_channel


def test_maps_to_nearest_5g8_channel():
    assert nearest_channel(5801.0) == "F4"      # F4 = 5800


def test_returns_none_when_far():
    assert nearest_channel(5500.0) is None


def test_maps_12g_channel():
    assert nearest_channel(1161.0) == "L3"      # L3 = 1160
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_channel_map.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'channel_map'`.

- [ ] **Step 3: Write minimal implementation**

Create `agent/scan/channel_map.py`:

```python
from typing import Optional

# Representative FPV channel centers (MHz). Informational only.
CHANNELS = {
    # 5.8 GHz — Raceband (R) + Fatshark (F)
    "R1": 5658, "R2": 5695, "R3": 5732, "R4": 5769,
    "R5": 5806, "R6": 5843, "R7": 5880, "R8": 5917,
    "F1": 5740, "F2": 5760, "F3": 5780, "F4": 5800,
    "F5": 5820, "F6": 5840, "F7": 5860, "F8": 5880,
    # 1.2 GHz (L)
    "L1": 1080, "L2": 1120, "L3": 1160, "L4": 1200,
    "L5": 1240, "L6": 1280, "L7": 1320, "L8": 1360,
    # 2.4 GHz (G)
    "G1": 2414, "G2": 2432, "G3": 2450, "G4": 2468, "G5": 2490,
}


def nearest_channel(center_mhz: float, tolerance_mhz: float = 10.0) -> Optional[str]:
    best = None
    best_d = tolerance_mhz
    for name, freq in CHANNELS.items():
        d = abs(freq - center_mhz)
        if d <= best_d:
            best_d = d
            best = name
    return best
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_channel_map.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add agent/scan/channel_map.py agent/scan/tests/test_channel_map.py
git commit -m "feat(scan): freq -> FPV channel mapping" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 8: Reporter (payload + state file + best-effort POST)

**Files:**
- Create: `agent/scan/reporter.py`
- Test: `agent/scan/tests/test_reporter.py`

- [ ] **Step 1: Write the failing test**

Create `agent/scan/tests/test_reporter.py`:

```python
import json

import reporter
from models import Detection


def _det():
    return Detection(
        ts=1, band="5.8G", center_mhz=5800.0, bandwidth_mhz=22.0, power_dbm=-47.0,
        snr_db=28.0, signal_class="analog", confidence=0.8, channel="F4",
    )


def test_build_payload_shape():
    p = reporter.build_payload("scan-01", 1718530000, [_det()], {"5.8G": 0.5}, {"5.8G": [-90.0]})
    assert p["scanner_id"] == "scan-01"
    assert p["detections"][0]["class"] == "analog"
    assert p["occupancy"]["5.8G"] == 0.5


def test_write_state_roundtrip(tmp_path):
    path = tmp_path / "scan.json"
    payload = reporter.build_payload("scan-01", 1, [_det()], {"5.8G": 0.5}, {})
    reporter.write_state(str(path), payload)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded == payload


def test_post_telemetry_swallows_errors(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(reporter.requests, "post", boom)
    ok = reporter.post_telemetry("http://10.8.0.1:8080", "", "scan-01", {"ts": 1})
    assert ok is False        # never raises; returns False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_reporter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'reporter'`.

- [ ] **Step 3: Write minimal implementation**

Create `agent/scan/reporter.py`:

```python
import json
import os
from typing import Dict, List

import requests

from models import Detection


def build_payload(
    scanner_id: str,
    ts: int,
    detections: List[Detection],
    occupancy: Dict[str, float],
    spectrum: Dict[str, list],
) -> dict:
    return {
        "scanner_id": scanner_id,
        "ts": ts,
        "detections": [d.to_dict() for d in detections],
        "occupancy": occupancy,
        "spectrum": spectrum,
    }


def write_state(path: str, payload: dict) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    os.replace(tmp, path)


def post_telemetry(url: str, token: str, scanner_id: str, payload: dict, timeout: float = 3.0) -> bool:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    endpoint = f"{url.rstrip('/')}/api/telemetry/{scanner_id}"
    try:
        requests.post(endpoint, json=payload, headers=headers, timeout=timeout)
        return True
    except Exception:
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_reporter.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add agent/scan/reporter.py agent/scan/tests/test_reporter.py
git commit -m "feat(scan): reporter (state file + best-effort telemetry POST)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 9: Live HackRF command builders

**Files:**
- Modify: `agent/scan/sweeper.py` (add `build_sweep_cmd`, `sweep_live`)
- Modify: `agent/scan/dweller.py` (add `build_transfer_cmd`, `dwell_live`)
- Test: `agent/scan/tests/test_live_cmds.py`

Only the pure argv builders are unit-tested (no hardware). `sweep_live`/`dwell_live` are thin
subprocess wrappers validated by the on-Pi smoke test in Task 12.

- [ ] **Step 1: Write the failing test**

Create `agent/scan/tests/test_live_cmds.py`:

```python
from sweeper import build_sweep_cmd
from dweller import build_transfer_cmd


def test_build_sweep_cmd():
    cmd = build_sweep_cmd(5645.0, 5945.0, 100_000.0)
    assert cmd[0] == "hackrf_sweep"
    assert "-f" in cmd and "5645:5945" in cmd
    assert "-w" in cmd and "100000" in cmd
    assert "-1" in cmd            # one-shot


def test_build_transfer_cmd():
    cmd = build_transfer_cmd(5_800_000_000.0, 20_000_000.0, 2_000_000, "/tmp/iq.bin")
    assert cmd[0] == "hackrf_transfer"
    assert "-r" in cmd and "/tmp/iq.bin" in cmd
    assert "-f" in cmd and "5800000000" in cmd
    assert "-s" in cmd and "20000000" in cmd
    assert "-n" in cmd and "2000000" in cmd
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_live_cmds.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_sweep_cmd'`.

- [ ] **Step 3: Write minimal implementation**

Add to the end of `agent/scan/sweeper.py`:

```python
import subprocess


def build_sweep_cmd(low_mhz: float, high_mhz: float, bin_hz: float) -> list:
    return [
        "hackrf_sweep",
        "-f", f"{int(low_mhz)}:{int(high_mhz)}",
        "-w", str(int(bin_hz)),
        "-1",
    ]


def sweep_live(low_mhz: float, high_mhz: float, bin_hz: float, timeout: float = 15.0) -> list:
    cmd = build_sweep_cmd(low_mhz, high_mhz, bin_hz)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=True)
    return proc.stdout.splitlines()
```

Add to the end of `agent/scan/dweller.py`:

```python
import os
import subprocess
import tempfile


def build_transfer_cmd(center_hz: float, sample_rate_hz: float, num_samples: int, out_path: str) -> list:
    return [
        "hackrf_transfer",
        "-r", out_path,
        "-f", str(int(center_hz)),
        "-s", str(int(sample_rate_hz)),
        "-n", str(int(num_samples)),
        "-a", "1",
    ]


def dwell_live(center_mhz: float, sample_rate_hz: float, num_samples: int, timeout: float = 15.0) -> np.ndarray:
    fd, path = tempfile.mkstemp(suffix=".bin")
    os.close(fd)
    try:
        cmd = build_transfer_cmd(center_mhz * 1e6, sample_rate_hz, num_samples, path)
        subprocess.run(cmd, capture_output=True, timeout=timeout, check=True)
        with open(path, "rb") as f:
            raw = f.read()
        return iq_from_int8(raw)
    finally:
        os.unlink(path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_live_cmds.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add agent/scan/sweeper.py agent/scan/dweller.py agent/scan/tests/test_live_cmds.py
git commit -m "feat(scan): live hackrf_sweep/hackrf_transfer drivers" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 10: Replay drivers

**Files:**
- Modify: `agent/scan/sweeper.py` (add `sweep_replay`)
- Modify: `agent/scan/dweller.py` (add `dwell_replay`)
- Test: `agent/scan/tests/test_replay.py`

- [ ] **Step 1: Write the failing test**

Create `agent/scan/tests/test_replay.py`:

```python
import numpy as np

from sweeper import sweep_replay
from dweller import dwell_replay


def test_sweep_replay_reads_csv(tmp_path):
    csv = tmp_path / "sweep_5.8G.csv"
    csv.write_text(
        "2024-01-01, 12:00:00.0, 5645000000, 5650000000, 1000000.0, 20, -90.0, -50.0, -90.0\n",
        encoding="utf-8",
    )
    spec = sweep_replay(str(csv), "5.8G")
    assert spec.band == "5.8G"
    assert len(spec.power_dbm) == 3
    assert spec.power_dbm[1] == -50.0


def test_dwell_replay_reads_iq(tmp_path):
    binp = tmp_path / "iq_5.8G.bin"
    binp.write_bytes(bytes([127, 0, 0, 127]))
    iq = dwell_replay(str(binp))
    assert iq.shape == (2,)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_replay.py -v`
Expected: FAIL with `ImportError: cannot import name 'sweep_replay'`.

- [ ] **Step 3: Write minimal implementation**

Add to the end of `agent/scan/sweeper.py`:

```python
def sweep_replay(csv_path: str, band: str) -> Spectrum:
    with open(csv_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    return parse_sweep_output(lines, band)
```

Add to the end of `agent/scan/dweller.py`:

```python
def dwell_replay(iq_path: str) -> np.ndarray:
    with open(iq_path, "rb") as f:
        raw = f.read()
    return iq_from_int8(raw)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_replay.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add agent/scan/sweeper.py agent/scan/dweller.py agent/scan/tests/test_replay.py
git commit -m "feat(scan): replay-mode sweep/dwell drivers" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 11: Orchestration — `run_cycle` + entrypoint

**Files:**
- Create: `agent/scan/main.py`
- Test: `agent/scan/tests/test_run_cycle.py`

- [ ] **Step 1: Write the failing test**

Create `agent/scan/tests/test_run_cycle.py`. It builds a one-band replay fixture set in a temp dir (a sweep CSV with a ~22 MHz bump at 5800, and an IQ blob), runs a full cycle, and checks the wiring end-to-end. Class correctness is covered by the classifier/feature unit tests, so here we only assert a valid class and that everything is wired and persisted.

```python
import json

import numpy as np

from config import Config
import main


def _write_fixtures(tmp_path):
    # Sweep CSV: flat -90 dB with a 22 MHz bump at -50 dB around 5800 MHz, 1 MHz bins.
    lo = 5645_000000
    bins = []
    for i in range(300):                       # 5645..5945 MHz, 1 MHz bins
        f_mhz = 5645 + i
        bins.append(-50.0 if 5789 <= f_mhz <= 5811 else -90.0)
    row = ["2024-01-01", "12:00:00.0", str(lo), str(lo + 300_000000), "1000000.0", "20"]
    row += [str(x) for x in bins]
    (tmp_path / "sweep_5.8G.csv").write_text(", ".join(row) + "\n", encoding="utf-8")

    # IQ blob: a strong tone (int8) so the dwell has real samples to analyze.
    fs = 20_000_000.0
    n = 40_000
    t = np.arange(n) / fs
    tone = np.exp(2j * np.pi * 1.0e6 * t)
    iq8 = np.empty(2 * n, dtype=np.int8)
    iq8[0::2] = np.clip(np.real(tone) * 100, -127, 127).astype(np.int8)
    iq8[1::2] = np.clip(np.imag(tone) * 100, -127, 127).astype(np.int8)
    (tmp_path / "iq_5.8G.bin").write_bytes(iq8.tobytes())


def _config(tmp_path):
    c = Config()
    c.source = "replay"
    c.fixtures_dir = str(tmp_path)
    c.state_path = str(tmp_path / "scan.json")
    c.server_url = "http://127.0.0.1:1"        # unreachable -> POST silently fails
    c.bands = {"5.8G": (5645.0, 5945.0)}        # single band for the test
    return c


def test_run_cycle_end_to_end(tmp_path):
    _write_fixtures(tmp_path)
    cfg = _config(tmp_path)

    payload = main.run_cycle(cfg, now_ts=1718530000)

    assert payload["scanner_id"] == "scan-01"
    assert len(payload["detections"]) == 1
    det = payload["detections"][0]
    assert det["band"] == "5.8G"
    assert abs(det["center_mhz"] - 5800.0) < 2.0
    assert det["class"] in {"analog", "digital", "unknown"}
    assert payload["occupancy"]["5.8G"] > 0.0

    # state file persisted and equals the returned payload
    saved = json.loads((tmp_path / "scan.json").read_text(encoding="utf-8"))
    assert saved == payload
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_run_cycle.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'main'`.

- [ ] **Step 3: Write minimal implementation**

Create `agent/scan/main.py`:

```python
import logging
import os
import time
from typing import List

import numpy as np

from config import Config, load_config
from sweeper import parse_sweep_output, sweep_live, sweep_replay
from detector import find_candidates
from dweller import compute_features, dwell_live, dwell_replay
from classifier import classify
from channel_map import nearest_channel
from reporter import build_payload, write_state, post_telemetry
from models import Spectrum, Candidate, Detection

LOG = logging.getLogger("scan")


def _get_spectrum(cfg: Config, band: str, brange) -> Spectrum:
    if cfg.source == "replay":
        path = os.path.join(cfg.fixtures_dir, f"sweep_{band}.csv")
        return sweep_replay(path, band)
    lines = sweep_live(brange[0], brange[1], cfg.sweep_bin_hz)
    return parse_sweep_output(lines, band)


def _get_iq(cfg: Config, cand: Candidate) -> np.ndarray:
    if cfg.source == "replay":
        path = os.path.join(cfg.fixtures_dir, f"iq_{cand.band}.bin")
        return dwell_replay(path)
    return dwell_live(cand.center_mhz, cfg.dwell_sample_rate_hz, cfg.dwell_num_samples)


def _occupancy(spec: Spectrum, cfg: Config) -> float:
    if len(spec.power_dbm) == 0:
        return 0.0
    noise = float(np.percentile(spec.power_dbm, 50.0))
    busy = spec.power_dbm > (noise + cfg.thresholds.occupancy_snr_db)
    return round(float(np.sum(busy)) / len(busy), 3)


def _downsample(spec: Spectrum, points: int = 64) -> list:
    p = spec.power_dbm
    if len(p) <= points:
        return [round(float(x), 1) for x in p]
    idx = np.linspace(0, len(p) - 1, points).astype(int)
    return [round(float(p[i]), 1) for i in idx]


def run_cycle(cfg: Config, now_ts: int) -> dict:
    detections: List[Detection] = []
    occupancy = {}
    spectrum_summary = {}

    for band, brange in cfg.bands.items():
        spec = _get_spectrum(cfg, band, brange)
        occupancy[band] = _occupancy(spec, cfg)
        spectrum_summary[band] = _downsample(spec)

        cands = find_candidates(
            spec, cfg.thresholds.snr_threshold_db, cfg.thresholds.min_bandwidth_mhz
        )
        cands.sort(key=lambda c: c.power_dbm, reverse=True)

        budget = cfg.max_dwells_per_cycle
        for i, c in enumerate(cands):
            if i >= budget:
                LOG.info("deferred %d candidates in %s (budget=%d)", len(cands) - budget, band, budget)
                break
            iq = _get_iq(cfg, c)
            feat = compute_features(iq, cfg.dwell_sample_rate_hz)
            cls, conf = classify(feat, cfg.thresholds)
            detections.append(Detection(
                ts=now_ts,
                band=band,
                center_mhz=c.center_mhz,
                bandwidth_mhz=feat.occupied_bw_mhz if feat.occupied_bw_mhz > 0 else c.bandwidth_mhz,
                power_dbm=c.power_dbm,
                snr_db=c.snr_db,
                signal_class=cls,
                confidence=conf,
                channel=nearest_channel(c.center_mhz),
            ))

    payload = build_payload(cfg.scanner_id, now_ts, detections, occupancy, spectrum_summary)
    write_state(cfg.state_path, payload)
    post_telemetry(cfg.server_url, cfg.server_token, cfg.scanner_id, payload)
    return payload


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config()
    backoff = 1.0
    while True:
        try:
            run_cycle(cfg, now_ts=int(time.time()))
            backoff = 1.0
        except Exception:                       # hardware/subprocess failures: log + backoff
            LOG.exception("scan cycle failed; backing off %.0fs", backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
            continue
        time.sleep(1.0)                          # brief gap between cycles


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the full test suite**

Run: `python -m pytest tests -v`
Expected: PASS — all tests across every module green.

- [ ] **Step 5: Commit**

```bash
git add agent/scan/main.py agent/scan/tests/test_run_cycle.py
git commit -m "feat(scan): orchestration run_cycle + daemon entrypoint" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 12: systemd unit, README, and on-Pi smoke checklist

**Files:**
- Create: `systemd/fpv-scan.service`
- Modify: `README.md` (add "Scan service (HackRF)" section)

- [ ] **Step 1: Create the systemd unit**

Create `systemd/fpv-scan.service`:

```ini
[Unit]
Description=FPV HackRF scan service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/fpv-video-stream/agent/scan
Environment=SCAN_ID=scan-01
Environment=SCAN_SERVER_URL=http://10.8.0.1:8080
Environment=SCAN_SOURCE=live
Environment=SCAN_STATE_PATH=/run/fpv-scan/scan.json
RuntimeDirectory=fpv-scan
ExecStart=/opt/fpv-video-stream/agent/scan/.venv/bin/python -u main.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Add the README section**

Add a `## Scan service (HackRF)` section to `README.md` documenting:

````markdown
## Scan service (HackRF)

A Pi-side daemon (`agent/scan/`) sweeps 1.2/2.4/5.8 GHz with a HackRF One, detects active video
carriers, classifies analog vs digital, and POSTs detections to the dashboard telemetry hook
(`/api/telemetry/<scanner-id>`) plus a local state file (`/run/fpv-scan/scan.json`).

### Install on the Pi
```bash
sudo apt-get install -y hackrf
cd /opt/fpv-video-stream/agent/scan
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
hackrf_info                       # confirm the HackRF is detected
sudo cp ../../systemd/fpv-scan.service /etc/systemd/system/
sudo systemctl enable --now fpv-scan
journalctl -u fpv-scan -f
```

### Develop without a HackRF (replay mode)
```bash
SCAN_SOURCE=replay SCAN_FIXTURES_DIR=./tests/fixtures \
  SCAN_STATE_PATH=./scan.json python main.py
```

### Record real fixtures on the Pi (for threshold tuning)
```bash
hackrf_sweep -f 5645:5945 -w 100000 -1 > tests/fixtures/sweep_5.8G.csv
hackrf_transfer -r tests/fixtures/iq_5.8G.bin -f 5800000000 -s 20000000 -n 2000000 -a 1
```
Then tune `Thresholds` in `config.py` against these captures and re-run `pytest`.
````

- [ ] **Step 3: Run the full suite once more**

Run: `python -m pytest tests -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add systemd/fpv-scan.service README.md
git commit -m "docs(scan): systemd unit + README + on-Pi smoke checklist" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 5: On-Pi live smoke (manual, requires the HackRF)**

On the Pi with the HackRF attached:
1. `hackrf_info` → reports the device (serial, firmware). 
2. `SCAN_SOURCE=live SCAN_STATE_PATH=./scan.json python main.py` for one cycle; Ctrl-C.
3. Power on a known 5.8 GHz analog VTX → confirm a detection appears in `scan.json` with
   `"class": "analog"` and a plausible `center_mhz`/`channel`.
4. If classification is wrong, record fixtures (Step 2 instructions) and tune `config.Thresholds`.

---

## Self-Review

**Spec coverage** (spec §-by-§ → task):
- §3 sequential sweep→dwell loop on one HackRF → Task 11 `run_cycle` (+ mutual exclusion: one owner, sequential calls).
- §4 components → Tasks 2–11 (config, sweeper, detector, dweller, classifier, channel_map, reporter, main).
- §5.1 sweep+detection → Tasks 3, 4. §5.2 dwell+features+classify → Tasks 5, 6. §5.3 strongest-first + log deferred → Task 11 (`cands.sort` + `LOG.info`).
- §6 band plan → Task 2 `Config.bands`.
- §7 output schema → Task 1 `Detection.to_dict` + Task 8 `build_payload`.
- §8 reporting/no server change + local state file → Task 8 + Task 11.
- §9 error handling (absent/busy, hang, mutual exclusion, server unreachable, backstop) → Task 8 (`post_telemetry` swallows), Task 11 (`main` backoff loop + sequential ownership + `subprocess timeout`), Task 12 (`Restart=always`).
- §10 testing (replay mode, unit, integration, live smoke, fixtures) → Tasks 3–11 unit/integration + Task 12 smoke/fixtures.
- §11 deliverables file list → matches Tasks 1–12.

**Placeholder scan:** no TBD/TODO; every code step has complete code; commands have expected output.

**Type consistency:** `Spectrum(band, freqs_mhz, power_dbm)`, `Candidate(band, center_mhz, bandwidth_mhz, power_dbm, snr_db)`, `Features(occupied_bw_mhz, spectral_flatness, carrier_spike_ratio)`, `Detection(..., signal_class, ...)` with `to_dict()` → "class". `find_candidates(spectrum, snr_threshold_db, min_bandwidth_mhz, noise_percentile=50.0)`. `classify(features, thresholds) -> (str, float)`. `compute_features(iq, sample_rate_hz)`. `build_payload(scanner_id, ts, detections, occupancy, spectrum)`. These names are used identically across Tasks 1–12.

**Note on numeric test bounds:** the tone-vs-noise feature assertions (Task 5) and the analog/digital
thresholds (`config.Thresholds`, Task 6) are seeded from theory and may need widening/tuning against
real Pi captures (Task 12) — the gaps in the unit tests are deliberately generous so they remain
deterministic on the dev box.
