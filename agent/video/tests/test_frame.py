import numpy as np

from frame import slice_lines, build_frame, reconstruct_frames, pick_sharpest, laplacian_var
from synth import make_cvbs
from standard import LINES, LINE_HZ


def _gradient(h=120, w=120):
    return np.tile(np.linspace(0.0, 1.0, h)[:, None], (1, w))


def test_slice_lines_shapes_and_sync_at_col0():
    fs = 8_000_000.0
    bb = make_cvbs("PAL", _gradient(), fs, frames=1)
    rows = slice_lines(bb, fs, "PAL")
    assert rows.ndim == 2
    assert rows.shape[0] >= LINES["PAL"] - 2
    # Sync (lowest level) is rolled to the start: col 0 mean is the minimum.
    col_mean = rows.mean(axis=0)
    assert int(np.argmin(col_mean)) <= 2


def test_build_frame_width_and_no_nan():
    fs = 8_000_000.0
    bb = make_cvbs("PAL", _gradient(), fs, frames=1)
    rows = slice_lines(bb, fs, "PAL")
    frame = build_frame(rows, width=360)
    assert frame.shape[1] == 360
    assert np.isfinite(frame).all()


def test_reconstructed_frame_correlates_with_source():
    fs = 8_000_000.0
    src = _gradient(120, 120)
    bb = make_cvbs("PAL", src, fs, frames=2)
    frame = pick_sharpest(reconstruct_frames(bb, fs, "PAL", width=120))
    # Compare vertical structure: per-row brightness should ramp like the source.
    rec_profile = frame.mean(axis=1)
    rec_profile = np.interp(np.linspace(0, 1, 120),
                            np.linspace(0, 1, len(rec_profile)), rec_profile)
    src_profile = src.mean(axis=1)
    corr = np.corrcoef(rec_profile, src_profile)[0, 1]
    assert corr > 0.7


def test_pick_sharpest_prefers_high_variance():
    flat = np.full((40, 40), 0.5)
    sharp = np.random.default_rng(0).random((40, 40))
    assert pick_sharpest([flat, sharp]) is sharp
    assert laplacian_var(sharp) > laplacian_var(flat)


def test_interlaced_frames_are_field_sized_single_copy():
    # Real cameras interlace: each FIELD is a complete vertical scan. Stacking a
    # full 2-field frame doubles the picture (the reported bug).
    fs = 8_000_000.0
    src = _gradient(120, 120)
    bb = make_cvbs("PAL", src, fs, frames=2, interlaced=True, vbi_lines=8)
    frames = reconstruct_frames(bb, fs, "PAL", width=120)
    f = pick_sharpest(frames)
    assert f.shape[0] == LINES["PAL"] // 2          # one field, not two stacked
    prof = f.mean(axis=1)
    prof_i = np.interp(np.linspace(0, 1, 120), np.linspace(0, 1, len(prof)), prof)
    src_prof = src.mean(axis=1)
    single = np.corrcoef(prof_i, src_prof)[0, 1]
    doubled = np.corrcoef(prof_i, np.tile(src_prof[::2], 2))[0, 1]
    assert single > 0.8 and single > doubled        # one copy of the picture, not two


def test_vsync_alignment_survives_arbitrary_chunk_offset():
    # Field boundaries land wherever the capture chunk starts; the VBI (darkest
    # row window) must be rolled to the top so the picture isn't split/wrapped.
    fs = 8_000_000.0
    src = _gradient(120, 120)
    bb = make_cvbs("PAL", src, fs, frames=3, interlaced=True, vbi_lines=8)
    off = int((fs / 15625) * 150)                   # start mid-field
    frames = reconstruct_frames(bb[off:], fs, "PAL", width=120)
    f = frames[1]                                   # interior frame: full field of data
    prof = f.mean(axis=1)
    prof_i = np.interp(np.linspace(0, 1, 120), np.linspace(0, 1, len(prof)), prof)
    corr = np.corrcoef(prof_i, src.mean(axis=1))[0, 1]
    assert corr > 0.7


def _slice_interp_reference(bb, fs, standard):
    # The pre-optimization algorithm, kept as an oracle.
    bb = np.asarray(bb, dtype=np.float64)
    spl = fs / LINE_HZ[standard]
    spl_i = int(round(spl))
    n = int(len(bb) // spl)
    starts = (np.arange(n) * spl)[:, None]
    cols = np.arange(spl_i)[None, :]
    rows = np.interp((starts + cols).ravel(), np.arange(len(bb)), bb).reshape(n, spl_i)
    k = int(np.argmin(rows.mean(axis=0)))
    return np.roll(rows, -k, axis=1)


def test_slice_lines_integer_spl_equals_interp_reference():
    fs = 6e6                                     # 6e6 / 15625 == 384 exactly
    bb = np.random.default_rng(3).normal(size=int(fs * 0.02))
    fast = slice_lines(bb, fs, "PAL")
    ref = _slice_interp_reference(bb, fs, "PAL")
    assert fast.shape == ref.shape
    assert np.allclose(fast, ref, atol=1e-12)    # np.interp at grid positions == grid values


def test_slice_lines_preserves_float32():
    fs = 6e6
    bb = np.random.default_rng(4).normal(size=int(fs * 0.01)).astype(np.float32)
    assert slice_lines(bb, fs, "PAL").dtype == np.float32
