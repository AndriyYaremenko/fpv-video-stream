import numpy as np

from sync_tracker import SyncTracker
from synth import make_cvbs, fm_modulate
from demod import fm_demod, lowpass
from standard import LINE_HZ


def _baseband(line_hz, fs=6e6):
    img = np.tile(np.linspace(0, 1, 64), (64, 1))
    bb = make_cvbs("PAL", img, fs, frames=4, line_hz=line_hz)
    return lowpass(fm_demod(fm_modulate(bb, fs, 4e6)), fs, 5e6)


def test_seed_recovers_off_nominal_line_rate():
    fs = 6e6
    t = SyncTracker("PAL")
    assert t.line_hz == LINE_HZ["PAL"] and t.locked is False
    t.seed(_baseband(15705.0, fs), fs)
    assert t.locked is True
    assert abs(t.line_hz - 15705.0) < 8.0                 # within a few Hz


def test_seed_holds_nominal_and_unlocked_on_noise():
    fs = 6e6
    noise = np.random.default_rng(2).normal(0, 1, 300_000)
    t = SyncTracker("PAL")
    t.seed(noise, fs)
    assert t.locked is False
    assert t.line_hz == LINE_HZ["PAL"]                    # clamp rejected the spurious peak


def test_seed_clamps_absurd_peak_to_nominal():
    fs = 6e6
    # a strong tone far from the line rate must be rejected by the +/-0.5% clamp
    t = SyncTracker("PAL")
    tone = np.sin(2 * np.pi * 30_000.0 * np.arange(200_000) / fs)
    t.seed(tone, fs)
    assert t.locked is False and t.line_hz == LINE_HZ["PAL"]


def test_note_vsync_and_status():
    t = SyncTracker("PAL")
    t.seed(_baseband(15625.0), 6e6)
    t.note_vsync(37)
    s = t.status()
    assert s["vsync_row"] == 37 and s["locked"] is True
    assert abs(s["line_hz"] - 15625.0) < 5.0
