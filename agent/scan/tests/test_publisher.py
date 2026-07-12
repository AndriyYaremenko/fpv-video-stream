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

    def subscribe(self, topic, *a, **k):
        self.subscribed = getattr(self, "subscribed", [])
        self.subscribed.append(topic)


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


def test_publish_rxtune_topic_qos_retain():
    fake = FakeClient()
    p = _pub(fake); p.connect(ts=1)
    p.publish_rxtune(500, 5865, "A1", "detected", [5865, 5800])
    msg = [m for m in fake.published if m[0] == "fpv/hackrf/rxtune"][-1]
    topic, payload, qos, retain = msg
    assert qos == 1 and retain is True
    body = json.loads(payload)
    assert body["scanner_id"] == "hackrf" and body["ts"] == 500
    assert body["freq_mhz"] == 5865 and body["channel"] == "A1"
    assert body["mode"] == "detected" and body["targets"] == [5865, 5800]


def test_publish_rxtune_is_noop_when_not_connected():
    p = publisher.MqttPublisher("h", 1, "u", "p", "hackrf")
    p.publish_rxtune(1, 5865, "A1", "scan", [])        # must not raise


def test_publish_view_includes_bandwidth():
    fake = FakeClient()
    p = _pub(fake); p.connect(ts=1)
    p.publish_view(500, True, freq_mhz=5865, until_ts=1600, stream="hackrf-view", bandwidth_mhz=2.5)
    msg = [m for m in fake.published if m[0] == "fpv/hackrf/view"][-1]
    topic, payload, qos, retain = msg
    assert qos == 1 and retain is True
    body = json.loads(payload)
    assert body["bandwidth_mhz"] == 2.5
    assert body["freq_mhz"] == 5865 and body["stream"] == "hackrf-view" and body["active"] is True


def test_publish_view_bandwidth_defaults_null():
    fake = FakeClient()
    p = _pub(fake); p.connect(ts=1)
    p.publish_view(500, False)                          # no bw -> present as null
    body = json.loads([m for m in fake.published if m[0] == "fpv/hackrf/view"][-1][1])
    assert body["bandwidth_mhz"] is None


class _Msg:
    def __init__(self, payload):
        self.payload = payload


def test_connect_subscribes_to_command_topic():
    fake = FakeClient()
    p = _pub(fake); p.connect(ts=1)
    p._on_connect(fake, None, None, 0)
    assert "fpv/hackrf/rxcmd" in getattr(fake, "subscribed", [])


def test_on_message_dispatches_command():
    p = publisher.MqttPublisher("h", 1, "u", "p", "hackrf")
    got = []
    p.on_command = lambda mode, channel: got.append((mode, channel))
    p._on_message(None, None, _Msg(json.dumps({"mode": "manual", "channel": "A1"})))
    assert got == [("manual", "A1")]


def test_on_message_swallows_bad_payload():
    p = publisher.MqttPublisher("h", 1, "u", "p", "hackrf")
    p.on_command = lambda mode, channel: (_ for _ in ()).throw(AssertionError("should not be called"))
    p._on_message(None, None, _Msg(b"{not json"))      # must not raise / not dispatch


def test_on_message_ignores_non_dict_payload():
    p = publisher.MqttPublisher("h", 1, "u", "p", "hackrf")
    p.on_command = lambda mode, channel: (_ for _ in ()).throw(AssertionError("should not be called"))
    p._on_message(None, None, _Msg(json.dumps(123)))     # valid JSON, not a dict -> ignored


def test_on_message_noop_when_no_handler():
    p = publisher.MqttPublisher("h", 1, "u", "p", "hackrf")   # on_command stays None
    p._on_message(None, None, _Msg(json.dumps({"mode": "scan"})))   # must not raise


import json as _json

from publisher import MqttPublisher as _MP


class _Msg:
    def __init__(self, payload):
        self.payload = payload


def test_on_message_routes_view_and_legacy_separately():
    p = _MP("h", 1883, "", "", "scan-01")
    seen = {}
    p.on_command = lambda mode, ch: seen.setdefault("rx", (mode, ch))
    p.on_view_command = lambda d: seen.setdefault("view", d)
    p._on_message(None, None, _Msg(b'{"view":"start","freq_mhz":5865}'))
    p._on_message(None, None, _Msg(b'{"mode":"manual","channel":"F4"}'))
    assert seen["view"] == {"view": "start", "freq_mhz": 5865}
    assert seen["rx"] == ("manual", "F4")


def test_view_payload_never_reaches_legacy_handler():
    p = _MP("h", 1883, "", "", "scan-01")
    calls = []
    p.on_command = lambda mode, ch: calls.append((mode, ch))
    p.on_view_command = None                       # even with no view handler wired
    p._on_message(None, None, _Msg(b'{"view":"stop"}'))
    assert calls == []


class _FakeViewClient:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, _json.loads(payload), qos, retain))


def test_publish_view_contract():
    p = _MP("h", 1883, "", "", "scan-01")
    p._client = _FakeViewClient()
    p.publish_view(123, True, freq_mhz=5865.0, until_ts=723)
    topic, data, qos, retain = p._client.published[0]
    assert topic == "fpv/scan-01/view" and qos == 1 and retain is True
    assert data == {"scanner_id": "scan-01", "ts": 123, "active": True,
                    "freq_mhz": 5865.0, "until_ts": 723, "error": None, "stream": None,
                    "bandwidth_mhz": None}
    p.publish_view(124, False, error="ffmpeg exited")
    data2 = p._client.published[1][1]
    assert data2["active"] is False and data2["error"] == "ffmpeg exited"


def test_publish_view_carries_stream_name():
    p = _MP("h", 1883, "", "", "scan-01")
    p._client = _FakeViewClient()
    p.publish_view(123, True, freq_mhz=5865.0, until_ts=723, stream="hackrf-view")
    topic, data, qos, retain = p._client.published[0]
    assert topic == "fpv/scan-01/view" and qos == 1 and retain is True
    assert data["stream"] == "hackrf-view"
    p.publish_view(124, False)                      # stream defaults to None
    assert p._client.published[1][1]["stream"] is None


def test_on_connected_hook_fires_after_connect_housekeeping():
    fake = FakeClient()
    p = _pub(fake)
    p.connect(ts=100)
    calls = []
    p.on_connected = lambda: calls.append(1)
    p._on_connect(fake, None, None, 0)
    assert calls == [1]
    # refused CONNACK must NOT fire the hook
    p._on_connect(fake, None, None, 5)
    assert calls == [1]


def test_on_connected_hook_errors_are_swallowed():
    fake = FakeClient()
    p = _pub(fake)
    p.connect(ts=100)
    def boom():
        raise RuntimeError("boom")
    p.on_connected = boom
    p._on_connect(fake, None, None, 0)              # must not raise
