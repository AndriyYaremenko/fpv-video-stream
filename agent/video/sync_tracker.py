"""Per-session sync lock state for the SDR live-view stream.

Seeds the ACTUAL line frequency once from an rfft peak (a real FPV camera's
crystal is off nominal by tens-hundreds of Hz; slicing at the nominal rate
shears the picture) and carries the vertical-blanking row across chunks so the
picture does not jump. View-path only; the scan snapshot path never builds one."""
import numpy as np

from standard import LINE_HZ

_CLAMP = 0.015          # accept a refined line rate within +/-1.5% of nominal
_SEARCH = 0.010         # search the rfft peak within +/-1.0% of nominal (crystal bound)
_MIN_PROMINENCE = 4.0   # peak must exceed the local magnitude floor by this ratio to lock


class SyncTracker:
    def __init__(self, standard):
        self.standard = standard
        self._nominal = float(LINE_HZ[standard])
        self.line_hz = self._nominal
        self.locked = False
        self.vsync_row = None

    def seed(self, baseband, fs):
        """One-time actual-line-rate estimate from a prominent rfft peak.
        No prominent line (e.g. pure noise) -> stay nominal, locked=False."""
        bb = np.asarray(baseband, dtype=np.float64)
        n = len(bb)
        self.locked = False
        if n < 4096:
            return
        spec = np.abs(np.fft.rfft(bb * np.hanning(n)))
        bin_hz = fs / n
        k0 = int(round(self._nominal / bin_hz))
        half = max(2, int(round(self._nominal * _SEARCH / bin_hz)))
        lo, hi = max(1, k0 - half), min(len(spec) - 1, k0 + half)
        if hi <= lo:
            return
        k = lo + int(np.argmax(spec[lo:hi + 1]))
        peak = float(spec[k])
        flo, fhi = max(1, k0 - 5 * half), min(len(spec), k0 + 5 * half)
        floor = float(np.median(spec[flo:fhi])) + 1e-12
        if peak / floor < _MIN_PROMINENCE:
            return                              # no genuine line sync -> nominal, unlocked
        if 0 < k < len(spec) - 1:
            a, b, c = spec[k - 1], spec[k], spec[k + 1]
            denom = a - 2 * b + c
            delta = 0.5 * (a - c) / denom if abs(denom) > 1e-12 else 0.0
            delta = float(np.clip(delta, -0.5, 0.5))
        else:
            delta = 0.0
        f = (k + delta) * bin_hz
        if abs(f - self._nominal) <= self._nominal * _CLAMP:
            self.line_hz = float(f)
            self.locked = True
        # else: leave nominal + unlocked

    def note_vsync(self, row):
        self.vsync_row = int(row)

    def status(self):
        return {"line_hz": self.line_hz, "locked": self.locked, "vsync_row": self.vsync_row}
