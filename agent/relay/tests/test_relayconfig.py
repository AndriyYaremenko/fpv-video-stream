from relayconfig import RelayConfig, load_relay_config


def test_defaults_disabled():
    c = load_relay_config({})
    assert c.relay_enabled is False and c.armed is False
    assert c.dst_mhz == 5771.0 and c.max_s == 600.0 and c.guard_mhz == 25.0
    assert c.lost_db == 12.0 and c.lost_s == 5.0
    assert c.fs_hz == 20_000_000.0 and c.rx_gain_db == 40 and c.tx_gain_db == 60
    assert c.agc_target == 1600.0 and c.block_samples == 32768


def test_truthy_parsing():
    assert load_relay_config({"RELAY_ENABLED": "1"}).relay_enabled is True
    assert load_relay_config({"RELAY_ARMED": "yes"}).armed is True
    for v in ("0", "false", "no", "", "  "):
        assert load_relay_config({"RELAY_ENABLED": v}).relay_enabled is False


def test_env_overrides():
    c = load_relay_config({
        "RELAY_ENABLED": "1", "RELAY_DST_MHZ": "5865", "RELAY_MAX_S": "30", "RELAY_GUARD_MHZ": "10",
        "RELAY_LOST_DB": "6", "RELAY_LOST_S": "2.5", "RELAY_FS_HZ": "1e7",
        "RELAY_RX_GAIN_DB": "30", "RELAY_TX_GAIN_DB": "50", "RELAY_AGC_TARGET": "1000",
    })
    assert c.dst_mhz == 5865.0 and c.max_s == 30.0 and c.guard_mhz == 10.0
    assert c.lost_db == 6.0 and c.lost_s == 2.5 and c.fs_hz == 10_000_000.0
    assert c.rx_gain_db == 30 and c.tx_gain_db == 50 and c.agc_target == 1000.0
