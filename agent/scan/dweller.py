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
