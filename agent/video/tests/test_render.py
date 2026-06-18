import base64
import io

import numpy as np
from PIL import Image

from render import normalize_luma, save_full_png, thumbnail_b64


def test_normalize_luma_stretches_to_full_range():
    arr = np.linspace(0.4, 0.6, 100).reshape(10, 10)   # narrow band
    u8 = normalize_luma(arr)
    assert u8.dtype == np.uint8
    assert u8.min() == 0 and u8.max() == 255


def test_save_full_png_writes_grayscale(tmp_path):
    u8 = (np.random.default_rng(0).random((32, 48)) * 255).astype(np.uint8)
    path = tmp_path / "frames" / "1718700000.png"   # dir does not exist yet
    save_full_png(u8, str(path))
    img = Image.open(str(path))
    assert img.mode == "L"
    assert img.size == (48, 32)                       # PIL is (width, height)


def test_thumbnail_b64_is_decodable_and_bounded():
    u8 = (np.random.default_rng(1).random((480, 640)) * 255).astype(np.uint8)
    b64 = thumbnail_b64(u8, max_width=320)
    img = Image.open(io.BytesIO(base64.b64decode(b64)))
    assert img.format == "PNG"
    assert img.width <= 320
