from view_controller import ViewController


class _Pub:
    def __init__(self):
        self.calls = []

    def publish_view(self, ts, active, freq_mhz=None, until_ts=None, error=None):
        self.calls.append({"ts": ts, "active": active, "freq_mhz": freq_mhz,
                           "until_ts": until_ts, "error": error})


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
