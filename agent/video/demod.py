import numpy as np


def fm_demod(iq, median_stride=64):
    """FM-demodulate: instantaneous frequency via phase differencing, de-carriered.

    angle(iq[n] * conj(iq[n-1])) is the per-sample phase advance; subtracting the
    median removes the residual carrier offset. The DC estimate uses a strided
    subsample (median_stride=1 restores the exact full median): statistically
    identical on millions of samples, ~60x cheaper than sorting them all.
    complex64 in -> float32 out (no float64 promotion)."""
    if len(iq) < 2:
        return np.zeros(0, dtype=np.float32)
    inst = np.angle(iq[1:] * np.conj(iq[:-1]))
    step = max(1, int(median_stride))
    return inst - inst.dtype.type(np.median(inst[::step]))


def lowpass(x, fs, cutoff_hz):
    """Moving-average low-pass (cumsum), length-preserving, edge-padded.

    Window ~ fs/cutoff. float32 in/out (halves the memory traffic of the view
    chain); the running sum accumulates in float64 — a float32 cumsum over
    millions of samples loses precision."""
    x = np.asarray(x)
    if x.dtype != np.float32:
        x = x.astype(np.float32)
    win = int(round(fs / cutoff_hz)) if cutoff_hz > 0 else 1
    if win <= 1 or win >= len(x):
        return x.copy()
    c = np.cumsum(np.insert(x, 0, np.float32(0.0)), dtype=np.float64)
    ma = ((c[win:] - c[:-win]) / win).astype(np.float32)
    pad_total = len(x) - len(ma)
    pad_left = pad_total // 2
    pad_right = pad_total - pad_left
    return np.concatenate([np.full(pad_left, ma[0], dtype=np.float32), ma,
                           np.full(pad_right, ma[-1], dtype=np.float32)])
