import numpy as np

from models import Spectrum
from detector import find_candidates


def _spectrum_with_bump():
    freqs = np.arange(5645.0, 5945.0, 0.1)
    power = np.full(freqs.shape, -90.0)
    bump = (freqs >= 5789.0) & (freqs <= 5811.0)   # ~22 MHz wide
    power[bump] = -50.0
    return Spectrum(band="5.8G", freqs_mhz=freqs, power_dbm=power)


def test_find_single_candidate():
    cands = find_candidates(_spectrum_with_bump(), snr_threshold_db=20.0, min_bandwidth_mhz=5.0)
    assert len(cands) == 1
    c = cands[0]
    assert abs(c.center_mhz - 5800.0) < 1.0
    assert 20.0 < c.bandwidth_mhz < 24.0
    assert c.power_dbm == -50.0
    assert c.snr_db == 40.0
    assert c.band == "5.8G"


def test_noise_only_yields_nothing():
    freqs = np.arange(5645.0, 5945.0, 0.1)
    power = np.full(freqs.shape, -90.0)
    spec = Spectrum(band="5.8G", freqs_mhz=freqs, power_dbm=power)
    assert find_candidates(spec, snr_threshold_db=20.0, min_bandwidth_mhz=5.0) == []


def test_narrow_blip_below_min_bandwidth_is_ignored():
    freqs = np.arange(5645.0, 5945.0, 0.1)
    power = np.full(freqs.shape, -90.0)
    power[(freqs >= 5800.0) & (freqs <= 5801.0)] = -40.0   # ~1 MHz
    spec = Spectrum(band="5.8G", freqs_mhz=freqs, power_dbm=power)
    assert find_candidates(spec, snr_threshold_db=20.0, min_bandwidth_mhz=5.0) == []
