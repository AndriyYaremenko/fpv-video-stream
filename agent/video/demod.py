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
