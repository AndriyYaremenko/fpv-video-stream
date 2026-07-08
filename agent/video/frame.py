import numpy as np

from standard import LINE_HZ, LINES


def slice_lines(baseband, fs, standard):
    """Slice the baseband into sync-aligned line rows: shape (n_lines, samples_per_line).

    When fs is an integer multiple of the line rate (PAL at 4/6/8 MS/s) the
    slicing is a plain reshape — identical output, no per-sample interpolation.
    dtype follows the input (the view chain stays float32) (fast path; the
    NTSC interp fallback still returns float64)."""
    bb = np.asarray(baseband)
    spl = fs / LINE_HZ[standard]
    spl_i = int(round(spl))
    n = int(len(bb) // spl)
    if n < 2 or spl_i < 4:
        return np.zeros((0, max(spl_i, 1)))
    if abs(spl - spl_i) < 1e-9:                  # integer samples-per-line: exact fast path
        rows = bb[:n * spl_i].reshape(n, spl_i)
    else:                                        # e.g. NTSC: line rate not integer-divisible
        starts = (np.arange(n) * spl)[:, None]
        cols = np.arange(spl_i)[None, :]
        pos = (starts + cols).ravel()
        rows = np.interp(pos, np.arange(len(bb)), bb.astype(np.float64)).reshape(n, spl_i)
    # Align sync (lowest mean column) to col 0.
    sync_col = int(np.argmin(rows.mean(axis=0)))
    return np.roll(rows, -sync_col, axis=1)


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


def _align_vsync(rows, win=6):
    """Roll rows so the vertical-blanking interval (the darkest consecutive row
    window — broad vsync pulses) sits at the top: without this the field
    boundary lands wherever the capture chunk started and the picture wraps.
    No-op when no clearly darker window exists (signals without a visible VBI)."""
    n = rows.shape[0]
    if n < win * 3:
        return rows
    m = rows.mean(axis=1)
    ext = np.concatenate([m, m[:win - 1]])                    # circular windows
    sums = np.convolve(ext, np.ones(win, dtype=ext.dtype), mode="valid")[:n]
    k = int(np.argmin(sums))
    depth = float(np.median(m) - sums[k] / win)
    spread = float(m.max() - m.min())
    if spread <= 1e-9 or depth < 0.25 * spread:
        return rows                                           # no distinct VBI
    return np.roll(rows, -k, axis=0)


def reconstruct_frames(baseband, fs, standard, width=720, blank_frac=0.18, budget=None):
    """Slice into lines, chunk into FIELDS (LINES/2), align each to its vertical
    sync, build each frame.

    Real CVBS is interlaced: every field is a complete vertical scan of the
    picture, so stacking a full 2-field frame shows the image TWICE. One field
    per output frame gives a single copy at half vertical resolution.

    budget: build at most this many frames, picked EVENLY across the chunk's
    fields (the streamer's fps budget) — skipped fields are never aligned or
    resampled, which is the point: they would be discarded downstream anyway."""
    rows = slice_lines(baseband, fs, standard)
    field = LINES[standard] // 2
    n_frames = rows.shape[0] // field
    idx = range(n_frames)
    if budget is not None:
        budget = int(budget)
        if budget <= 0:
            return []                      # the caller explicitly asked for nothing
        if budget < n_frames:
            idx = np.round(np.linspace(0, n_frames - 1, budget)).astype(int)
    frames = [build_frame(_align_vsync(rows[f * field:(f + 1) * field]), width, blank_frac)
              for f in idx]
    if not frames:                       # fewer than one full field of lines
        frames.append(build_frame(_align_vsync(rows), width, blank_frac))
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
