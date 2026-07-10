import numpy as np

from stream_demod import (VIEW_HEIGHT, build_capture_cmd, build_encode_cmd,
                          pick_standard, resize_rows, chunk_to_frames)
from synth import make_cvbs, fm_modulate
from demod import fm_demod, lowpass


def test_capture_cmd_streams_to_stdout():
    cmd = build_capture_cmd(5865e6, 8e6, lna=32, vga=22, amp=1)
    assert cmd[:3] == ["hackrf_transfer", "-r", "-"]
    assert "-n" not in cmd                                  # continuous, not fixed-count
    assert "5865000000" in cmd and "8000000" in cmd
    assert cmd[cmd.index("-l") + 1] == "32" and cmd[cmd.index("-a") + 1] == "1"


def test_encode_cmd_rawgray_to_rtsp():
    cmd = build_encode_cmd("rtsp://u:p@10.8.0.1:8554/hackrf-view", 480, 288, 15)
    assert cmd[0] == "ffmpeg" and cmd[-1].endswith("/hackrf-view")
    assert "480x288" in cmd and "gray" in cmd
    assert "zerolatency" in cmd and "rtsp" in cmd and "yuv420p" in cmd


def test_encode_cmd_short_gop():
    # libx264 defaults to keyint=250 (~17 s at 15 fps): a WHEP viewer joining
    # mid-GOP waits that long for an IDR. -g fps = a keyframe every ~1 s.
    cmd = build_encode_cmd("rtsp://u:p@10.8.0.1:8554/hackrf-view", 480, 288, 15)
    assert cmd[cmd.index("-g") + 1] == "15"
    cmd = build_encode_cmd("rtsp://u:p@h:8554/s", 480, 288, 12.6)
    assert cmd[cmd.index("-g") + 1] == "13"


def test_pick_standard_forced_and_noise_fallback():
    noise = np.random.default_rng(1).normal(0, 1, 200_000)
    assert pick_standard(noise, 8e6, forced="ntsc") == "NTSC"
    assert pick_standard(noise, 8e6, forced="pal") == "PAL"
    assert pick_standard(noise, 8e6, forced="auto") == "PAL"     # gate rejects -> fallback


def test_pick_standard_detects_real_pal():
    fs = 8e6
    img = np.tile(np.linspace(0, 1, 64), (64, 1))
    bb = make_cvbs("PAL", img, fs, frames=8)
    base = lowpass(fm_demod(fm_modulate(bb, fs, 4e6)), fs, 5e6)
    assert pick_standard(base, fs, forced="auto") == "PAL"


def test_resize_rows_shapes():
    img = np.arange(20, dtype=np.uint8).reshape(10, 2)
    assert resize_rows(img, 4).shape == (4, 2)
    assert resize_rows(img, 25).shape == (25, 2)
    assert resize_rows(np.zeros((0, 2), dtype=np.uint8), 4).shape == (4, 2)


def test_chunk_to_frames_fixed_size_uint8():
    fs = 4e6                                    # cheaper than 8e6; same code path
    img = (np.indices((48, 48)).sum(axis=0) % 2).astype(float)
    bb = make_cvbs("PAL", img, fs, frames=6)
    iq = fm_modulate(bb, fs, 2e6)
    frames = chunk_to_frames(iq, fs, "PAL", width=320, height=VIEW_HEIGHT["PAL"],
                             lpf_cutoff_hz=2.5e6)
    assert len(frames) >= 5
    for fr in frames:
        assert fr.shape == (288, 320) and fr.dtype == np.uint8
    assert frames[0].std() > 5                  # picture content, not a flat field


def test_iq_from_int8_fast_is_scaled_dweller():
    from dweller import iq_from_int8
    from stream_demod import iq_from_int8_fast
    raw = bytes(range(0, 128)) + bytes([255, 254, 128, 127])
    fast = iq_from_int8_fast(raw)
    ref = iq_from_int8(raw) * 128.0
    assert fast.dtype == np.complex64
    assert np.array_equal(fast, ref)             # /128 and *128 are exact in float32


