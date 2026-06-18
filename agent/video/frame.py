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


def reconstruct_frames(baseband, fs, standard, width=720, blank_frac=0.18):
    """Slice into lines, chunk into frames of LINES[standard], build each frame."""
    rows = slice_lines(baseband, fs, standard)
    lines = LINES[standard]
    frames = []
    n_frames = rows.shape[0] // lines
    for f in range(n_frames):
        frames.append(build_frame(rows[f * lines:(f + 1) * lines], width, blank_frac))
    if not frames:                       # fewer than one full frame of lines
        frames.append(build_frame(rows, width, blank_frac))
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
