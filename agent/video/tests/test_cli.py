import os

import numpy as np

import iq_video
from synth import make_cvbs, fm_modulate, to_int8


def _gradient(h=120, w=120):
    return np.tile(np.linspace(0.0, 1.0, h)[:, None], (1, w))


def _write_iq(tmp_path, standard, fs, frames=2, noise_std=0.0):
    bb = make_cvbs(standard, _gradient(), fs, frames=frames)
    iq = fm_modulate(bb, fs, deviation_hz=2_000_000.0)
    raw = to_int8(iq, noise_std=noise_std)
    p = tmp_path / "cap.iq"
    p.write_bytes(raw)
    return str(p)


def _run(monkeypatch, tmp_path, argv):
    # Capture publishes instead of hitting a broker; isolate the frames dir.
    sent = {}
    monkeypatch.setattr(
        iq_video, "publish_video_once",
        lambda *a, **k: (sent.update(payload=a[5]) or True),
    )
    monkeypatch.setenv("FPV_FRAMES_DIR", str(tmp_path / "frames"))
    return iq_video.main(argv), sent


def test_pal_capture_publishes_and_saves(monkeypatch, tmp_path):
    fs = 8_000_000.0
    iq_path = _write_iq(tmp_path, "PAL", fs)
    code, sent = _run(monkeypatch, tmp_path,
                      ["--iq", iq_path, "--fs", str(fs), "--center", "5800e6"])
    assert code == 0
    assert sent["payload"]["standard"] == "PAL"
    assert sent["payload"]["center_mhz"] == 5800.0
    assert sent["payload"]["frame_png_b64"]
    # A full-resolution PNG was saved locally.
    frames = os.listdir(str(tmp_path / "frames"))
    assert len(frames) == 1 and frames[0].endswith(".png")


def test_pure_noise_returns_not_video(monkeypatch, tmp_path):
    fs = 8_000_000.0
    rng = np.random.default_rng(3)
    raw = (rng.integers(-128, 128, 2 * 300_000)).astype(np.int8).tobytes()
    p = tmp_path / "noise.iq"
    p.write_bytes(raw)
    code, sent = _run(monkeypatch, tmp_path,
                      ["--iq", str(p), "--fs", str(fs), "--center", "5800e6"])
    assert code == 2
    assert sent == {}                     # nothing published
    assert not os.path.isdir(str(tmp_path / "frames")) or \
        os.listdir(str(tmp_path / "frames")) == []


def test_broker_down_saves_locally_exit_1(monkeypatch, tmp_path):
    fs = 8_000_000.0
    iq_path = _write_iq(tmp_path, "PAL", fs)
    monkeypatch.setattr(iq_video, "publish_video_once", lambda *a, **k: False)
    monkeypatch.setenv("FPV_FRAMES_DIR", str(tmp_path / "frames"))
    code = iq_video.main(["--iq", iq_path, "--fs", str(fs), "--center", "5800e6"])
    assert code == 1
    assert len(os.listdir(str(tmp_path / "frames"))) == 1   # frame still saved


def test_std_auto_distinguishes_ntsc(monkeypatch, tmp_path):
    fs = 8_000_000.0
    iq_path = _write_iq(tmp_path, "NTSC", fs)
    code, sent = _run(monkeypatch, tmp_path,
                      ["--iq", iq_path, "--fs", str(fs), "--center", "1200e6", "--std", "auto"])
    assert code == 0
    assert sent["payload"]["standard"] == "NTSC"


def test_empty_reconstruction_errors_without_crashing(monkeypatch, tmp_path):
    # Sync gate passes but reconstruction yields no lines -> clean exit 1, no publish/crash.
    from pipeline import VideoFrame
    fs = 8_000_000.0
    iq_path = _write_iq(tmp_path, "PAL", fs)
    published = {"called": False}
    monkeypatch.setattr(iq_video, "extract_frame",
                        lambda *a, **k: VideoFrame("PAL", 15625, 20.0, None))
    monkeypatch.setattr(iq_video, "publish_video_once",
                        lambda *a, **k: published.__setitem__("called", True) or True)
    monkeypatch.setenv("FPV_FRAMES_DIR", str(tmp_path / "frames"))
    code = iq_video.main(["--iq", iq_path, "--fs", str(fs), "--center", "5800e6"])
    assert code == 1
    assert published["called"] is False                       # nothing published
    assert not os.path.isdir(str(tmp_path / "frames"))        # nothing saved
