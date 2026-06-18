import json
import sys
import types

from models import Detection
import publisher


def _det():
    return Detection(
        ts=1, band="5.8G", center_mhz=5800.0, bandwidth_mhz=22.0, power_dbm=-47.0,
        snr_db=28.0, signal_class="analog", confidence=0.8, channel="F4",
    )


def test_build_spectrum_frame_is_self_describing():
    f = publisher.build_spectrum_frame("hackrf", 100, "5.8G", 5645.0, 5945.0, [-90.0, -50.0])
    assert f["scanner_id"] == "hackrf"
    assert f["ts"] == 100
    assert len(f["bands"]) == 1
    b = f["bands"][0]
    assert b["id"] == "5.8G"
    assert b["low_mhz"] == 5645.0
    assert b["high_mhz"] == 5945.0
    assert b["psd"] == [-90.0, -50.0]


def test_build_detection_payload_shape():
    p = publisher.build_detection_payload("hackrf", 100, [_det()], {"5.8G": 0.5})
    assert p["scanner_id"] == "hackrf"
    assert p["ts"] == 100
    assert p["detections"][0]["class"] == "analog"     # to_dict() emits "class"
    assert p["occupancy"] == {"5.8G": 0.5}


class FakeClient:
    def __init__(self):
        self.published = []          # list of (topic, payload, qos, retain)
        self.will = None             # (topic, payload, qos, retain)
        self.creds = None            # (user, pass)
        self.connected_to = None     # (host, port, keepalive)
        self.on_connect = None
        self.loop_started = False

    def username_pw_set(self, user, password):
        self.creds = (user, password)

    def will_set(self, topic, payload, qos=0, retain=False):
        self.will = (topic, payload, qos, retain)

    def connect(self, host, port, keepalive=60):
        self.connected_to = (host, port, keepalive)

    def loop_start(self):
        self.loop_started = True

    def loop_stop(self):
        self.loop_started = False

    def disconnect(self):
        pass

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))


def _pub(fake):
    return publisher.MqttPublisher(
        "10.8.0.1", 1883, "pub", "pw", "hackrf", client_factory=lambda cid: fake
    )


def test_connect_sets_lwt_and_creds_and_starts_loop():
    fake = FakeClient()
    p = _pub(fake)
    p.connect(ts=100)
    topic, payload, qos, retain = fake.will
    assert topic == "fpv/hackrf/status"
    assert json.loads(payload) == {"online": False, "ts": 100}
    assert qos == 1 and retain is True
    assert fake.creds == ("pub", "pw")
    assert fake.connected_to == ("10.8.0.1", 1883, 60)
    assert fake.loop_started is True


def test_on_connect_publishes_online_status():
    fake = FakeClient()
    p = _pub(fake)
    p.connect(ts=100)
    p._on_connect(fake, None, None, 0)          # simulate the broker connack
    status = [m for m in fake.published if m[0] == "fpv/hackrf/status"]
    assert status, "expected an online status publish"
    topic, payload, qos, retain = status[-1]
    assert json.loads(payload)["online"] is True
    assert qos == 1 and retain is True


def test_publish_spectrum_topic_qos_retain():
    fake = FakeClient()
    p = _pub(fake); p.connect(ts=1)
    p.publish_spectrum(200, "2.4G", 2370.0, 2510.0, [-80.0])
    msg = [m for m in fake.published if m[0] == "fpv/hackrf/spectrum"][-1]
    topic, payload, qos, retain = msg
    assert qos == 0 and retain is True
    body = json.loads(payload)
    assert body["ts"] == 200 and body["bands"][0]["id"] == "2.4G"


def test_publish_detection_topic_qos_retain():
    fake = FakeClient()
    p = _pub(fake); p.connect(ts=1)
    p.publish_detection(300, [_det()], {"5.8G": 0.4})
    msg = [m for m in fake.published if m[0] == "fpv/hackrf/detection"][-1]
    topic, payload, qos, retain = msg
    assert qos == 1 and retain is True
    assert json.loads(payload)["detections"][0]["class"] == "analog"


def test_publish_is_noop_when_not_connected():
    p = publisher.MqttPublisher("h", 1, "u", "p", "hackrf")     # never connect()
    p.publish_spectrum(1, "5.8G", 5645.0, 5945.0, [-90.0])      # must not raise
    p.publish_detection(1, [], {})


def test_publish_swallows_client_errors():
    class BoomClient(FakeClient):
        def publish(self, *a, **k):
            raise RuntimeError("broker down")
    fake = BoomClient()
    p = _pub(fake); p.connect(ts=1)
    p.publish_spectrum(1, "5.8G", 5645.0, 5945.0, [-90.0])      # guarded -> no raise


def test_on_connect_skips_status_on_refused_connack():
    fake = FakeClient()
    p = _pub(fake); p.connect(ts=1)
    p._on_connect(fake, None, None, 5)          # rc != 0 -> connection refused
    status = [m for m in fake.published if m[0] == "fpv/hackrf/status"]
    assert status == []                          # no "online" published on a refused connect


def test_default_client_factory_passes_callback_api_version_v1(monkeypatch):
    # The real paho isn't installed in the dev venv; fake the lazy-imported module so we can
    # verify the constructor is called the paho-2.x way (CallbackAPIVersion required).
    calls = {}

    class _CB:                                   # stand-in for mqtt.CallbackAPIVersion
        VERSION1 = "VERSION1"

    def _Client(*args, **kwargs):
        calls["args"] = args
        calls["kwargs"] = kwargs
        return object()

    fake_mod = types.ModuleType("paho.mqtt.client")
    fake_mod.CallbackAPIVersion = _CB
    fake_mod.Client = _Client
    monkeypatch.setitem(sys.modules, "paho", types.ModuleType("paho"))
    monkeypatch.setitem(sys.modules, "paho.mqtt", types.ModuleType("paho.mqtt"))
    monkeypatch.setitem(sys.modules, "paho.mqtt.client", fake_mod)

    publisher._default_client_factory("scan-hackrf")

    assert calls["args"][0] == _CB.VERSION1       # first positional = CallbackAPIVersion.VERSION1
    assert calls["kwargs"].get("client_id") == "scan-hackrf"


def test_publish_video_topic_qos_retain():
    fake = FakeClient()
    p = _pub(fake); p.connect(ts=1)
    p.publish_video(400, 5800.0, "PAL", 15625, 18.3, "QUJD")
    msg = [m for m in fake.published if m[0] == "fpv/hackrf/video"][-1]
    topic, payload, qos, retain = msg
    assert qos == 1 and retain is True
    body = json.loads(payload)
    assert body["scanner_id"] == "hackrf"
    assert body["ts"] == 400 and body["center_mhz"] == 5800.0
    assert body["standard"] == "PAL" and body["line_hz"] == 15625
    assert body["sync_snr_db"] == 18.3 and body["frame_png_b64"] == "QUJD"


def test_publish_video_is_noop_when_not_connected():
    p = publisher.MqttPublisher("h", 1, "u", "p", "hackrf")     # never connect()
    p.publish_video(1, 5800.0, "PAL", 15625, 10.0, "x")        # must not raise
