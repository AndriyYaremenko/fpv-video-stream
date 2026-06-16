from typing import Tuple

from config import Thresholds
from models import Features


def _confidence(ratio: float) -> float:
    # ratio >= 1 means the feature clears its threshold; map to (0.5, 1.0]
    return float(min(1.0, 0.5 + 0.25 * min(max(ratio, 0.0), 2.0)))


def classify(features: Features, t: Thresholds) -> Tuple[str, float]:
    f = features
    is_peaky = f.spectral_flatness < t.flatness_thresh
    has_spike = f.carrier_spike_ratio > t.spike_thresh
    analog_bw = t.analog_bw_min_mhz <= f.occupied_bw_mhz <= t.analog_bw_max_mhz

    if is_peaky and has_spike and analog_bw:
        return "analog", _confidence(f.carrier_spike_ratio / t.spike_thresh)

    is_flat = f.spectral_flatness >= t.flatness_thresh
    no_spike = f.carrier_spike_ratio <= t.spike_thresh
    digital_bw = f.occupied_bw_mhz >= t.digital_bw_min_mhz

    if is_flat and no_spike and digital_bw:
        return "digital", _confidence(f.spectral_flatness / t.flatness_thresh)

    return "unknown", 0.4
