from main import load_env


def test_load_env_defaults_and_overrides():
    d = load_env({})
    assert d["node_id"] == "bladerf" and d["interval_s"] == 15 and d["host"] == "10.8.0.1"
    d2 = load_env({"TELEM_NODE_ID": "pi5", "TELEM_INTERVAL_S": "30", "MQTT_PUB_USER": "bladerf"})
    assert d2["node_id"] == "pi5" and d2["interval_s"] == 30 and d2["user"] == "bladerf"
