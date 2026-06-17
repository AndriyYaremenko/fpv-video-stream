from config import load_config


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
        "SCAN_MQTT_ENABLED": "0",
    })
    assert c2.mqtt_host == "10.8.0.9"
    assert c2.mqtt_port == 1884
    assert c2.mqtt_user == "pi"
    assert c2.mqtt_pass == "s3cret"
    assert c2.mqtt_enabled is False


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
