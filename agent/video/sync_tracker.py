"""Per-session sync lock state for the SDR live-view stream.

Seeds the ACTUAL line frequency once from an rfft peak (a real FPV camera's
crystal is off nominal by tens-hundreds of Hz within +/-2% of nominal; slicing
at the nominal rate shears the picture) and carries the vertical-blanking row
across chunks so the picture does not jump. View-path only; the scan snapshot
path never builds one.

Locks only within +/-2% of nominal (every real FPV crystal, with margin) and
only when a prominent peak has a prominent 2nd harmonic at exactly 2x the
candidate. Offsets beyond a real crystal's range are not a valid PAL/NTSC
line; the gates reject the common cases but a wrong lock on such non-physical
input is out of contract."""
import numpy as np

from standard import LINE_HZ

_CLAMP = 0.020               # accept a refined line rate within +/-2% of nominal (crystal bound)
_SEARCH = 0.020              # search the rfft peak within +/-2% of nominal
_MIN_PROMINENCE = 4.0        # fundamental peak / local floor to be a candidate
_MIN_HARM_PROMINENCE = 2.5   # 2nd-harmonic peak / local floor to CONFIRM a real line rate


def _peak_prominence(spec, k_center, half_win, floor_half):
    """(peak/local-floor ratio, peak bin) for a magnitude spectrum around k_center."""
    lo, hi = max(1, k_center - half_win), min(len(spec) - 1, k_center + half_win)
    if hi <= lo:
        return 0.0, k_center
    k = lo + int(np.argmax(spec[lo:hi + 1]))
    peak = float(spec[k])
    flo, fhi = max(1, k_center - floor_half), min(len(spec), k_center + floor_half)
    floor = float(np.median(spec[flo:fhi])) + 1e-12
    return peak / floor, k


class SyncTracker:
    def __init__(self, standard):
        self.standard = standard
        self._nominal = float(LINE_HZ[standard])
        self.line_hz = self._nominal
        self.locked = False
        self.vsync_row = None

    def seed(self, baseband, fs):
        """One-time actual-line-rate estimate. Locks only within +/-2% of nominal
        (every real FPV crystal, with margin) and only when a prominent peak
        near nominal ALSO has a prominent 2nd harmonic at exactly 2x the
        candidate (a real line-rate fundamental does; in-window CVBS artifacts
        do not). Any failure leaves line_hz at nominal + locked=False. Offsets
        beyond a real crystal's range are not a valid PAL/NTSC line and are out
        of contract: the gates reject the common cases but a wrong lock on such
        non-physical input is not guaranteed against."""
        bb = np.asarray(baseband, dtype=np.float64)
        n = len(bb)
        self.locked = False
        self.line_hz = self._nominal          # a failed (re)seed never keeps a stale rate
        if n < 4096:
            return
        spec = np.abs(np.fft.rfft(bb * np.hanning(n)))
        bin_hz = fs / n
        k0 = int(round(self._nominal / bin_hz))
        half = max(2, int(round(self._nominal * _SEARCH / bin_hz)))
        prom, k = _peak_prominence(spec, k0, half, 5 * half)
        if prom < _MIN_PROMINENCE:
            return
        if 0 < k < len(spec) - 1:
            a, b, c = spec[k - 1], spec[k], spec[k + 1]
            denom = a - 2 * b + c
            delta = float(np.clip(0.5 * (a - c) / denom, -0.5, 0.5)) if abs(denom) > 1e-12 else 0.0
        else:
            delta = 0.0
        f = (k + delta) * bin_hz
        if abs(f - self._nominal) > self._nominal * _CLAMP:
            return
        # confirm a real line rate: the 2nd harmonic must also stand out
        k2 = int(round(2.0 * f / bin_hz))
        harm, _ = _peak_prominence(spec, k2, 3, 5 * half)   # tight: a companion at exactly 2*candidate
        if harm < _MIN_HARM_PROMINENCE:
            return                            # in-window artifact, not the line fundamental
        self.line_hz = float(f)
        self.locked = True

    def note_vsync(self, row):
        self.vsync_row = int(row)

    def status(self):
        return {"line_hz": self.line_hz, "locked": self.locked, "vsync_row": self.vsync_row}
