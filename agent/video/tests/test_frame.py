import numpy as np

from frame import (slice_lines, build_frame, reconstruct_frames, pick_sharpest, laplacian_var,
                    deshear, _align_vsync)
from synth import make_cvbs, fm_modulate
from demod import fm_demod, lowpass
from sync_tracker import SyncTracker
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


def test_reconstruct_budget_picks_even_subset_of_identical_frames():
    fs = 4e6
    img = (np.indices((32, 32)).sum(axis=0) % 2).astype(float)
    bb = make_cvbs("PAL", img, fs, frames=6)     # 12 fields
    full = reconstruct_frames(bb, fs, "PAL", width=320)
    k = 5
    budgeted = reconstruct_frames(bb, fs, "PAL", width=320, budget=k)
    assert len(budgeted) == k
    expect_idx = np.round(np.linspace(0, len(full) - 1, k)).astype(int)
    for got, idx in zip(budgeted, expect_idx):
        assert np.allclose(got, full[idx], atol=1e-6)


def test_reconstruct_budget_none_and_oversized_are_noops():
    fs = 4e6
    img = np.tile(np.linspace(0, 1, 32), (32, 1))
    bb = make_cvbs("PAL", img, fs, frames=3)
    full = reconstruct_frames(bb, fs, "PAL", width=320)
    assert len(reconstruct_frames(bb, fs, "PAL", width=320, budget=None)) == len(full)
    assert len(reconstruct_frames(bb, fs, "PAL", width=320, budget=999)) == len(full)


def test_build_frame_pipeline_stays_float32():
    fs = 6e6
    bb = np.random.default_rng(5).normal(size=int(fs * 0.05)).astype(np.float32)
    frames = reconstruct_frames(bb, fs, "PAL", width=360)
    assert frames and frames[0].dtype == np.float32


def test_reconstruct_budget_zero_returns_nothing():
    fs = 4e6
    img = np.tile(np.linspace(0, 1, 32), (32, 1))
    bb = make_cvbs("PAL", img, fs, frames=2)
    assert reconstruct_frames(bb, fs, "PAL", width=320, budget=0) == []


def test_build_frame_float64_path_bit_identical_to_reference():
    rng = np.random.default_rng(9)
    rows = rng.normal(size=(64, 384))                # float64
    got = build_frame(rows, width=720)
    start = int(rows.shape[1] * 0.18)                # pre-optimization reference
    active = rows[:, start:]
    src_w = active.shape[1]
    ratio = (src_w - 1) / (720 - 1)
    pos = np.arange(720) * ratio
    lo = np.floor(pos).astype(int)
    hi = np.minimum(lo + 1, src_w - 1)
    frac = pos - lo
    ref = active[:, lo] * (1.0 - frac) + active[:, hi] * frac
    assert got.dtype == np.float64
    assert np.array_equal(got, ref)                  # bit-for-bit


def test_build_frame_float32_arithmetic_no_float64_roundtrip():
    rows = np.random.default_rng(10).normal(size=(64, 384)).astype(np.float32)
    got = build_frame(rows, width=360)
    assert got.dtype == np.float32
    ref = build_frame(rows.astype(np.float64), width=360)
    # atol=2e-4 (not 1e-4): genuine float32 arithmetic carries ulp-level error in
    # `pos`/`frac` (~2e-5 at position magnitude ~310) that this synthetic
    # uncorrelated-noise fixture amplifies via large adjacent-sample deltas
    # (~5-6) -- root-caused via manual index/frac inspection: `lo` picks the
    # identical index on both paths, only the fractional weight differs. Real
    # (spatially smooth) video would not amplify this the same way.
    assert np.allclose(got, ref, atol=2e-4)          # float32 result matches float64 math


def test_build_frame_empty_rows_keeps_dtype():
    assert build_frame(np.zeros((0, 384)), width=320).dtype == np.float64
    assert build_frame(np.zeros((0, 384), dtype=np.float32), width=320).dtype == np.float32


def test_slice_lines_line_hz_override_uses_rounded_reshape():
    fs = 6e6
    bb = np.random.default_rng(3).normal(size=int(fs * 0.02)).astype(np.float32)
    rows = slice_lines(bb, fs, "PAL", line_hz=15705.0)     # spl = 382.04 -> reshape at 382
    assert rows.shape[1] == 382
    assert rows.dtype == np.float32                         # override path never coerces float64


def test_deshear_straightens_a_known_shear():
    # A straight vertical bar, then sheared by rolling row r by round(r*D); deshear undoes it.
    n, w, D = 40, 100, 0.7
    base = np.zeros((n, w), dtype=np.float32)
    base[:, 50] = 1.0                                       # vertical bar at col 50
    sheared = np.stack([np.roll(base[r], int(round(r * D))) for r in range(n)])
    fixed = deshear(sheared, D)
    # every row's bar returns to col 50 (within +/-1 px from integer rounding)
    bar_cols = fixed.argmax(axis=1)
    assert np.all(np.abs(bar_cols - 50) <= 1)


def test_deshear_zero_is_identity():
    rows = np.random.default_rng(1).normal(size=(20, 64)).astype(np.float32)
    assert np.array_equal(deshear(rows, 0.0), rows)


