"""TX-generator config (env-driven), mirror of agent/video/vconfig.py. Role-gated by TX_ENABLED."""
import os
from dataclasses import dataclass


@dataclass
class TxConfig:
    tx_enabled: bool = False
    tx_dir: str = "/var/lib/fpv/tx"
    tx_cache_bin: str = "/var/lib/fpv/tx/.cache/current.bin"
    tx_max_s: float = 120.0            # auto-stop deadline (safety)
    fs_hz: float = 20_000_000.0
    deviation_hz: float = 4_000_000.0
    standard: str = "PAL"
    width: int = 640
    height: int = 512
    fps: int = 25
    secs: float = 3.0                  # render clip length (Phase-0 max_secs), NOT TX duration
    vbi_lines: int = 6
    gain_db: int = 30


def load_tx_config(env=None):
    env = os.environ if env is None else env
    c = TxConfig()
    if "TX_ENABLED" in env:
        c.tx_enabled = env["TX_ENABLED"].strip().lower() not in ("0", "false", "no", "")
    c.tx_dir = env.get("FPV_TX_DIR", c.tx_dir)
    c.tx_cache_bin = env.get("FPV_TX_CACHE_BIN", c.tx_cache_bin)
    if "FPV_TX_MAX_S" in env: c.tx_max_s = float(env["FPV_TX_MAX_S"])
    if "FPV_TX_FS_HZ" in env: c.fs_hz = float(env["FPV_TX_FS_HZ"])
    if "FPV_TX_DEVIATION_HZ" in env: c.deviation_hz = float(env["FPV_TX_DEVIATION_HZ"])
    if "FPV_TX_STANDARD" in env: c.standard = env["FPV_TX_STANDARD"].strip().upper()
    if "FPV_TX_WIDTH" in env: c.width = int(env["FPV_TX_WIDTH"])
    if "FPV_TX_HEIGHT" in env: c.height = int(env["FPV_TX_HEIGHT"])
    if "FPV_TX_FPS" in env: c.fps = int(env["FPV_TX_FPS"])
    if "FPV_TX_SECS" in env: c.secs = float(env["FPV_TX_SECS"])
    if "FPV_TX_VBI_LINES" in env: c.vbi_lines = int(env["FPV_TX_VBI_LINES"])
    if "FPV_TX_GAIN_DB" in env: c.gain_db = int(env["FPV_TX_GAIN_DB"])
    return c
