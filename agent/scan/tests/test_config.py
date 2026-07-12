from config import load_config, parse_bands


def test_scan_bands_parse_and_env_override():
    assert parse_bands("B1:900-2200,B2:2200-3400") == {"B1": (900.0, 2200.0), "B2": (2200.0, 3400.0)}
    # malformed entries are skipped, valid ones kept
    assert parse_bands("good:100-200, bad, x:notnum-300") == {"good": (100.0, 200.0)}
    c = load_config({"SCAN_BANDS": "L:900-3450,H:3450-6000"})
    assert c.bands == {"L": (900.0, 3450.0), "H": (3450.0, 6000.0)}
    # a fully-unparseable value leaves the default 3 bands intact
    assert set(load_config({"SCAN_BANDS": "garbage"}).bands.keys()) == {"1.2G", "2.4G", "5.8G"}


def test_defaults():
    c = load_config({})
    assert c.scanner_id == "scan-01"
    assert c.source == "live"
    assert set(c.bands.keys()) == {"1.2G", "2.4G", "5.8G"}
    assert c.bands["5.8G"] == (5645.0, 5945.0)
    assert c.thresholds.snr_threshold_db == 20.0
    assert c.dwell_num_samples == 2_000_000
    assert c.sweep_bin_hz == 100_000.0


def test_env_overrides():
    env = {
        "SCAN_ID": "scan-09",
        "SCAN_SOURCE": "replay",
        "SCAN_FIXTURES_DIR": "/tmp/fx",
    }
    c = load_config(env)
    assert c.scanner_id == "scan-09"
    assert c.source == "replay"
    assert c.fixtures_dir == "/tmp/fx"


def test_mqtt_config():
    c = load_config({})
    assert c.mqtt_enabled is True
    assert c.mqtt_host == "10.8.0.1"
    assert c.mqtt_port == 1883
    assert c.mqtt_user == "pub"
    assert c.mqtt_pass == ""
    c2 = load_config({
        "SCAN_MQTT_HOST": "10.8.0.9", "SCAN_MQTT_PORT": "1884",
        "MQTT_PUB_USER": "pi", "MQTT_PUB_PASS": "s3cret",
        "SCAN_MQTT_KEEPALIVE": "30", "SCAN_MQTT_ENABLED": "0",
    })
    assert c2.mqtt_host == "10.8.0.9"
    assert c2.mqtt_port == 1884
    assert c2.mqtt_user == "pi"
    assert c2.mqtt_pass == "s3cret"
    assert c2.mqtt_keepalive == 30
    assert c2.mqtt_enabled is False
    # a truthy SCAN_MQTT_ENABLED keeps it on (guards the falsey-set boundary)
    assert load_config({"SCAN_MQTT_ENABLED": "1"}).mqtt_enabled is True


def test_http_endpoint_config():
    c = load_config({})
    assert c.local_http_host == "127.0.0.1"
    assert c.local_http_port == 8077
    c2 = load_config({"SCAN_HTTP_PORT": "9099", "SCAN_HTTP_HOST": "0.0.0.0"})
    assert c2.local_http_port == 9099
    assert c2.local_http_host == "0.0.0.0"


def test_rf_gain_config():
    c = load_config({})
    assert c.lna_gain == 40
    assert c.vga_gain == 20
    assert c.amp_enable == 0
    c2 = load_config({"SCAN_LNA": "24", "SCAN_VGA": "16", "SCAN_AMP": "1"})
    assert c2.lna_gain == 24
    assert c2.vga_gain == 16
    assert c2.amp_enable == 1


def test_rx5808_defaults_and_env():
    c = load_config({})
    assert c.rx5808_enabled is True
    assert (c.rx5808_clk, c.rx5808_data, c.rx5808_le) == (5, 6, 13)
    assert c.rx5808_dwell_s == 4.0 and c.rx5808_settle_ms == 35
    c2 = load_config({
        "RX5808_ENABLED": "0", "RX5808_CLK": "17", "RX5808_DATA": "27",
        "RX5808_LE": "22", "RX5808_DWELL_S": "2.5", "RX5808_SETTLE_MS": "50",
    })
    assert c2.rx5808_enabled is False
    assert (c2.rx5808_clk, c2.rx5808_data, c2.rx5808_le) == (17, 27, 22)
    assert c2.rx5808_dwell_s == 2.5 and c2.rx5808_settle_ms == 50


def test_rx5808_carrier_thresholds_defaults_and_env():
    c = load_config({})
    assert c.rx5808_carrier_snr_db == 15.0
    assert c.rx5808_carrier_min_bw_mhz == 0.5
    c2 = load_config({"RX5808_CARRIER_SNR_DB": "18", "RX5808_CARRIER_MIN_BW_MHZ": "1.0"})
    assert c2.rx5808_carrier_snr_db == 18.0
    assert c2.rx5808_carrier_min_bw_mhz == 1.0


def test_rx5808_osd_file_default_and_env():
    c = load_config({})
    assert c.rx5808_osd_file == "/run/fpv/rx5808.txt"
    c2 = load_config({"FPV_RX_OSD_FILE": "/tmp/osd.txt"})
    assert c2.rx5808_osd_file == "/tmp/osd.txt"


def test_sdr_backend_defaults_and_env():
    c = load_config({})
    assert c.sdr == "bladerf"
    assert c.bladerf_sample_rate_hz == 40_000_000.0
    assert c.bladerf_bandwidth_hz == 40_000_000.0
    assert c.bladerf_window_mhz == 30.0
    assert c.bladerf_sweep_samples == 65_536
    assert c.bladerf_gain_db == 40
    c2 = load_config({
        "SCAN_SDR": "hackrf",
        "BLADERF_SAMPLE_RATE": "20000000",
        "BLADERF_BANDWIDTH": "18000000",
        "BLADERF_WINDOW_MHZ": "15",
        "BLADERF_SWEEP_SAMPLES": "32768",
        "BLADERF_GAIN": "30",
    })
    assert c2.sdr == "hackrf"
    assert c2.bladerf_sample_rate_hz == 20_000_000.0
    assert c2.bladerf_bandwidth_hz == 18_000_000.0
    assert c2.bladerf_window_mhz == 15.0
    assert c2.bladerf_sweep_samples == 32768
    assert c2.bladerf_gain_db == 30


def test_scan_enabled_default_and_env():
    assert load_config({}).scan_enabled is True
    assert load_config({"SCAN_ENABLED": "0"}).scan_enabled is False
    assert load_config({"SCAN_ENABLED": "false"}).scan_enabled is False
    assert load_config({"SCAN_ENABLED": "1"}).scan_enabled is True


def test_thresholds_path_default_and_env():
    assert load_config({}).thresholds_path == "/var/lib/fpv/thresholds.json"
    assert load_config({"FPV_THRESHOLDS_PATH": "/tmp/t.json"}).thresholds_path == "/tmp/t.json"
