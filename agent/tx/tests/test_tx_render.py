import numpy as np
from tx_render import to_sc16q11, frame_to_iq, build_ffmpeg_decode_cmd
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


def test_render_drops_partial_final_frame(tmp_path):
    import numpy as np
    from tx_render import render, frame_to_iq
    w, h = 16, 16
    full = b"\x80" * (w * h)
    chunks = [full, full, b"\x01\x02\x03"]  # 2 full frames + a short (partial) tail

    class _FakeStdout:
        def __init__(self, data): self._data = list(data); self._i = 0
        def read(self, n):
            if self._i >= len(self._data): return b""
            c = self._data[self._i]; self._i += 1; return c

    class _FakeProc:
        def __init__(self): self.stdout = _FakeStdout(chunks)
        def kill(self): pass
        def wait(self, timeout=None): pass

    out_bin = tmp_path / "iq.bin"
    frames, written = render("x.mp4", str(out_bin), standard="PAL", fs=4e6,
                             deviation_hz=1e6, width=w, height=h, fps=25, max_secs=1.0,
                             vbi_lines=2, popen=lambda *a, **k: _FakeProc())
    one = frame_to_iq(np.frombuffer(full, dtype=np.uint8).reshape(h, w),
                      "PAL", 4e6, 1e6, True, 2)
    assert frames == 2                       # partial 3-byte tail dropped, not counted
    assert written == 2 * len(one)           # only full frames written
    assert out_bin.stat().st_size == written


def test_render_respects_max_frames_cap(tmp_path):
    from tx_render import render, frame_to_iq
    import numpy as np
    w, h = 16, 16
    full = b"\x80" * (w * h)

    class _EndlessStdout:
        def read(self, n): return full   # never runs out -> only the cap can stop the loop

    class _FakeProc:
        def __init__(self): self.stdout = _EndlessStdout()
        def kill(self): pass
        def wait(self, timeout=None): pass

    out_bin = tmp_path / "iq.bin"
    frames, written = render("x.mp4", str(out_bin), standard="PAL", fs=4e6,
                             deviation_hz=1e6, width=w, height=h, fps=25, max_secs=0.08,
                             vbi_lines=2, popen=lambda *a, **k: _FakeProc())
    one = frame_to_iq(np.frombuffer(full, dtype=np.uint8).reshape(h, w),
                      "PAL", 4e6, 1e6, True, 2)
    assert frames == 2                       # int(round(0.08*25)) == 2, loop is cap-bounded
    assert written == 2 * len(one)


def test_render_creates_missing_out_dir(tmp_path):
    from tx_render import render
    w, h = 16, 16
    full = b"\x80" * (w * h)

    class _FakeStdout:
        def __init__(self, data): self._data = list(data); self._i = 0
        def read(self, n):
            if self._i >= len(self._data): return b""
            c = self._data[self._i]; self._i += 1; return c

    class _FakeProc:
        def __init__(self): self.stdout = _FakeStdout([full, b""])   # one frame then EOF
        def kill(self): pass
        def wait(self, timeout=None): pass

    out_bin = tmp_path / "tx" / ".cache" / "current.bin"    # parent dirs do NOT exist
    assert not out_bin.parent.exists()
    frames, written = render("x.mp4", str(out_bin), standard="PAL", fs=4e6,
                             deviation_hz=1e6, width=w, height=h, fps=25, max_secs=1.0,
                             vbi_lines=2, popen=lambda *a, **k: _FakeProc())
    assert out_bin.parent.exists()                          # render() made the cache dir
    assert frames == 1 and out_bin.stat().st_size == written
