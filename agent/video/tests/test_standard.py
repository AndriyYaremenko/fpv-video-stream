import numpy as np

from standard import detect_standard, LINE_HZ, LINES


def _tone(line_hz, fs, n, noise=0.02, seed=0):
    # Line-rate fundamental + 2nd harmonic (mimics sync pulse train) + noise.
    rng = np.random.default_rng(seed)
    t = np.arange(n) / fs
    sig = np.sin(2 * np.pi * line_hz * t) + 0.5 * np.sin(2 * np.pi * 2 * line_hz * t)
    return sig + rng.normal(0, noise, n)


def test_detects_pal():
    fs = 8_000_000.0
    bb = _tone(LINE_HZ["PAL"], fs, 400_000)
    res = detect_standard(bb, fs)
    assert res.standard == "PAL"
    assert res.line_hz == LINE_HZ["PAL"]
    assert res.sync_snr_db >= 10.0


def test_detects_ntsc():
    fs = 8_000_000.0
    bb = _tone(LINE_HZ["NTSC"], fs, 400_000)
    res = detect_standard(bb, fs)
    assert res.standard == "NTSC"
    assert res.line_hz == LINE_HZ["NTSC"]


def test_pure_noise_is_not_video():
    fs = 8_000_000.0
    rng = np.random.default_rng(1)
    bb = rng.normal(0, 1.0, 400_000)
    res = detect_standard(bb, fs)
    assert res.standard is None


def test_forced_standard_skips_autoselect():
    fs = 8_000_000.0
    bb = _tone(LINE_HZ["PAL"], fs, 400_000)
    res = detect_standard(bb, fs, forced="pal")
    assert res.standard == "PAL"


def test_format_tables_are_consistent():
    assert set(LINE_HZ) == set(LINES) == {"PAL", "NTSC"}
    assert LINE_HZ["PAL"] == 15625 and LINES["PAL"] == 625
    assert LINE_HZ["NTSC"] == 15734 and LINES["NTSC"] == 525
