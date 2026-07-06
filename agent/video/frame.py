import numpy as np

from standard import LINE_HZ, LINES


def slice_lines(baseband, fs, standard):
    """Slice the baseband into sync-aligned line rows: shape (n_lines, samples_per_line)."""
    bb = np.asarray(baseband, dtype=np.float64)
    spl = fs / LINE_HZ[standard]
    spl_i = int(round(spl))
    n = int(len(bb) // spl)
    if n < 2 or spl_i < 4:
        return np.zeros((0, max(spl_i, 1)))
    starts = (np.arange(n) * spl)[:, None]
    cols = np.arange(spl_i)[None, :]
    pos = (starts + cols).ravel()
    rows = np.interp(pos, np.arange(len(bb)), bb).reshape(n, spl_i)
    # Align sync (lowest mean column) to col 0.
    sync_col = int(np.argmin(rows.mean(axis=0)))
    return np.roll(rows, -sync_col, axis=1)


def build_frame(rows, width=720, blank_frac=0.18):
    """Drop sync+blanking, resample each active line to `width` px (vectorized)."""
    if rows.shape[0] == 0:
        return np.zeros((0, width))
    start = int(rows.shape[1] * blank_frac)
    active = rows[:, start:]
    src_w = active.shape[1]
    if src_w < 2:
        return np.zeros((rows.shape[0], width))
    ratio = (src_w - 1) / (width - 1) if width > 1 else 0.0
    pos = np.arange(width) * ratio
    lo = np.floor(pos).astype(int)
    hi = np.minimum(lo + 1, src_w - 1)
    frac = pos - lo
    return active[:, lo] * (1.0 - frac) + active[:, hi] * frac


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
    sums = np.convolve(ext, np.ones(win), mode="valid")[:n]
    k = int(np.argmin(sums))
    depth = float(np.median(m) - sums[k] / win)
    spread = float(m.max() - m.min())
    if spread <= 1e-9 or depth < 0.25 * spread:
        return rows                                           # no distinct VBI
    return np.roll(rows, -k, axis=0)


def reconstruct_frames(baseband, fs, standard, width=720, blank_frac=0.18):
    """Slice into lines, chunk into FIELDS (LINES/2), align each to its vertical
    sync, build each frame.

    Real CVBS is interlaced: every field is a complete vertical scan of the
    picture, so stacking a full 2-field frame shows the image TWICE. One field
    per output frame gives a single copy at half vertical resolution."""
    rows = slice_lines(baseband, fs, standard)
    field = LINES[standard] // 2
    frames = []
    n_frames = rows.shape[0] // field
    for f in range(n_frames):
        frames.append(build_frame(_align_vsync(rows[f * field:(f + 1) * field]),
                                  width, blank_frac))
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
