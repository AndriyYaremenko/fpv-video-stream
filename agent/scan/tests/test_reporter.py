import json

import reporter
from models import Detection


def _det():
    return Detection(
        ts=1, band="5.8G", center_mhz=5800.0, bandwidth_mhz=22.0, power_dbm=-47.0,
        snr_db=28.0, signal_class="analog", confidence=0.8, channel="F4",
    )


def test_build_payload_shape():
    p = reporter.build_payload("scan-01", 1718530000, [_det()], {"5.8G": 0.5}, {"5.8G": [-90.0]})
    assert p["scanner_id"] == "scan-01"
    assert p["detections"][0]["class"] == "analog"
    assert p["occupancy"]["5.8G"] == 0.5
    assert p["ts"] == 1718530000
    assert p["spectrum"] == {"5.8G": [-90.0]}


def test_write_state_roundtrip(tmp_path):
    path = tmp_path / "scan.json"
    payload = reporter.build_payload("scan-01", 1, [_det()], {"5.8G": 0.5}, {})
    reporter.write_state(str(path), payload)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded == payload


def test_post_telemetry_swallows_errors(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(reporter.requests, "post", boom)
    ok = reporter.post_telemetry("http://10.8.0.1:8080", "", "scan-01", {"ts": 1})
    assert ok is False        # never raises; returns False
