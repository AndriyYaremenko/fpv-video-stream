import numpy as np

from pipeline import extract_frame, VideoFrame
from vconfig import load_video_config
from synth import make_cvbs, fm_modulate


def _gradient(h=120, w=120):
    return np.tile(np.linspace(0.0, 1.0, h)[:, None], (1, w))


def _pal_iq(fs):
    bb = make_cvbs("PAL", _gradient(), fs, frames=2)
    return fm_modulate(bb, fs, deviation_hz=2_000_000.0)


def test_extract_frame_returns_luma_for_pal():
    fs = 8_000_000.0
    vcfg = load_video_config(env={})
    vf = extract_frame(_pal_iq(fs), fs, 5_800_000_000.0, vcfg)
    assert isinstance(vf, VideoFrame)
    assert vf.standard == "PAL"
    assert vf.line_hz == 15625
    assert vf.luma is not None
    assert vf.luma.dtype == np.uint8 and vf.luma.ndim == 2


def test_extract_frame_noise_is_not_video():
    fs = 8_000_000.0
    vcfg = load_video_config(env={})
    rng = np.random.default_rng(0)
    iq = rng.normal(0, 1, 200_000) + 1j * rng.normal(0, 1, 200_000)
    vf = extract_frame(iq, fs, 5_800_000_000.0, vcfg)
    assert vf.standard is None
    assert vf.luma is None
