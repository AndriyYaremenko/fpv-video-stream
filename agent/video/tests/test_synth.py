import numpy as np

from synth import make_cvbs, fm_modulate, to_int8
from standard import detect_standard, LINE_HZ
from demod import fm_demod, lowpass
from iqio import load_iq


def _gradient(h=64, w=64):
    col = np.linspace(0.0, 1.0, h)[:, None]
    return np.tile(col, (1, w))            # vertical gradient (brightness by row)


def test_make_cvbs_has_line_rate_tone():
    fs = 8_000_000.0
    bb = make_cvbs("PAL", _gradient(), fs, frames=2)
    res = detect_standard(bb, fs)
    assert res.standard == "PAL"


def test_modulate_then_demod_roundtrips_through_pipeline():
    fs = 8_000_000.0
    bb = make_cvbs("NTSC", _gradient(), fs, frames=2)
    iq = fm_modulate(bb, fs, deviation_hz=2_000_000.0)
    rec = lowpass(fm_demod(iq), fs, cutoff_hz=5_000_000.0)
    # Demodulated baseband still carries the NTSC line tone.
    assert detect_standard(rec, fs).standard == "NTSC"


def test_to_int8_roundtrips_via_file(tmp_path):
    fs = 8_000_000.0
    bb = make_cvbs("PAL", _gradient(), fs, frames=1)
    iq = fm_modulate(bb, fs, deviation_hz=2_000_000.0)
    raw = to_int8(iq, noise_std=0.0)
    f = tmp_path / "cap.iq"
    f.write_bytes(raw)
    back = load_iq(str(f))
    assert back.shape == iq.shape
    # int8 quantization keeps the unit-circle samples close.
    assert np.mean(np.abs(back - iq)) < 0.02


def test_make_cvbs_line_hz_override_changes_sync_period():
    fs = 6e6
    img = np.tile(np.linspace(0, 1, 32), (32, 1))
    nominal = make_cvbs("PAL", img, fs, frames=1)                     # 15625 Hz
    off = make_cvbs("PAL", img, fs, frames=1, line_hz=15705.0)        # faster lines
    # The sync-pulse COUNT is line_hz-invariant (make_cvbs always sweeps LINES*frames
    # lines); the override shows up as a shorter signal — higher line_hz -> fewer
    # samples-per-line (spl = fs/line_hz) -> fewer total samples.
    def n_sync_edges(sig):
        at_sync = sig < 0.15
        return int(np.sum(at_sync[1:] & ~at_sync[:-1]))
    # Both have ~625 lines; verify the override changes signal length
    assert len(off) < len(nominal), "Higher line_hz should produce shorter signal"
    # Verify sync edges are still detected (one per line, minus the first)
    assert n_sync_edges(nominal) >= 624
    assert n_sync_edges(off) >= 624
