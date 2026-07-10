import threading

from vconfig import VideoConfig
from stream_demod import VIEW_CANVAS_HEIGHT
from view_encoder import ViewEncoder


def _vcfg(width=4, fps=50.0):
    c = VideoConfig()
    c.view_push_url = "rtsp://u:p@10.8.0.1:8554/hackrf-view"
    c.view_width = width
    c.view_fps = fps
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