def test_chunk_to_frames_respects_budget():
    fs = 4e6
    img = (np.indices((48, 48)).sum(axis=0) % 2).astype(float)
    bb = make_cvbs("PAL", img, fs, frames=6)
    iq = fm_modulate(bb, fs, 2e6)
    full = chunk_to_frames(iq, fs, "PAL", width=320, height=VIEW_HEIGHT["PAL"],
                           lpf_cutoff_hz=2.5e6)
    lim = chunk_to_frames(iq, fs, "PAL", width=320, height=VIEW_HEIGHT["PAL"],
                          lpf_cutoff_hz=2.5e6, budget=4)
    assert len(full) > 4 and len(lim) == 4


import io
import threading

from stream_demod import CHUNK_S, FramePacer, select_frames, run_stream
from synth import to_int8
from vconfig import VideoConfig


def test_select_frames_caps_to_fps_budget():
    frames = list(range(12))
    sel = select_frames(frames, 0.5, 15)          # budget = 8
    assert len(sel) == 8 and sel[0] == 0 and sel[-1] == 11
    assert select_frames([1, 2], 0.5, 15) == [1, 2]
    assert select_frames([], 0.5, 15) == []


def test_frame_pacer_spaces_writes():
    t = [0.0]
    slept = []
    out = []
    pacer = FramePacer(10, out.append, clock=lambda: t[0],
                       sleep=lambda s: (slept.append(s), t.__setitem__(0, t[0] + s)))
    pacer.tick(b"a")                               # first write: immediate
    pacer.tick(b"b")                               # second: sleeps ~0.1s
    assert out == [b"a", b"b"]
    assert len(slept) == 1 and abs(slept[0] - 0.1) < 1e-6


class _FakeProc:
    def __init__(self, stdout=None):
        self.stdout = stdout
        self.stdin = io.BytesIO()
        self.killed = False

    def poll(self):
        if self.stdout is None:
            return None
        return 1 if self.stdout.tell() >= len(self.stdout.getbuffer()) else None

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return 0


def _vcfg():
    c = VideoConfig()
    c.view_push_url = "rtsp://u:p@10.8.0.1:8554/hackrf-view"
    c.view_sample_rate_hz = 4e6
    c.view_width = 320
    c.view_fps = 10.0
    c.view_standard = "pal"                        # skip detection: deterministic
    c.lpf_cutoff_hz = 2.5e6
    return c


import functools
import time as _t


