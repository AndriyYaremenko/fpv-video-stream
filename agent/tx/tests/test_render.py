import numpy as np
from render import to_sc16q11, frame_to_iq, build_ffmpeg_decode_cmd
from bladerf_source import iq_from_sc16q11


def test_to_sc16q11_scales_and_interleaves():
    raw = to_sc16q11(np.array([1 + 0j, 0 + 1j], dtype=np.complex128))
    vals = np.frombuffer(raw, dtype=np.int16)
    assert list(vals) == [2047, 0, 0, 2047]           # ×2047, interleaved I/Q, clipped


def test_to_sc16q11_clips_and_roundtrips():
    iq = np.array([1.5 + 0j, -2.0 + 0.5j], dtype=np.complex128)   # 1.5 clips to 1.0 range
    dec = iq_from_sc16q11(to_sc16q11(iq))
    assert dec.dtype == np.complex64
    assert np.allclose(dec, np.array([1.0 + 0j, -1.0 + 0.5j]), atol=1e-3)  # 2047/2048 tolerance


def test_frame_to_iq_length_and_nonconstant():
    # a 32x32 checker → one PAL frame of IQ; length = round(spl*lines)*4 bytes; not a constant tone
    frame = ((np.indices((32, 32)).sum(axis=0) % 2) * 255).astype(np.uint8)
    raw = frame_to_iq(frame, "PAL", fs=4e6, deviation_hz=1e6, interlaced=True, vbi_lines=2)
    iq = iq_from_sc16q11(raw)
    from standard import LINE_HZ, LINES
    spl = 4e6 / LINE_HZ["PAL"]
    assert len(iq) == int(round(spl * LINES["PAL"]))   # one frame of samples
    assert np.std(np.abs(np.diff(np.angle(iq)))) > 0   # FM phase actually varies (picture modulated)


def test_build_ffmpeg_decode_cmd():
    cmd = build_ffmpeg_decode_cmd("/clip.mp4", fps=25, width=640, height=512)
    assert cmd[0] == "ffmpeg" and cmd[-1] == "-"
    assert "/clip.mp4" in cmd
    vf = cmd[cmd.index("-vf") + 1]
    assert "fps=25" in vf and "scale=640:512" in vf and "format=gray" in vf
    assert "rawvideo" in cmd
