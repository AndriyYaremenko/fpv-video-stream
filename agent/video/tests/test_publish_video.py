import json

import publisher


def test_build_video_payload_shape():
    p = publisher.build_video_payload(
        "scan-01", 1718700000.0, 5800.0, "PAL", 15625, 18.3, "QUJD"
    )
    assert p == {
        "scanner_id": "scan-01", "ts": 1718700000.0, "center_mhz": 5800.0,
        "standard": "PAL", "line_hz": 15625, "sync_snr_db": 18.3,
        "frame_png_b64": "QUJD",
    }


class _Info:
    def __init__(self, ok=True):
        self._ok = ok
    def wait_for_publish(self, timeout=None):
        pass
    def is_published(self):
        return self._ok


class FakeClient:
    def __init__(self):
        self.published = []
        self.creds = None
        self.connected_to = None
        self.disconnected = False
    def username_pw_set(self, u, p):
        self.creds = (u, p)
    def connect(self, host, port, keepalive=60):
        self.connected_to = (host, port, keepalive)
    def loop_start(self):
        pass
    def loop_stop(self):
        pass
    def disconnect(self):
        self.disconnected = True
    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))
        return _Info(True)


def test_publish_video_once_topic_qos_retain_no_status():
    fake = FakeClient()
    ok = publisher.publish_video_once(
        "10.8.0.1", 1883, "pub", "pw", "scan-01",
        {"ts": 1, "frame_png_b64": "x"}, client_factory=lambda cid: fake,
    )
    assert ok is True
    assert fake.creds == ("pub", "pw")
    assert fake.connected_to == ("10.8.0.1", 1883, 60)
    assert len(fake.published) == 1
    topic, payload, qos, retain = fake.published[0]
    assert topic == "fpv/scan-01/video"
    assert qos == 1 and retain is True
    assert json.loads(payload)["frame_png_b64"] == "x"
    # Must NOT touch the presence/status topic owned by the scan service.
    assert all(t != "fpv/scan-01/status" for (t, *_rest) in fake.published)
    assert fake.disconnected is True


def test_publish_video_once_returns_false_when_broker_down():
    class BoomClient(FakeClient):
        def connect(self, *a, **k):
            raise OSError("connection refused")
    ok = publisher.publish_video_once(
        "10.8.0.1", 1883, "pub", "pw", "scan-01",
        {"ts": 1}, client_factory=lambda cid: BoomClient(),
    )
    assert ok is False
