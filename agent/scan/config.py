import os
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple


@dataclass
class Thresholds:
    snr_threshold_db: float = 20.0
    min_bandwidth_mhz: float = 5.0
    flatness_thresh: float = 0.4
    spike_thresh: float = 50.0
    analog_bw_min_mhz: float = 10.0
    analog_bw_max_mhz: float = 30.0
    digital_bw_min_mhz: float = 15.0
    occupancy_snr_db: float = 10.0


@dataclass
class Config:
    scanner_id: str = "scan-01"
    server_url: str = "http://10.8.0.1:8080"
    server_token: str = ""
    source: str = "live"                       # "live" | "replay"
    fixtures_dir: str = ""
    state_path: str = "/run/fpv-scan/scan.json"
    dwell_sample_rate_hz: float = 20_000_000.0
    dwell_num_samples: int = 2_000_000
    max_dwells_per_cycle: int = 12
    sweep_bin_hz: float = 100_000.0
    bands: Dict[str, Tuple[float, float]] = field(default_factory=lambda: {
        "1.2G": (1080.0, 1360.0),
        "2.4G": (2370.0, 2510.0),
        "5.8G": (5645.0, 5945.0),
    })
    thresholds: Thresholds = field(default_factory=Thresholds)


def load_config(env: Optional[dict] = None) -> Config:
    env = os.environ if env is None else env
    c = Config()
    c.scanner_id = env.get("SCAN_ID", c.scanner_id)
    c.server_url = env.get("SCAN_SERVER_URL", c.server_url)
    c.server_token = env.get("SCAN_SERVER_TOKEN", c.server_token)
    c.source = env.get("SCAN_SOURCE", c.source)
    c.fixtures_dir = env.get("SCAN_FIXTURES_DIR", c.fixtures_dir)
    c.state_path = env.get("SCAN_STATE_PATH", c.state_path)
    return c