@functools.lru_cache(maxsize=4)
def _chunk_bytes(fs, seconds=CHUNK_S):
    # Cached: the CVBS synth + FM modulation cost seconds per call and several
    # run_stream tests request the identical chunk. bytes are immutable — safe to share.
    img = (np.indices((32, 32)).sum(axis=0) % 2).astype(float)
    bb = make_cvbs("PAL", img, fs, frames=max(1, int(round(seconds * 25))))
    raw = to_int8(fm_modulate(bb, fs, 2e6))
    need = int(fs * 2 * seconds)
    return (raw * (need // len(raw) + 1))[:need]


def test_run_stream_reports_capture_death_and_kills_procs():
    fs = 4e6
    procs = []

    def popen(cmd, **kw):
        p = _FakeProc(stdout=io.BytesIO(_chunk_bytes(fs) * 2)) if cmd[0] == "hackrf_transfer" else _FakeProc()
        procs.append(p)
        return p

    t = [0.0]
    err = run_stream(_vcfg(), 947.0, threading.Event(), max_s=60.0, popen=popen,
                     clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + max(s, 0.01)))
    assert err == "hackrf_transfer exited"          # finite stdout -> EOF -> death detected
    assert all(p.killed for p in procs)
    enc = procs[1]                                   # frames actually reached ffmpeg stdin
    frame_size = 320 * 288
    assert len(enc.stdin.getvalue()) >= frame_size
    assert len(enc.stdin.getvalue()) % frame_size == 0


def test_run_stream_stops_cleanly_on_stop_event():
    stop = threading.Event()
    stop.set()

    def popen(cmd, **kw):
        return _FakeProc(stdout=io.BytesIO(b"")) if cmd[0] == "hackrf_transfer" else _FakeProc()

    err = run_stream(_vcfg(), 947.0, stop, max_s=60.0, popen=popen,
                     clock=lambda: 0.0, sleep=lambda s: None)
    assert err is None


def test_run_stream_times_out():
    fs = 4e6
    chunk = _chunk_bytes(fs)

    class _Endless:                                  # capture never EOFs during the test...
        reads = 0
        def read(self, n):
            _Endless.reads += 1
            if _Endless.reads > 100:                 # ...but dies afterwards: leaked daemon
                return b""                           # readers must not outlive the test
            _t.sleep(0.001)      # yield the GIL: a hot spin starves the demod thread (and,
            return chunk         # as a leaked daemon, every later test in the session)

    def popen(cmd, **kw):
        p = _FakeProc()
        if cmd[0] == "hackrf_transfer":
            p.stdout = _Endless()
            p.poll = lambda: None
        return p

    t = [0.0]
    err = run_stream(_vcfg(), 947.0, threading.Event(), max_s=1.0, popen=popen,
                     clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + max(s, 0.05)))
    assert err is None                               # deadline reached = clean exit


from stream_demod import ChunkMailbox


def test_chunk_mailbox_fifo_depth2_drops_oldest():
    mb = ChunkMailbox()
    assert mb.take() is None
    mb.put(b"a")
    assert mb.take() == b"a" and mb.take() is None
    assert mb.dropped == 0
    mb.put(b"b")
    mb.put(b"c")                     # depth 2: both retained, in order
    assert mb.dropped == 0
    mb.put(b"d")                     # overflow: oldest ("b") dropped
    assert mb.dropped == 1
    assert mb.take() == b"c" and mb.take() == b"d" and mb.take() is None


def test_chunk_mailbox_custom_depth():
    mb = ChunkMailbox(depth=1)       # old single-slot semantics
    mb.put(b"x")
    mb.put(b"y")
    assert mb.dropped == 1 and mb.take() == b"y"


from stream_demod import FrameQueue


def test_frame_queue_fifo_and_drop_oldest():
    q = FrameQueue(maxlen=2)
    q.put(b"1"); q.put(b"2")
    assert len(q) == 2 and q.dropped == 0
    q.put(b"3")                       # full: "1" (oldest) is dropped
    assert q.dropped == 1
    assert q.get() == b"2" and q.get() == b"3"
    assert q.get(timeout=0.01) is None            # empty, not closed -> timeout


def test_frame_queue_close_drains_then_none():
    q = FrameQueue(maxlen=4)
    q.put(b"a"); q.put(b"b")
    q.close()
    assert q.closed
    assert q.get() == b"a" and q.get() == b"b"    # close still drains the tail
    assert q.get(timeout=0.01) is None            # drained -> end of stream


def test_frame_queue_clear_drops_pending():
    q = FrameQueue(maxlen=4)
    q.put(b"a"); q.put(b"b")
    q.clear()
    assert len(q) == 0 and q.get(timeout=0.01) is None


from stream_demod import writer_loop


class _Pacer:
    def __init__(self, fail_after=None):
        self.out = []
        self._fail_after = fail_after

    def tick(self, fr):
        if self._fail_after is not None and len(self.out) >= self._fail_after:
            raise BrokenPipeError()
        self.out.append(fr)


def test_writer_loop_drains_closed_queue_in_order():
    q = FrameQueue(maxlen=8)
    for b in (b"1", b"2", b"3"):
        q.put(b)
    q.close()
    pacer = _Pacer()
    err = {"msg": None}
    writer_loop(q, pacer, _FakeProc(), threading.Event(), err, clock=lambda: 0.0)
    assert pacer.out == [b"1", b"2", b"3"]
    assert err["msg"] is None


