import os
from dataclasses import dataclass


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
    view_engine: str = "persistent"      # persistent (agent-lifetime ffmpeg) | legacy (per-session)


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
    if "VIEW_ENGINE" in env:
        c.view_engine = env["VIEW_ENGINE"].strip().lower()
    return c
