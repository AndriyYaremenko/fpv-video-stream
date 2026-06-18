import numpy as np

import video_emit                                   # sets sys.path to include ../video
from vconfig import VideoConfig
from synth import make_cvbs, fm_modulate


def _gradient(h=120, w=120):
    return np.tile(np.linspace(0.0, 1.0, h)[:, None], (1, w))


def _pal_iq(fs):
    bb = make_cvbs("PAL", _gradient(), fs, frames=2)
    return fm_modulate(bb, fs, deviation_hz=2_000_000.0)


class _FakePub:
    def __init__(self):
        self.videos = []     # (ts, center_mhz, standard, line_hz, sync_snr_db, b64)

    def publish_video(self, ts, center_mhz, standard, line_hz, sync_snr_db, frame_png_b64):
        self.videos.append((ts, center_mhz, standard, line_hz, sync_snr_db, frame_png_b64))


def _emitter(tmp_path, pub, cooldown_s=100.0):
    vcfg = VideoConfig(frames_dir=str(tmp_path / "frames"))
    return video_emit.VideoEmitter(pub, vcfg, cooldown_s)


def test_publishes_pal_frame(tmp_path):
    fs = 8_000_000.0
    pub = _FakePub()
    em = _emitter(tmp_path, pub)
    status = em.maybe_emit(_pal_iq(fs), fs, 5800.0, now_ts=1000)
    assert status == "published"
    assert len(pub.videos) == 1
    ts, center_mhz, standard, line_hz, snr, b64 = pub.videos[0]
    assert center_mhz == 5800.0 and standard == "PAL" and line_hz == 15625
    assert isinstance(b64, str) and b64


def test_cooldown_suppresses_second_emit(tmp_path):
    fs = 8_000_000.0
    pub = _FakePub()
    em = _emitter(tmp_path, pub, cooldown_s=100.0)
    iq = _pal_iq(fs)
    assert em.maybe_emit(iq, fs, 5800.0, now_ts=1000) == "published"
    assert em.maybe_emit(iq, fs, 5800.0, now_ts=1050) == "cooldown"   # within window
    assert len(pub.videos) == 1                                       # no second publish


def test_noise_is_not_video_no_publish(tmp_path):
    fs = 8_000_000.0
    pub = _FakePub()
    em = _emitter(tmp_path, pub)
    rng = np.random.default_rng(2)
    iq = rng.normal(0, 1, 200_000) + 1j * rng.normal(0, 1, 200_000)
    assert em.maybe_emit(iq, fs, 5800.0, now_ts=1000) == "not_video"
    assert pub.videos == []


def test_extract_error_is_swallowed(tmp_path, monkeypatch):
    fs = 8_000_000.0
    pub = _FakePub()
    em = _emitter(tmp_path, pub)
    def _boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(video_emit, "extract_frame", _boom)
    assert em.maybe_emit(_pal_iq(fs), fs, 5800.0, now_ts=1000) == "error"
    assert pub.videos == []
