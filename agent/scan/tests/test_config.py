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
        "SCAN_SERVER_URL": "http://10.0.0.2:9090",
        "SCAN_FIXTURES_DIR": "/tmp/fx",
    }
    c = load_config(env)
    assert c.scanner_id == "scan-09"
    assert c.source == "replay"
    assert c.server_url == "http://10.0.0.2:9090"
    assert c.fixtures_dir == "/tmp/fx"


def test_http_endpoint_config():
    c = load_config({})
    assert c.local_http_host == "127.0.0.1"
    assert c.local_http_port == 8077
    c2 = load_config({"SCAN_HTTP_PORT": "9099", "SCAN_HTTP_HOST": "0.0.0.0"})
    assert c2.local_http_port == 9099
    assert c2.local_http_host == "0.0.0.0"
