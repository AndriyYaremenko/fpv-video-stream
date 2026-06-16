from config import load_config, Config


def test_defaults():
    c = load_config({})
    assert c.scanner_id == "scan-01"
    assert c.source == "live"
    assert set(c.bands.keys()) == {"1.2G", "2.4G", "5.8G"}
    assert c.bands["5.8G"] == (5645.0, 5945.0)
    assert c.thresholds.snr_threshold_db == 20.0


def test_env_overrides():
    env = {
        "SCAN_ID": "scan-09",
        "SCAN_SOURCE": "replay",
        "SCAN_SERVER_URL": "http://10.8.0.1:8080",
        "SCAN_FIXTURES_DIR": "/tmp/fx",
    }
    c = load_config(env)
    assert c.scanner_id == "scan-09"
    assert c.source == "replay"
    assert c.fixtures_dir == "/tmp/fx"