def test_writer_loop_exits_immediately_on_stop_without_draining():
    q = FrameQueue(maxlen=8)
    q.put(b"1")
    stop = threading.Event()
    stop.set()
    pacer = _Pacer()
    writer_loop(q, pacer, _FakeProc(), stop, {"msg": None}, clock=lambda: 0.0)
    assert pacer.out == []                        # nothing written after stop


def test_writer_loop_reports_pipe_and_encoder_death():
    q = FrameQueue(maxlen=8)
    q.put(b"1"); q.put(b"2")
    err = {"msg": None}
    writer_loop(q, _Pacer(fail_after=1), _FakeProc(), threading.Event(), err,
                clock=lambda: 0.0)
    assert err["msg"] == "ffmpeg pipe closed"

    dead = _FakeProc()
    dead.poll = lambda: 1                         # encoder died, queue open+empty
    err2 = {"msg": None}
    writer_loop(FrameQueue(maxlen=8), _Pacer(), dead, threading.Event(), err2,
                clock=lambda: 0.0)
    assert err2["msg"] == "ffmpeg exited"


def test_writer_loop_logs_stats_line(caplog):
    import logging
    q = FrameQueue(maxlen=8)
    for b in (b"1", b"2", b"3"):
        q.put(b)
    q.close()
    t = [0.0]

    def clock():
        t[0] += 6.0                               # 2 reads cross the 10 s threshold
        return t[0]

    with caplog.at_level(logging.INFO):
        writer_loop(q, _Pacer(), _FakeProc(), threading.Event(), {"msg": None},
                    dropped_chunks=lambda: 7, mailbox_len=lambda: 2, clock=clock)
    lines = [r.getMessage() for r in caplog.records if "dropped_chunks" in r.getMessage()]
    assert lines and "dropped_chunks=7" in lines[0] and "queue=" in lines[0]
    assert "mailbox=2" in lines[0]


def test_run_stream_writes_every_selected_frame_of_a_chunk():
    # One full chunk then EOF: the writer must drain the ENTIRE select budget
    # (chunk_s * fps frames) before run_stream returns — no air lost to pacing.
    fs = 4e6
    procs = []

    def popen(cmd, **kw):
        p = _FakeProc(stdout=io.BytesIO(_chunk_bytes(fs))) if cmd[0] == "hackrf_transfer" else _FakeProc()
        procs.append(p)
        return p

    t = [0.0]
    err = run_stream(_vcfg(), 947.0, threading.Event(), max_s=60.0, popen=popen,
                     clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + max(s, 0.01)))
    assert err == "hackrf_transfer exited"
    frame_size = 320 * 288
    budget = int(round(CHUNK_S * 10.0))            # _vcfg() fps = 10 -> 5 frames
    assert len(procs[1].stdin.getvalue()) == budget * frame_size


def test_run_stream_surfaces_writer_failure_during_drain(monkeypatch):
    # Timeout exit with a healthy pipeline, then the writer fails while draining
    # the tail: the error must still surface as run_stream's return value.
    import itertools
    import time as _time

    import stream_demod as sd

    def _failing_writer(q, pacer, enc, stop_event, err, **kw):
        while not q.closed:
            _time.sleep(0.001)
        err["msg"] = "ffmpeg pipe closed"            # failure lands in the drain window

    monkeypatch.setattr(sd, "writer_loop", _failing_writer)
    fs = 4e6
    chunk = _chunk_bytes(fs)

    class _Endless:                                  # capture never EOFs during the test...
        reads = 0
        def read(self, n):
            _Endless.reads += 1
            if _Endless.reads > 100:                 # ...but dies afterwards: leaked daemon
                return b""                           # readers must not outlive the test
            _time.sleep(0.001)   # yield the GIL: a hot spin starves the demod thread
            return chunk

    def popen(cmd, **kw):
        p = _FakeProc()
        if cmd[0] == "hackrf_transfer":
            p.stdout = _Endless()
            p.poll = lambda: None
        return p

    ticks = itertools.count()
    # max_s=30 gives ~100 clock ticks of budget so the reader thread's startup latency
    # can never exhaust the deadline before the first chunk is demodulated; the 1 ms
    # real sleep lets the reader run while the mailbox is empty.
    err = run_stream(_vcfg(), 947.0, threading.Event(), max_s=30.0, popen=popen,
                     clock=lambda: next(ticks) * 0.3, sleep=lambda s: _time.sleep(0.001))
    assert err == "ffmpeg pipe closed"


