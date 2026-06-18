import numpy as np

from demod import fm_demod, lowpass


def test_fm_demod_recovers_constant_tone():
    # A complex tone at frequency f has constant instantaneous frequency.
    fs = 1_000_000.0
    f = 50_000.0
    n = 4096
    t = np.arange(n) / fs
    iq = np.exp(1j * 2 * np.pi * f * t)
    bb = fm_demod(iq)
    # After de-carrier (median subtract) a pure tone is ~flat near zero.
    assert bb.shape == (n - 1,)
    assert np.std(bb) < 1e-3


def test_fm_demod_tracks_changing_frequency():
    # A signal whose frequency steps up halfway should demod higher in the 2nd half.
    fs = 1_000_000.0
    n = 8000
    t = np.arange(n) / fs
    inst = np.where(t < t[n // 2], 40_000.0, 120_000.0)
    phase = 2 * np.pi * np.cumsum(inst) / fs
    iq = np.exp(1j * phase)
    bb = fm_demod(iq)
    assert bb[: n // 2 - 10].mean() < bb[n // 2 + 10 :].mean()


def test_lowpass_attenuates_high_frequency():
    fs = 1_000_000.0
    n = 8192
    t = np.arange(n) / fs
    dc = 0.7
    hf = np.sin(2 * np.pi * 200_000.0 * t)   # well above a 20 kHz cutoff
    x = dc + hf
    y = lowpass(x, fs, cutoff_hz=20_000.0)
    assert y.shape == x.shape
    assert abs(y.mean() - dc) < 0.05          # DC preserved
    assert np.std(y) < 0.3 * np.std(hf)       # HF strongly attenuated
