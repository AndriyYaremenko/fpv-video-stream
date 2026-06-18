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
    # Auto PAL/NTSC discrimination relies on the 109 Hz gap between 15625 and 15734;
    # the +/-2-bin peak window only resolves them when bin_hz = fs/n is small enough
    # (fine at the real operating points: degraded n~333k, normal n~3.2M). On a tie at
    # tiny n the first candidate (PAL) wins.
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