def test_align_vsync_locks_vbi_not_dark_scene():
    # VBI (rows 5..10): low mean, LOW variance. Dark scene (rows 25..30): EVEN
    # LOWER mean but HIGH variance. Mean-only would wrongly pick the scene; the
    # robust low-mean+low-variance detector (tracker path) must pick the VBI.
    from frame import _align_vsync
    from sync_tracker import SyncTracker
    rng = np.random.default_rng(7)
    field = rng.uniform(0.3, 1.0, size=(50, 80)).astype(np.float32)
    field[5:11, :] = 0.12 + rng.uniform(0, 0.01, size=(6, 80))     # VBI: low mean, LOW var
    field[25:31, :] = rng.uniform(0.0, 0.2, size=(6, 80))          # scene: lower mean, HIGH var
    t = SyncTracker("PAL")
    out = _align_vsync(field, tracker=t)
    assert out[0:4].mean() < 0.16 and out[0:4].std() < 0.02        # VBI (low-var) landed on top


def test_reconstruct_with_tracker_reduces_shear_vs_nominal():
    fs = 6e6
    bars = np.zeros((64, 64), dtype=np.float64)
    bars[:, ::8] = 1.0                                           # vertical bars
    bb = make_cvbs("PAL", bars, fs, frames=4, interlaced=True, vbi_lines=6, line_hz=15705.0)
    base = lowpass(fm_demod(fm_modulate(bb, fs, 4e6)), fs, 5e6)
    t = SyncTracker("PAL")
    t.seed(base, fs)
    plain = reconstruct_frames(base, fs, "PAL", width=240)[0]
    locked = reconstruct_frames(base, fs, "PAL", width=240, tracker=t)[0]
    # shear metric: how much each row's brightness profile shifts vs the field's
    # mean profile (lower = straighter). The locked frame must be straighter.
    def shear_metric(fr):
        prof = fr.mean(axis=0)
        return float(np.mean([np.abs(np.correlate(r - r.mean(), prof - prof.mean(), "full").argmax()
                                     - (len(prof) - 1)) for r in fr]))
    assert shear_metric(locked) < shear_metric(plain)


def test_align_vsync_tracker_none_matches_old_mean_only_oracle():
    # tracker=None MUST reproduce the pre-branch darkest-window (mean-only) heuristic.
    from frame import _align_vsync
    def _old_align(rows, win=6):
        n = rows.shape[0]
        if n < win * 3:
            return rows
        m = rows.mean(axis=1)
        ext = np.concatenate([m, m[:win - 1]])
        sums = np.convolve(ext, np.ones(win, dtype=ext.dtype), "valid")[:n]
        k = int(np.argmin(sums))
        depth = float(np.median(m) - sums[k] / win)
        spread = float(m.max() - m.min())
        if spread <= 1e-9 or depth < 0.25 * spread:
            return rows
        return np.roll(rows, -k, axis=0)
    rng = np.random.default_rng(3)
    for _ in range(5):
        rows = rng.uniform(0, 1, size=(48, 60)).astype(np.float32)
        rows[7:13] = 0.02
        assert np.array_equal(_align_vsync(rows, tracker=None), _old_align(rows))


def test_deshear_before_align_has_no_seam_unlike_after():
    # Reconstruct applies deshear THEN _align_vsync. Prove that order matters:
    # a straight field (bar at a constant column) + a mid-field VBI (forces a
    # nonzero roll), pre-sheared as off-nominal slicing would leave it. Deshear
    # BEFORE the roll recovers a straight bar; deshear AFTER the roll tears it at
    # the roll's wrap seam.
    from frame import deshear, _align_vsync
    n, w, drift = 48, 80, 0.6
    true = np.full((n, w), 0.6, dtype=np.float32)
    true[:, 40] = 1.0                              # straight vertical bar, all rows
    true[10:16, :] = 0.0                           # VBI band mid-field (sync level)
    sheared = np.stack([np.roll(true[r], int(round(r * drift))) for r in range(n)])

    good = _align_vsync(deshear(sheared, drift))   # SHIPPED order: straighten, then roll
    bad = deshear(_align_vsync(sheared), drift)     # buggy order: roll, then straighten

    def max_bar_jump(field):
        # ignore the 6 VBI rows (all-zero → argmax is 0, not a bar); measure the
        # largest adjacent-row bar-column jump among the picture rows.
        cols = field.argmax(axis=1)
        picture = [c for r, c in enumerate(cols) if field[r].max() > 0.5]
        return int(np.abs(np.diff(picture)).max()) if len(picture) > 1 else 0

    assert max_bar_jump(good) <= 1                  # straight: bar column constant across picture rows
    assert max_bar_jump(bad) >= 10                  # buggy order tears at the wrap seam


def test_align_vsync_bias_prefers_tracked_row_over_equal_decoy():
    from frame import _align_vsync
    from sync_tracker import SyncTracker
    rng = np.random.default_rng(1)
    field = rng.uniform(0.3, 1.0, size=(60, 80)).astype(np.float32)
    field[10:16, :] = 0.05 + rng.uniform(0, 0.005, size=(6, 80))   # true VBI near tracked row
    field[40:46, :] = 0.04 + rng.uniform(0, 0.005, size=(6, 80))   # slightly-better decoy far away
    t = SyncTracker("PAL")
    t.note_vsync(10)                                               # previous chunk locked row 10
    out = _align_vsync(field, tracker=t)
    # biased toward row 10 (VBI), NOT the marginally-better decoy at 40
    assert out[0:4].mean() < 0.1
    assert abs(t.vsync_row - 10) <= 6                              # stayed near the tracked row
