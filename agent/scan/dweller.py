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
