from config import Thresholds
from models import Features
from classifier import classify


T = Thresholds()


def test_analog_signature():
    f = Features(occupied_bw_mhz=22.0, spectral_flatness=0.05, carrier_spike_ratio=200.0)
    cls, conf = classify(f, T)
    assert cls == "analog"
    assert 0.0 < conf <= 1.0


def test_digital_signature():
    f = Features(occupied_bw_mhz=30.0, spectral_flatness=0.8, carrier_spike_ratio=5.0)
    cls, conf = classify(f, T)
    assert cls == "digital"
    assert 0.0 < conf <= 1.0


def test_unknown_signature():
    f = Features(occupied_bw_mhz=3.0, spectral_flatness=0.3, carrier_spike_ratio=15.0)
    cls, conf = classify(f, T)
    assert cls == "unknown"
