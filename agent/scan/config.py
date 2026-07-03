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
    mqtt_enabled: bool = True
    mqtt_host: str = "10.8.0.1"
    mqtt_port: int = 1883
    mqtt_user: str = "pub"
    mqtt_pass: str = ""
    mqtt_keepalive: int = 60
    source: str = "live"                       # "live" | "replay"
    fixtures_dir: str = ""
    state_path: str = "/run/fpv-scan/scan.json"
    dwell_sample_rate_hz: float = 20_000_000.0
    dwell_num_samples: int = 2_000_000
    max_dwells_per_cycle: int = 12
    sweep_bin_hz: float = 100_000.0
    lna_gain: int = 40                          # RX LNA/IF gain, 0-40 dB (8 dB steps)
    vga_gain: int = 20                          # RX VGA/baseband gain, 0-62 dB (2 dB steps)
    amp_enable: int = 0                         # RF front-end amp, 0/1 (on risks overload near strong TX)
    sdr: str = "bladerf"                        # "bladerf" | "hackrf"
    bladerf_sample_rate_hz: float = 40_000_000.0
    bladerf_bandwidth_hz: float = 40_000_000.0
    bladerf_window_mhz: float = 30.0            # tuning step between window centers (windows overlap: window_spectrum emits the full sample_rate span)
    bladerf_sweep_samples: int = 65_536         # IQ per window for the power spectrum
    bladerf_gain_db: int = 40
    local_http_host: str = "127.0.0.1"
    local_http_port: int = 8077
    bands: Dict[str, Tuple[float, float]] = field(default_factory=lambda: {
        "1.2G": (1080.0, 1360.0),
        "2.4G": (2370.0, 2510.0),
        "5.8G": (5645.0, 5945.0),
    })
    rx5808_enabled: bool = True
    rx5808_clk: int = 5
    rx5808_data: int = 6
    rx5808_le: int = 13
    rx5808_dwell_s: float = 4.0
    rx5808_settle_ms: int = 35
    rx5808_carrier_snr_db: float = 15.0
    rx5808_carrier_min_bw_mhz: float = 0.5
    rx5808_osd_file: str = "/run/fpv/rx5808.txt"
    thresholds: Thresholds = field(default_factory=Thresholds)


def load_config(env: Optional[dict] = None) -> Config:
    env = os.environ if env is None else env
    c = Config()
    c.scanner_id = env.get("SCAN_ID", c.scanner_id)
    c.source = env.get("SCAN_SOURCE", c.source)
    c.mqtt_host = env.get("SCAN_MQTT_HOST", c.mqtt_host)
    if "SCAN_MQTT_PORT" in env:
        c.mqtt_port = int(env["SCAN_MQTT_PORT"])
    c.mqtt_user = env.get("MQTT_PUB_USER", c.mqtt_user)
    c.mqtt_pass = env.get("MQTT_PUB_PASS", c.mqtt_pass)
    if "SCAN_MQTT_KEEPALIVE" in env:
        c.mqtt_keepalive = int(env["SCAN_MQTT_KEEPALIVE"])
    if "SCAN_MQTT_ENABLED" in env:
        c.mqtt_enabled = env["SCAN_MQTT_ENABLED"].strip().lower() not in ("0", "false", "no", "")
    c.fixtures_dir = env.get("SCAN_FIXTURES_DIR", c.fixtures_dir)
    c.state_path = env.get("SCAN_STATE_PATH", c.state_path)
    c.local_http_host = env.get("SCAN_HTTP_HOST", c.local_http_host)
    if "SCAN_HTTP_PORT" in env:
        c.local_http_port = int(env["SCAN_HTTP_PORT"])
    if "SCAN_LNA" in env:
        c.lna_gain = int(env["SCAN_LNA"])
    if "SCAN_VGA" in env:
        c.vga_gain = int(env["SCAN_VGA"])
    if "SCAN_AMP" in env:
        c.amp_enable = int(env["SCAN_AMP"])
    c.sdr = env.get("SCAN_SDR", c.sdr)
    if "BLADERF_SAMPLE_RATE" in env:
        c.bladerf_sample_rate_hz = float(env["BLADERF_SAMPLE_RATE"])
    if "BLADERF_BANDWIDTH" in env:
        c.bladerf_bandwidth_hz = float(env["BLADERF_BANDWIDTH"])
    if "BLADERF_WINDOW_MHZ" in env:
        c.bladerf_window_mhz = float(env["BLADERF_WINDOW_MHZ"])
    if "BLADERF_SWEEP_SAMPLES" in env:
        c.bladerf_sweep_samples = int(env["BLADERF_SWEEP_SAMPLES"])
    if "BLADERF_GAIN" in env:
        c.bladerf_gain_db = int(env["BLADERF_GAIN"])
    if "RX5808_ENABLED" in env:
        c.rx5808_enabled = env["RX5808_ENABLED"].strip().lower() not in ("0", "false", "no", "")
    if "RX5808_CLK" in env:
        c.rx5808_clk = int(env["RX5808_CLK"])
    if "RX5808_DATA" in env:
        c.rx5808_data = int(env["RX5808_DATA"])
    if "RX5808_LE" in env:
        c.rx5808_le = int(env["RX5808_LE"])
    if "RX5808_DWELL_S" in env:
        c.rx5808_dwell_s = float(env["RX5808_DWELL_S"])
    if "RX5808_SETTLE_MS" in env:
        c.rx5808_settle_ms = int(env["RX5808_SETTLE_MS"])
    if "RX5808_CARRIER_SNR_DB" in env:
        c.rx5808_carrier_snr_db = float(env["RX5808_CARRIER_SNR_DB"])
    if "RX5808_CARRIER_MIN_BW_MHZ" in env:
        c.rx5808_carrier_min_bw_mhz = float(env["RX5808_CARRIER_MIN_BW_MHZ"])
    if "FPV_RX_OSD_FILE" in env:
        c.rx5808_osd_file = env["FPV_RX_OSD_FILE"]
    return c
