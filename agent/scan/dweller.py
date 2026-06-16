import os
import subprocess
import tempfile

import numpy as np

from models import Features

_EPS = 1e-12


def iq_from_int8(raw: bytes) -> np.ndarray:
    data = np.frombuffer(raw, dtype=np.int8).astype(np.float32)
    i = data[0::2]
    q = data[1::2]
    return (i + 1j * q) / 128.0


def _averaged_psd(iq: np.ndarray, seg: int = 512) -> np.ndarray:
    """Welch-style averaged power spectral density (NOT normalized)."""
    n = len(iq)
    if n < seg:
        seg = n
    if seg <= 0:
        return np.zeros(0)
    nseg = max(1, n // seg)
    win = np.hanning(seg)
    acc = np.zeros(seg)
    used = 0
    for k in range(nseg):
        chunk = iq[k * seg:(k + 1) * seg]
        if len(chunk) < seg:
            break
        acc += np.abs(np.fft.fftshift(np.fft.fft(chunk * win))) ** 2
        used += 1
    if used == 0:
        return np.zeros(seg)
    return acc / used


def compute_features(iq: np.ndarray, sample_rate_hz: float) -> Features:
    psd = _averaged_psd(iq)
    seg = len(psd)
    if seg == 0:
        return Features(occupied_bw_mhz=0.0, spectral_flatness=1.0, carrier_spike_ratio=0.0)
    # flatness (Wiener entropy) and carrier spike ratio are scale-invariant,
    # so they are computed on the raw (un-normalized) PSD.
    gmean = float(np.exp(np.mean(np.log(psd + _EPS))))
    amean = float(np.mean(psd)) + _EPS
    flatness = gmean / amean
    spike = float(np.max(psd) / (np.median(psd) + _EPS))
    # occupied bandwidth uses a threshold relative to the peak, so it needs normalization.
    peak = float(np.max(psd))
    norm = psd / peak if peak > 0 else psd
    occ_frac = float(np.sum(norm >= 0.01)) / seg     # bins within -20 dB of peak
    occupied_bw_mhz = occ_frac * (sample_rate_hz / 1e6)
    return Features(
        occupied_bw_mhz=occupied_bw_mhz,
        spectral_flatness=flatness,
        carrier_spike_ratio=spike,
    )


def build_transfer_cmd(center_hz: float, sample_rate_hz: float, num_samples: int, out_path: str) -> list:
    """center_hz is in Hz (note: build_sweep_cmd takes MHz)."""
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
        try:
            subprocess.run(cmd, capture_output=True, timeout=timeout, check=True)
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode(errors="replace") if isinstance(e.stderr, (bytes, bytearray)) else (e.stderr or "")
            raise RuntimeError(f"hackrf_transfer failed (exit {e.returncode}): {stderr}") from e
        with open(path, "rb") as f:
            raw = f.read()
        if not raw:
            raise RuntimeError("hackrf_transfer captured 0 bytes")
        return iq_from_int8(raw)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def dwell_replay(iq_path: str) -> np.ndarray:
    with open(iq_path, "rb") as f:
        raw = f.read()
    return iq_from_int8(raw)
