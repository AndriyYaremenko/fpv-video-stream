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


def test_fm_demod_strided_median_differs_only_by_dc_offset():
    # On pure noise the subsampled median legitimately deviates (uniform phase
    # distribution), but the result must differ from the exact variant ONLY by a
    # constant DC offset — which per-frame normalization removes entirely.
    rng = np.random.default_rng(7)
    iq = (rng.normal(size=100_000) + 1j * rng.normal(size=100_000)).astype(np.complex64)
    exact = fm_demod(iq, median_stride=1)
    fast = fm_demod(iq, median_stride=64)
    diff = fast - exact
    assert np.allclose(diff, diff[0], atol=1e-6)


def test_fm_demod_strided_median_tight_on_concentrated_signal():
    # A real FM signal has a concentrated phase-step distribution -> the strided
    # DC estimate is tight.
    fs = 1_000_000.0
    n = 200_000
    t = np.arange(n) / fs
    phase = 2 * np.pi * 50_000.0 * t
    iq = np.exp(1j * phase).astype(np.complex64)
    exact = fm_demod(iq, median_stride=1)
    fast = fm_demod(iq, median_stride=64)
    assert abs(float(exact.mean() - fast.mean())) < 1e-4


def test_fm_demod_complex64_stays_float32():
    iq = np.exp(1j * np.linspace(0, 20, 4096)).astype(np.complex64)
    assert fm_demod(iq).dtype == np.float32


def test_lowpass_float32_in_out_matches_float64_reference():
    fs = 1_000_000.0
    rng = np.random.default_rng(11)
    x64 = rng.normal(size=50_000)
    x32 = x64.astype(np.float32)
    y = lowpass(x32, fs, cutoff_hz=20_000.0)
    assert y.dtype == np.float32
    # float64 reference computed inline (old algorithm)
    win = int(round(fs / 20_000.0))
    c = np.cumsum(np.insert(x64, 0, 0.0))
    ma = (c[win:] - c[:-win]) / win
    pad_l = (len(x64) - len(ma)) // 2
    ref = np.concatenate([np.full(pad_l, ma[0]), ma,
                          np.full(len(x64) - len(ma) - pad_l, ma[-1])])
    assert np.allclose(y, ref, atol=1e-4)
