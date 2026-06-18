import numpy as np

from frame import slice_lines, build_frame, reconstruct_frames, pick_sharpest, laplacian_var
from synth import make_cvbs
from standard import LINES


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
