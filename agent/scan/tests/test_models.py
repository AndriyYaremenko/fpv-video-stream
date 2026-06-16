from models import Detection


def test_detection_to_dict_serializes_class_key():
    d = Detection(
        ts=1718530000,
        band="5.8G",
        center_mhz=5800.0,
        bandwidth_mhz=22.0,
        power_dbm=-47.0,
        snr_db=28.0,
        signal_class="analog",
        confidence=0.82,
        channel="F4",
    )
    out = d.to_dict()
    assert out["class"] == "analog"          # python keyword -> json "class"
    assert "signal_class" not in out
    assert out["center_mhz"] == 5800.0
    assert out["channel"] == "F4"
