import numpy as np

from standard import LINE_HZ, LINES

# Composite levels (normalized): sync tip lowest, blanking, then active video.
_SYNC = 0.0
_BLANK = 0.30
_SYNC_FRAC = 0.075       # fraction of a line that is the sync pulse
_ACTIVE_START = 0.18     # picture starts after sync + back porch
_ACTIVE_END = 0.98


def make_cvbs(standard, image, fs, frames=1, interlaced=False, vbi_lines=0, line_hz=None):
    """Build a real luma CVBS baseband (sync train + image) for `frames` frames.

    Vectorized: a phase accumulator over the whole signal places the sync pulse at
    the start of each line and maps the active window to image columns/rows.

    interlaced=True models a real camera's CVBS: the vertical period is one FIELD
    (LINES/2), so every field carries a complete vertical scan of the image.
    vbi_lines holds the first N lines of each period at sync level (a crude
    vertical-blanking interval, enough for vsync-alignment logic to lock onto).
    """
    image = np.asarray(image, dtype=np.float64)
    img_h, img_w = image.shape
    line_hz = LINE_HZ[standard] if line_hz is None else float(line_hz)
    lines = LINES[standard]
    spl = fs / line_hz                       # samples per line (fractional)
    total = int(round(spl * lines * frames))

    n = np.arange(total)
    line_pos = n / spl                       # fractional line index over the whole run
    line_idx = np.floor(line_pos).astype(np.int64)
    phase = line_pos - line_idx              # 0..1 within the current line

    sig = np.full(total, _BLANK)
    sig[phase < _SYNC_FRAC] = _SYNC

    active = (phase >= _ACTIVE_START) & (phase < _ACTIVE_END)
    col_frac = (phase - _ACTIVE_START) / (_ACTIVE_END - _ACTIVE_START)
    col = np.clip((col_frac * img_w).astype(np.int64), 0, img_w - 1)
    period = lines // 2 if interlaced else lines
    row_in_period = line_idx % period
    picture_rows = max(period - vbi_lines, 1)
    vrow = np.clip(((row_in_period - vbi_lines) / picture_rows * img_h).astype(np.int64),
                   0, img_h - 1)
    px = image[vrow, col]
    sig[active] = _BLANK + (1.0 - _BLANK) * px[active]
    if vbi_lines:
        sig[row_in_period < vbi_lines] = _SYNC   # broad vsync pulses: whole line at sync level
    return sig


def fm_modulate(baseband, fs, deviation_hz):
    """FM-modulate a real baseband to a complex IQ signal on the unit circle."""
    bb = np.asarray(baseband, dtype=np.float64)
    bb = bb - np.mean(bb)
    phase = 2 * np.pi * deviation_hz * np.cumsum(bb) / fs
    return np.exp(1j * phase)


def to_int8(iq, noise_std=0.0, seed=0):
    """Quantize complex IQ to interleaved int8 bytes, with optional AWGN."""
    iq = np.asarray(iq, dtype=np.complex128)
    if noise_std > 0:
        rng = np.random.default_rng(seed)
        iq = iq + rng.normal(0, noise_std, len(iq)) + 1j * rng.normal(0, noise_std, len(iq))
    i = np.clip(np.round(iq.real * 127.0), -128, 127).astype(np.int8)
    q = np.clip(np.round(iq.imag * 127.0), -128, 127).astype(np.int8)
    out = np.empty(2 * len(iq), dtype=np.int8)
    out[0::2] = i
    out[1::2] = q
    return out.tobytes()
