import os
from dataclasses import dataclass

from osd import DEFAULT_OSD_FONT


@dataclass
class VideoConfig:
    frames_dir: str = "/var/lib/fpv/frames"
    lpf_cutoff_hz: float = 5_000_000.0
    frame_width: int = 720
    thumb_max_width: int = 320
    line_snr_db: float = 10.0
    harm_snr_db: float = 6.0
    blank_frac: float = 0.18
    default_fs: float = 16_000_000.0
    video_enabled: bool = True
    emit_cooldown_s: float = 10.0
    # SDR live-view stream (manual mode)
    view_enabled: bool = False
    view_push_url: str = ""
    view_sample_rate_hz: float = 8_000_000.0
    view_max_s: float = 600.0
    view_width: int = 480
    view_fps: float = 15.0
    view_standard: str = "auto"          # auto | pal | ntsc
    view_engine: str = "persistent"      # persistent (agent-lifetime ffmpeg) | legacy (per-session)
    view_osd_file: str = "/run/fpv/view-osd.txt"   # reload=1 drawtext textfile; "" disables OSD
    view_osd_font: str = DEFAULT_OSD_FONT


def load_video_config(env=None):
    """Build a VideoConfig from env (DSP/IO knobs). MQTT creds come from the reused
    agent/scan config.load_config(), not from here."""
    env = os.environ if env is None else env
    c = VideoConfig()
    c.frames_dir = env.get("FPV_FRAMES_DIR", c.frames_dir)
    if "FPV_LPF_CUTOFF_HZ" in env:
        c.lpf_cutoff_hz = float(env["FPV_LPF_CUTOFF_HZ"])
    if "FPV_FRAME_WIDTH" in env:
        c.frame_width = int(env["FPV_FRAME_WIDTH"])
    if "FPV_THUMB_MAX_WIDTH" in env:
        c.thumb_max_width = int(env["FPV_THUMB_MAX_WIDTH"])
    if "FPV_LINE_SNR_DB" in env:
        c.line_snr_db = float(env["FPV_LINE_SNR_DB"])
    if "FPV_HARM_SNR_DB" in env:
        c.harm_snr_db = float(env["FPV_HARM_SNR_DB"])
    if "FPV_BLANK_FRAC" in env:
        c.blank_frac = float(env["FPV_BLANK_FRAC"])
    if "FPV_DEFAULT_FS" in env:
        c.default_fs = float(env["FPV_DEFAULT_FS"])
    if "FPV_VIDEO_ENABLED" in env:
        c.video_enabled = env["FPV_VIDEO_ENABLED"].strip().lower() not in ("0", "false", "no", "")
    if "FPV_EMIT_COOLDOWN_S" in env:
        c.emit_cooldown_s = float(env["FPV_EMIT_COOLDOWN_S"])
    if "VIEW_ENABLED" in env:
        c.view_enabled = env["VIEW_ENABLED"].strip().lower() not in ("0", "false", "no", "")
    c.view_push_url = env.get("VIEW_PUSH_URL", c.view_push_url)
    if "VIEW_SAMPLE_RATE_HZ" in env:
        c.view_sample_rate_hz = float(env["VIEW_SAMPLE_RATE_HZ"])
    if "VIEW_MAX_S" in env:
        c.view_max_s = float(env["VIEW_MAX_S"])
    if "VIEW_WIDTH" in env:
        c.view_width = int(env["VIEW_WIDTH"])
    if "VIEW_FPS" in env:
        c.view_fps = float(env["VIEW_FPS"])
    if "VIEW_STANDARD" in env:
        c.view_standard = env["VIEW_STANDARD"].strip().lower()
    if "VIEW_ENGINE" in env:
        c.view_engine = env["VIEW_ENGINE"].strip().lower()
    c.view_osd_file = env.get("VIEW_OSD_FILE", c.view_osd_file)
    c.view_osd_font = env.get("VIEW_OSD_FONT", c.view_osd_font)
    return c
