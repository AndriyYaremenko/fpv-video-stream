from view_controller import ViewController


class _Pub:
    def __init__(self):
        self.calls = []

    def publish_view(self, ts, active, freq_mhz=None, until_ts=None, error=None, stream=None):
        self.calls.append({"ts": ts, "active": active, "freq_mhz": freq_mhz,
                           "until_ts": until_ts, "error": error, "stream": stream})


def test_set_command_validates_and_pending_consumes_once():
    vc = ViewController(None, run_stream=lambda *a: None)
    vc.set_command({"view": "start", "freq_mhz": 5865})
    assert vc.pending() == 5865.0
    assert vc.pending() is None
    for bad in ({"view": "start"}, {"view": "start", "freq_mhz": "x"},
                {"view": "start", "freq_mhz": 50}, {"view": "start", "freq_mhz": 9000},
                {"view": "wat"}, {}):
        vc.set_command(bad)
        assert vc.pending() is None


def test_run_view_lifecycle_publishes_and_resets():
    pub = _Pub()
    resets = []

    def stream(freq, stop, max_s):
        assert freq == 5865.0 and max_s == 60.0 and not stop.is_set()
        return None

    vc = ViewController(pub, stream, max_s=60.0,
                        reset=lambda: resets.append(1), clock=lambda: 1000.0)
    assert vc.run_view(5865.0) is None
    start, end = pub.calls
    assert start["active"] is True and start["freq_mhz"] == 5865.0 and start["until_ts"] == 1060
    assert end["active"] is False and end["error"] is None and end["freq_mhz"] is None
    assert resets == [1]


def test_run_view_reports_error_and_crash():
    pub = _Pub()
    vc = ViewController(pub, lambda f, s, m: "ffmpeg exited", max_s=60.0, reset=lambda: None)
    assert vc.run_view(5000.0) == "ffmpeg exited"
    assert pub.calls[-1]["error"] == "ffmpeg exited"

    def boom(f, s, m):
        raise RuntimeError("boom")

    vc2 = ViewController(pub, boom, max_s=60.0, reset=lambda: None)
    assert "boom" in vc2.run_view(5000.0)
    assert "boom" in pub.calls[-1]["error"]


def test_stale_stop_is_cleared_before_a_new_session():
    seen = {}

    def stream(freq, stop, max_s):
        seen["preset"] = stop.is_set()
        return None

    vc = ViewController(_Pub(), stream, max_s=60.0, reset=lambda: None)
    vc.set_command({"view": "stop"})               # stop arrives while idle
    vc.run_view(5000.0)
    assert seen["preset"] is False                 # run_view cleared it


def test_stream_name_from_push_url():
    from view_controller import stream_name_from_push_url
    assert stream_name_from_push_url("rtsp://u:p@10.8.0.1:8554/hackrf-view") == "hackrf-view"
    assert stream_name_from_push_url("rtsp://host:8554/a/b-view/") == "b-view"
    assert stream_name_from_push_url("") is None
    assert stream_name_from_push_url(None) is None


def test_announce_publishes_retained_inactive_state_with_stream():
    pub = _Pub()
    vc = ViewController(pub, lambda *a: None, stream="hackrf-view", clock=lambda: 111.0)
    vc.announce()
    assert pub.calls == [{"ts": 111, "active": False, "freq_mhz": None,
                          "until_ts": None, "error": None, "stream": "hackrf-view"}]


def test_announce_mid_session_republishes_the_active_state():
    pub = _Pub()

    def stream(freq, stop, max_s):
        vc.announce()                    # simulates an MQTT reconnect during a session
        return None

    vc = ViewController(pub, stream, max_s=60.0, reset=lambda: None,
                        clock=lambda: 1000.0, stream="hackrf-view")
    vc.run_view(5865.0)
    actives = [c for c in pub.calls if c["active"]]
    assert len(actives) == 2             # session start + reconnect re-announce
    assert actives[1]["freq_mhz"] == 5865.0 and actives[1]["until_ts"] == 1060
    assert all(c["stream"] == "hackrf-view" for c in pub.calls)


def test_has_pending_is_non_consuming():
    vc = ViewController(None, run_stream=lambda *a: None)
    assert vc.has_pending() is False
    vc.set_command({"view": "start", "freq_mhz": 5865})
    assert vc.has_pending() is True
    assert vc.pending() == 5865.0
    assert vc.has_pending() is False


def test_run_view_retunes_in_place_on_start_command():
    pub = _Pub()
    freqs = []

    def stream(freq, stop, max_s):
        freqs.append(freq)
        if len(freqs) == 1:
            vc.set_command({"view": "start", "freq_mhz": 1280})
            assert stop.is_set()         # the running stream is interrupted immediately
        return None

    vc = ViewController(pub, stream, max_s=60.0, reset=lambda: None, clock=lambda: 1000.0)
    assert vc.run_view(5865.0) is None
    assert freqs == [5865.0, 1280.0]     # second session started WITHOUT leaving run_view
    actives = [c for c in pub.calls if c["active"]]
    assert [c["freq_mhz"] for c in actives] == [5865.0, 1280.0]
    assert all(c["until_ts"] == 1060 for c in actives)   # fresh 10-min deadline per retune
    assert pub.calls[-1]["active"] is False              # single final inactive publish


def test_retune_after_stream_error_resets_device_and_keeps_retune():
    pub = _Pub()
    calls = []
    resets = []

    def stream(freq, stop, max_s):
        calls.append(freq)
        if len(calls) == 1:
            vc.set_command({"view": "start", "freq_mhz": 2400})
            return "hackrf_transfer exited"
        return None

    vc = ViewController(pub, stream, max_s=60.0, reset=lambda: resets.append(len(calls)))
    assert vc.run_view(5865.0) is None                   # last session ended clean
    assert calls == [5865.0, 2400.0]
    assert resets and resets[0] == 1                     # reset BETWEEN error and retune
    assert pub.calls[-1]["error"] is None                # final state carries the last error only


def test_stop_command_during_session_exits_to_sweep():
    pub = _Pub()

    def stream(freq, stop, max_s):
        vc.set_command({"view": "stop"})
        return None

    vc = ViewController(pub, stream, max_s=60.0, reset=lambda: None)
    vc.run_view(5000.0)
    assert pub.calls[-1]["active"] is False
    assert vc.has_pending() is False
