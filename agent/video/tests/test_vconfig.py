from vconfig import VideoConfig, load_video_config


def test_defaults():
    c = load_video_config(env={})
    assert isinstance(c, VideoConfig)
    assert c.frames_dir == "/var/lib/fpv/frames"
    assert c.frame_width == 720
    assert c.thumb_max_width == 320
    assert c.lpf_cutoff_hz == 5_000_000.0
    assert c.line_snr_db == 10.0 and c.harm_snr_db == 6.0


def test_env_overrides():
    c = load_video_config(env={
        "FPV_FRAMES_DIR": "/tmp/frames",
        "FPV_FRAME_WIDTH": "640",
        "FPV_THUMB_MAX_WIDTH": "240",
        "FPV_LPF_CUTOFF_HZ": "4e6",
        "FPV_LINE_SNR_DB": "12",
        "FPV_HARM_SNR_DB": "7",
    })
    assert c.frames_dir == "/tmp/frames"
    assert c.frame_width == 640
    assert c.thumb_max_width == 240
    assert c.lpf_cutoff_hz == 4_000_000.0
    assert c.line_snr_db == 12.0 and c.harm_snr_db == 7.0


def test_video_emit_defaults():
    c = load_video_config(env={})
    assert c.video_enabled is True
    assert c.emit_cooldown_s == 10.0


def test_video_emit_env_overrides():
    c = load_video_config(env={"FPV_VIDEO_ENABLED": "0", "FPV_EMIT_COOLDOWN_S": "30"})
    assert c.video_enabled is False
    assert c.emit_cooldown_s == 30.0


def test_view_defaults():
    from vconfig import load_video_config
    c = load_video_config(env={})
    assert c.view_enabled is False and c.view_push_url == ""
    assert c.view_sample_rate_hz == 8_000_000.0 and c.view_max_s == 600.0
    assert c.view_width == 480 and c.view_fps == 15.0 and c.view_standard == "auto"


def test_view_env_overrides():
    from vconfig import load_video_config
    c = load_video_config(env={
        "VIEW_ENABLED": "1", "VIEW_PUSH_URL": "rtsp://u:p@10.8.0.1:8554/hackrf-view",
        "VIEW_SAMPLE_RATE_HZ": "10000000", "VIEW_MAX_S": "300",
        "VIEW_WIDTH": "360", "VIEW_FPS": "10", "VIEW_STANDARD": "PAL",
    })
    assert c.view_enabled is True and c.view_push_url.endswith("/hackrf-view")
    assert c.view_sample_rate_hz == 10_000_000.0 and c.view_max_s == 300.0
    assert c.view_width == 360 and c.view_fps == 10.0
    assert c.view_standard == "pal"                     # normalized to lowercase


def test_view_engine_default_and_env():
    from vconfig import load_video_config
    assert load_video_config({}).view_engine == "persistent"
    assert load_video_config({"VIEW_ENGINE": "Legacy"}).view_engine == "legacy"
