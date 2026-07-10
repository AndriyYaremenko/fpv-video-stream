import threading

from vconfig import VideoConfig
from stream_demod import VIEW_CANVAS_HEIGHT
from view_encoder import ViewEncoder


def _vcfg(width=4, fps=50.0, osd_file=""):
    c = VideoConfig()
    c.view_push_url = "rtsp://u:p@10.8.0.1:8554/hackrf-view"
    c.view_width = width
    c.view_fps = fps
    c.view_osd_file = osd_file
    return c


class _Clock:
    """Fake monotonic clock: sleep() advances it, so the pacer never really waits."""
    def __init__(self):
        self.t = 0.0
    def __call__(self):
        return self.t
    def sleep(self, s):
        self.t += max(s, 0.0)


class _FakeEnc:
    """Stands in for the ffmpeg Popen: records stdin writes, optional stop hook."""
    def __init__(self, on_write=None, rc=None):
        self.writes = []
        self._on_write = on_write
        self.rc = rc
        self.killed = False
        outer = self
        class _Stdin:
            def write(self, b):
                outer.writes.append(bytes(b))
                if outer._on_write:
                    outer._on_write(len(outer.writes))
        self.stdin = _Stdin()
    def poll(self):
        return self.rc
    def kill(self):
        self.killed = True
    def wait(self, timeout=None):
        return 0


def test_writer_emits_black_while_idle():
    clk = _Clock()
    ve = ViewEncoder(_vcfg(), clock=clk, sleep=clk.sleep)
    enc = _FakeEnc(on_write=lambda n: n >= 3 and ve._stop.set())
    ve._run_writer(enc)
    black = bytes(4 * VIEW_CANVAS_HEIGHT)
    assert enc.writes == [black, black, black]
    assert ve.repeats == 3


def test_writer_plays_live_frames_then_freezes_last():
    clk = _Clock()
    ve = ViewEncoder(_vcfg(), clock=clk, sleep=clk.sleep)
    a = b"A" * (4 * VIEW_CANVAS_HEIGHT)
    b = b"B" * (4 * VIEW_CANVAS_HEIGHT)
    ve.submit(a); ve.submit(b)
    enc = _FakeEnc(on_write=lambda n: n >= 4 and ve._stop.set())
    ve._run_writer(enc)
    assert enc.writes == [a, b, b, b]          # live, live, freeze, freeze
    assert ve.repeats == 2


def test_idle_clears_freeze_back_to_black_and_drains_queue():
    clk = _Clock()
    ve = ViewEncoder(_vcfg(), clock=clk, sleep=clk.sleep)
    a = b"A" * (4 * VIEW_CANVAS_HEIGHT)
    ve.submit(a)

    def hook(n):
        if n == 1:
            ve.submit(b"S" * (4 * VIEW_CANVAS_HEIGHT))   # stale frame from the ended session
            ve.idle()                                     # session over: drop freeze + queue
        if n >= 3:
            ve._stop.set()
    enc = _FakeEnc(on_write=hook)
    ve._run_writer(enc)
    black = bytes(4 * VIEW_CANVAS_HEIGHT)
    assert enc.writes == [a, black, black]


def test_writer_returns_when_encoder_dies():
    clk = _Clock()
    ve = ViewEncoder(_vcfg(), clock=clk, sleep=clk.sleep)
    enc = _FakeEnc(rc=1)                                  # dead from the start
    ve._run_writer(enc)                                   # must return, not hang
    assert enc.writes == []


def test_supervisor_survives_writer_crash_and_respawns():
    clk = _Clock()
    ve = ViewEncoder(_vcfg(), clock=clk, sleep=clk.sleep)
    spawned = []
    calls = {"n": 0}

    def crashing_writer(enc):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")      # e.g. a stats-fn race
        ve._stop.set()
    ve._run_writer = crashing_writer
    ve._popen = lambda cmd, **kw: (spawned.append(_FakeEnc()) or spawned[-1])
    ve._supervise()
    assert calls["n"] == 2                  # crash contained, writer re-entered
    assert len(spawned) == 2 and all(e.killed for e in spawned)


def test_supervisor_respawns_dead_encoder_with_backoff():
    clk = _Clock()
    slept = []
    spawned = []
    ve = ViewEncoder(_vcfg(), clock=clk,
                     sleep=lambda s: (slept.append(s), clk.sleep(s)))

    def popen(cmd, **kw):
        assert cmd[0] == "ffmpeg" and "4x288" in cmd and "-g" in cmd
        enc = _FakeEnc(rc=1)                       # dies instantly
        spawned.append(enc)
        if len(spawned) == 3:
            ve._stop.set()
        return enc
    ve._popen = popen
    ve._supervise()                                # run inline: deterministic
    assert len(spawned) == 3
    assert all(e.killed for e in spawned)
    assert slept[:2] == [1.0, 2.0]                 # backoff between respawns


def test_supervisor_stop_kills_child_and_exits():
    clk = _Clock()
    ve = ViewEncoder(_vcfg(), clock=clk, sleep=clk.sleep)
    enc = _FakeEnc(on_write=lambda n: ve._stop.set())   # first placeholder write -> stop
    ve._popen = lambda cmd, **kw: enc
    ve._supervise()
    assert enc.killed


def test_set_osd_and_idle_write_the_file(tmp_path):
    osd = tmp_path / "osd.txt"
    ve = ViewEncoder(_vcfg(osd_file=str(osd)))
    ve.set_osd("947 MHz · PAL")
    assert osd.read_text(encoding="utf-8") == "947 MHz · PAL"
    ve.idle()
    assert osd.read_text(encoding="utf-8") == "—"


def test_supervise_writes_idle_osd_and_adds_vf_before_spawn(tmp_path):
    osd = tmp_path / "osd.txt"
    clk = _Clock()
    ve = ViewEncoder(_vcfg(osd_file=str(osd)), clock=clk, sleep=clk.sleep)
    seen = {}

    def popen(cmd, **kw):
        ve._stop.set()                       # stop FIRST: a failed assert must not hang the loop
        seen["vf"] = "-vf" in cmd
        return _FakeEnc()
    ve._popen = popen
    ve._supervise()
    assert seen["vf"] is True                # drawtext filter present when OSD enabled
    assert osd.read_text(encoding="utf-8") == "—"   # textfile exists before ffmpeg opens it


def test_osd_disabled_is_noop_and_no_vf(tmp_path):
    ve = ViewEncoder(_vcfg(osd_file=""))         # disabled
    ve.set_osd("947 MHz")                          # must not raise, must not create a file
    seen = {}

    def popen(cmd, **kw):
        seen["vf"] = "-vf" in cmd
        ve._stop.set()
        return _FakeEnc()
    ve._popen = popen
    ve._supervise()
    assert seen["vf"] is False
