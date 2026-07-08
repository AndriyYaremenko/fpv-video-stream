import numpy as np

from standard import LINE_HZ, LINES


def slice_lines(baseband, fs, standard, line_hz=None):
    """Slice the baseband into sync-aligned line rows: shape (n_lines, samples_per_line).

    Integer samples-per-line (PAL at 4/6/8 MS/s) -> plain reshape, identical
    output, no per-sample interpolation; dtype follows the input.

    line_hz overrides the nominal line rate (the tracker's measured value) and
    ALWAYS reshapes at the rounded integer spl — never np.interp (that would
    undo the fast path); the sub-integer residual is corrected by deshear."""
    bb = np.asarray(baseband)
    if line_hz is not None:
        spl_i = int(round(fs / line_hz))
        n = len(bb) // spl_i
        if n < 2 or spl_i < 4:
            return np.zeros((0, max(spl_i, 1)))
        rows = bb[:n * spl_i].reshape(n, spl_i)
    else:
        spl = fs / LINE_HZ[standard]
        spl_i = int(round(spl))
        n = int(len(bb) // spl)
        if n < 2 or spl_i < 4:
            return np.zeros((0, max(spl_i, 1)))
        if abs(spl - spl_i) < 1e-9:
            rows = bb[:n * spl_i].reshape(n, spl_i)
        else:
            starts = (np.arange(n) * spl)[:, None]
            cols = np.arange(spl_i)[None, :]
            pos = (starts + cols).ravel()
            rows = np.interp(pos, np.arange(len(bb)), bb.astype(np.float64)).reshape(n, spl_i)
    sync_col = int(np.argmin(rows.mean(axis=0)))
    return np.roll(rows, -sync_col, axis=1)


def deshear(rows, drift_per_row):
    """Undo linear horizontal shear: shift row r left-circularly by
    round(r * drift_per_row) samples (a per-row gather). drift_per_row is the
    fractional samples-per-line the sync tip walks after integer-spl slicing;
    0 -> identity."""
    if drift_per_row == 0 or rows.shape[0] == 0:
        return rows
    n, w = rows.shape
    shift = np.round(np.arange(n) * drift_per_row).astype(np.int64)
    idx = (np.arange(w)[None, :] + shift[:, None]) % w
    return np.take_along_axis(rows, idx, axis=1)


def build_frame(rows, width=720, blank_frac=0.18):
    """Drop sync+blanking, resample each active line to `width` px (vectorized).
    Computes in the input dtype: float32 stays float32 throughout (no int32
    promotion to float64). The float64 path (NTSC interp rows) keeps float64
    positions bit-for-bit; the live scan/view paths are float32 end-to-end
    (verified ≤1 LSB output delta vs the old float64 math)."""
    if rows.shape[0] == 0:
        return np.zeros((0, width), dtype=rows.dtype)
    start = int(rows.shape[1] * blank_frac)
    active = rows[:, start:]
    src_w = active.shape[1]
    if src_w < 2:
        return np.zeros((rows.shape[0], width), dtype=rows.dtype)
    dt = np.float32 if active.dtype == np.float32 else np.float64
    ratio = (src_w - 1) / (width - 1) if width > 1 else 0.0
    pos = np.arange(width, dtype=dt) * dt(ratio)
    lo_f = np.floor(pos)
    lo = lo_f.astype(np.int32)
    hi = np.minimum(lo + 1, src_w - 1)
    frac = pos - lo_f                      # stays in dt — no int-driven float64 promotion
    return active[:, lo] * (dt(1.0) - frac) + active[:, hi] * frac


def _align_vsync(rows, tracker=None, win=6):
    """Roll rows so the vertical-blanking interval sits at the top.

    Scores each length-`win` row window by low mean AND low variance: the broad
    vsync pulses sit near sync level with little variation, unlike a merely dark
    SCENE region (low mean, normal variance). With a tracker carrying a prior
    vsync_row, biases the search to a small band around it and re-acquires when
    the best local candidate is weak. No-op when no window clearly beats the
    field. tracker=None keeps the original darkest-window heuristic."""
    n = rows.shape[0]
    if n < win * 3:
        return rows
    m = rows.mean(axis=1)
    v = rows.var(axis=1)
    ext_m = np.concatenate([m, m[:win - 1]])
    ext_v = np.concatenate([v, v[:win - 1]])
    ones = np.ones(win, dtype=ext_m.dtype)
    win_mean = np.convolve(ext_m, ones, "valid")[:n] / win
    win_var = np.convolve(ext_v, ones, "valid")[:n] / win
    spread = float(m.max() - m.min())
    if spread <= 1e-9:
        return rows
    # score: prefer low mean AND low variance (both normalized to the field)
    score = win_mean / spread + win_var / (float(v.max()) + 1e-12)
    if tracker is not None and tracker.vsync_row is not None:
        band = max(win, n // 8)
        centre = tracker.vsync_row % n
        mask = np.ones(n) * 1e9
        lo, hi = centre - band, centre + band + 1
        for r in range(lo, hi):
            mask[r % n] = 0.0
        biased = score + mask
        k = int(np.argmin(biased))
        if score[k] > score.min() * 4:          # local band is weak -> re-acquire globally
            k = int(np.argmin(score))
    else:
        k = int(np.argmin(score))
    depth = float(np.median(m) - win_mean[k])
    if depth < 0.25 * spread:
        if tracker is not None:
            tracker.note_vsync(k)
        return rows
    if tracker is not None:
        tracker.note_vsync(k)
    return np.roll(rows, -k, axis=0)


def reconstruct_frames(baseband, fs, standard, width=720, blank_frac=0.18, budget=None,
                       tracker=None):
    """Slice into lines, chunk into FIELDS (LINES/2), align each to its vertical
    sync, build each frame.

    With a tracker: slice at the measured line rate, deshear the fractional
    residual per field, and lock vsync with cross-chunk bias. tracker=None =
    nominal slicing, no deshear, independent per-field vsync (scan path)."""
    line_hz = tracker.line_hz if tracker is not None else None
    rows = slice_lines(baseband, fs, standard, line_hz=line_hz)
    if tracker is not None:
        spl_i = int(round(fs / tracker.line_hz))
        drift = fs / tracker.line_hz - spl_i
    else:
        drift = 0.0
    field = LINES[standard] // 2
    n_frames = rows.shape[0] // field
    idx = range(n_frames)
    if budget is not None:
        budget = int(budget)
        if budget <= 0:
            return []
        if budget < n_frames:
            idx = np.round(np.linspace(0, n_frames - 1, budget)).astype(int)
    frames = []
    for f in idx:
        fr = _align_vsync(rows[f * field:(f + 1) * field], tracker=tracker)
        if drift != 0.0:
            fr = deshear(fr, drift)
        frames.append(build_frame(fr, width, blank_frac))
    if not frames:
        fr = _align_vsync(rows, tracker=tracker)
        if drift != 0.0:
            fr = deshear(fr, drift)
        frames.append(build_frame(fr, width, blank_frac))
    return frames


def laplacian_var(img):
    """Sharpness metric: variance of a 4-neighbour Laplacian."""
    if img.size == 0:
        return 0.0          # empty frame has no sharpness; avoids a NaN/RuntimeWarning
    lap = (-4.0 * img
           + np.roll(img, 1, 0) + np.roll(img, -1, 0)
           + np.roll(img, 1, 1) + np.roll(img, -1, 1))
    return float(lap.var())


def pick_sharpest(frames):
    """Return the frame with the highest Laplacian variance."""
    return max(frames, key=laplacian_var)
