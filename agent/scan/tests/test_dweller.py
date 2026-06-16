import numpy as np

from dweller import iq_from_int8, compute_features


def test_iq_from_int8_decodes_pairs():
    raw = bytes([127, 0, 0, 127])          # I,Q interleaved int8
    iq = iq_from_int8(raw)
    assert iq.shape == (2,)
    assert abs(iq[0].real - (127 / 128.0)) < 1e-6
    assert abs(iq[1].imag - (127 / 128.0)) < 1e-6


def test_features_tone_is_peaky():
    fs = 20_000_000.0
    n = 40_000
    t = np.arange(n) / fs
    tone = np.exp(2j * np.pi * 1.0e6 * t)
    f = compute_features(tone, fs)
    assert f.spectral_flatness < 0.15        # very peaky
    assert f.carrier_spike_ratio > 50.0


def test_features_noise_is_flat():
    fs = 20_000_000.0
    n = 40_000
    rng = np.random.default_rng(0)
    noise = rng.standard_normal(n) + 1j * rng.standard_normal(n)
    f = compute_features(noise, fs)
    assert 0.25 < f.spectral_flatness <= 1.0   # noise-like, bounded by AM-GM
    assert f.carrier_spike_ratio < 50.0
