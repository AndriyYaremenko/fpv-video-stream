from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class Spectrum:
    band: str
    freqs_mhz: np.ndarray
    power_dbm: np.ndarray


@dataclass
class Candidate:
    band: str
    center_mhz: float
    bandwidth_mhz: float
    power_dbm: float
    snr_db: float


@dataclass
class Features:
    occupied_bw_mhz: float
    spectral_flatness: float
    carrier_spike_ratio: float


@dataclass
class Detection:
    ts: int
    band: str
    center_mhz: float
    bandwidth_mhz: float
    power_dbm: float
    snr_db: float
    signal_class: str            # "analog" | "digital" | "unknown"
    confidence: float
    channel: Optional[str]

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "band": self.band,
            "center_mhz": self.center_mhz,
            "bandwidth_mhz": self.bandwidth_mhz,
            "power_dbm": self.power_dbm,
            "snr_db": self.snr_db,
            "class": self.signal_class,
            "confidence": self.confidence,
            "channel": self.channel,
        }
