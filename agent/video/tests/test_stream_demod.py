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
