from txconfig import TxConfig, load_tx_config


def test_defaults_disabled():
    c = load_tx_config({})
    assert c.tx_enabled is False
    assert c.tx_dir == "/var/lib/fpv/tx"
    assert c.tx_cache_bin == "/var/lib/fpv/tx/.cache/current.bin"
    assert c.tx_max_s == 120.0
    assert c.fs_hz == 20_000_000.0 and c.deviation_hz == 4_000_000.0
    assert c.standard == "PAL" and c.width == 640 and c.height == 512
    assert c.fps == 25 and c.secs == 3.0 and c.vbi_lines == 6 and c.gain_db == 30


def test_tx_enabled_truthy_parsing():
    assert load_tx_config({"TX_ENABLED": "1"}).tx_enabled is True
    for v in ("0", "false", "no", "", "  "):
        assert load_tx_config({"TX_ENABLED": v}).tx_enabled is False


def test_env_overrides():
    c = load_tx_config({
        "TX_ENABLED": "yes", "FPV_TX_DIR": "/data/vids", "FPV_TX_MAX_S": "45",
        "FPV_TX_DEVIATION_HZ": "3e6", "FPV_TX_STANDARD": "ntsc", "FPV_TX_GAIN_DB": "20",
        "FPV_TX_FPS": "30", "FPV_TX_SECS": "2",
    })
    assert c.tx_enabled is True and c.tx_dir == "/data/vids" and c.tx_max_s == 45.0
    assert c.deviation_hz == 3_000_000.0 and c.standard == "NTSC"   # upper-cased
    assert c.gain_db == 20 and c.fps == 30 and c.secs == 2.0
