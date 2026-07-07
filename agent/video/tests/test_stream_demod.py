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


def _chunk_bytes(fs, seconds=CHUNK_S):
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

    class _Endless:                                  # capture never EOFs: timeout must end it
        def read(self, n):
            return chunk

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


def test_chunk_mailbox_counts_overwrites():
    mb = ChunkMailbox()
    assert mb.take() is None
    mb.put(b"a")
    assert mb.take() == b"a" and mb.take() is None
    assert mb.dropped == 0
    mb.put(b"b")
    mb.put(b"c")                     # unconsumed "b" is replaced -> 1 dropped chunk
    assert mb.dropped == 1
    assert mb.take() == b"c"