def test_run_stream_low_fps_still_streams_frames():
    # VIEW_FPS=1 rounds the naive per-chunk budget to 0 — the guard must keep >=1.
    fs = 4e6
    procs = []

    def popen(cmd, **kw):
        p = _FakeProc(stdout=io.BytesIO(_chunk_bytes(fs))) if cmd[0] == "hackrf_transfer" else _FakeProc()
        procs.append(p)
        return p

    cfg = _vcfg()
    cfg.view_fps = 1.0
    t = [0.0]
    run_stream(cfg, 947.0, threading.Event(), max_s=60.0, popen=popen,
               clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + max(s, 0.01)))
    assert len(procs[1].stdin.getvalue()) == 320 * 288       # exactly the 1-frame budget


def test_run_stream_kills_capture_before_draining_the_writer():
    # Teardown order: capture dies first so the reader can't inflate dropped_chunks
    # while the writer drains the tail into the still-alive encoder.
    fs = 4e6
    kills = []

    def popen(cmd, **kw):
        p = _FakeProc(stdout=io.BytesIO(_chunk_bytes(fs))) if cmd[0] == "hackrf_transfer" else _FakeProc()
        orig_kill = p.kill
        p.kill = lambda name=cmd[0]: (kills.append(name), orig_kill())
        return p

    t = [0.0]
    run_stream(_vcfg(), 947.0, threading.Event(), max_s=60.0, popen=popen,
               clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + max(s, 0.01)))
    assert kills == ["hackrf_transfer", "ffmpeg"]


def test_chunk_to_frames_forwards_tracker():
    from sync_tracker import SyncTracker
    fs = 6e6
    bars = np.zeros((64, 64)); bars[:, ::8] = 1.0
    bb = make_cvbs("PAL", bars, fs, frames=4, interlaced=True, vbi_lines=6, line_hz=15705.0)
    iq = fm_modulate(bb, fs, 4e6)
    t = SyncTracker("PAL")
    from demod import fm_demod as _fd, lowpass as _lp
    t.seed(_lp(_fd(iq), fs, 5e6), fs)
    frames = chunk_to_frames(iq, fs, "PAL", width=240, height=VIEW_HEIGHT["PAL"],
                             lpf_cutoff_hz=5e6, tracker=t)
    assert frames and frames[0].shape == (288, 240)          # tracker path still yields fixed-size frames


def test_writer_loop_stats_includes_sync(caplog):
    import logging
    q = FrameQueue(maxlen=8)
    for b in (b"1", b"2", b"3"):
        q.put(b)
    q.close()
    t = [0.0]
    def clock():
        t[0] += 6.0
        return t[0]
    with caplog.at_level(logging.INFO):
        writer_loop(q, _Pacer(), _FakeProc(), threading.Event(), {"msg": None},
                    dropped_chunks=lambda: 0, mailbox_len=lambda: 1,
                    sync_status=lambda: {"line_hz": 15705.0, "locked": True, "vsync_row": 37,
                                         "nominal": 15625.0},
                    clock=clock)
    line = [r.getMessage() for r in caplog.records if "view stream:" in r.getMessage()][0]
    assert "line=15705" in line and "V37" in line
