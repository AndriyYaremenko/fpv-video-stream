import base64
import io
import os

import numpy as np
from PIL import Image


def normalize_luma(frame, lo=2.0, hi=98.0):
    """Percentile-stretch a float luma frame to a uint8 0..255 image."""
    a = np.asarray(frame, dtype=np.float64)
    if a.size == 0:
        return np.zeros(a.shape, dtype=np.uint8)
    plo, phi = np.percentile(a, [lo, hi])
    if phi <= plo:
        phi = plo + 1e-9
    out = np.clip((a - plo) / (phi - plo), 0.0, 1.0)
    return (out * 255.0).astype(np.uint8)


def save_full_png(luma_u8, path):
    """Write a full-resolution grayscale PNG, creating parent dirs as needed."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    Image.fromarray(luma_u8, mode="L").save(path, format="PNG")


def thumbnail_b64(luma_u8, max_width=320):
    """Return a base64 PNG thumbnail (width <= max_width) of a grayscale frame."""
    img = Image.fromarray(luma_u8, mode="L")
    if img.width > max_width:
        h = max(1, int(round(img.height * max_width / img.width)))
        img = img.resize((max_width, h))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")
