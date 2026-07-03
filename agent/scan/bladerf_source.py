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
