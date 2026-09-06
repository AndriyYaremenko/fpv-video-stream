"""Auto-relay config (env-driven), mirror of agent/tx/txconfig.py. Role-gated by RELAY_ENABLED."""
import os
from dataclasses import dataclass


def _truthy(v):
    return v.strip().lower() not in ("0", "false", "no", "")


@dataclass
class RelayConfig:
    relay_enabled: bool = False
    armed: bool = False                # arm state at process start (dashboard toggles it live)
    dst_mhz: float = 5771.0            # relay target (B3)
    max_s: float = 600.0               # safety deadline per session; next cycle may re-acquire
    guard_mhz: float = 25.0            # ignore sources within dst±guard (TX would feed back into RX)
    lost_db: float = 12.0              # source "gone": raw RX RMS this far below the session reference...
    lost_s: float = 5.0                # ...for this long
    min_rms: float = 40.0              # session reference below this = source already dead at open (real ~460)
    fs_hz: float = 20_000_000.0        # RX and TX sample rate (bandwidth = fs)
    rx_gain_db: int = 40
    tx_gain_db: int = 60
    agc_target: float = 1600.0         # post-AGC peak (of 2047)
    block_samples: int = 32768
    src_bands: str = ""                # comma list of band ids allowed as sources ("" = all); e.g. "1.2G,2.4G,3.3G"
                                       # — a source in the target's own band is pointless to relay and feeds back
    min_sync_db: float = 0.0           # ignore hits with sync SNR below this (band-edge/spur false locks); 0 = off


def load_relay_config(env=None):
    env = os.environ if env is None else env
    c = RelayConfig()
    if "RELAY_ENABLED" in env: c.relay_enabled = _truthy(env["RELAY_ENABLED"])
    if "RELAY_ARMED" in env: c.armed = _truthy(env["RELAY_ARMED"])
    if "RELAY_DST_MHZ" in env: c.dst_mhz = float(env["RELAY_DST_MHZ"])
    if "RELAY_MAX_S" in env: c.max_s = float(env["RELAY_MAX_S"])
    if "RELAY_GUARD_MHZ" in env: c.guard_mhz = float(env["RELAY_GUARD_MHZ"])
    if "RELAY_LOST_DB" in env: c.lost_db = float(env["RELAY_LOST_DB"])
    if "RELAY_LOST_S" in env: c.lost_s = float(env["RELAY_LOST_S"])
    if "RELAY_MIN_RMS" in env: c.min_rms = float(env["RELAY_MIN_RMS"])
    if "RELAY_FS_HZ" in env: c.fs_hz = float(env["RELAY_FS_HZ"])
    if "RELAY_RX_GAIN_DB" in env: c.rx_gain_db = int(env["RELAY_RX_GAIN_DB"])
    if "RELAY_TX_GAIN_DB" in env: c.tx_gain_db = int(env["RELAY_TX_GAIN_DB"])
    if "RELAY_AGC_TARGET" in env: c.agc_target = float(env["RELAY_AGC_TARGET"])
    c.src_bands = env.get("RELAY_SRC_BANDS", c.src_bands)
    if "RELAY_MIN_SYNC_DB" in env: c.min_sync_db = float(env["RELAY_MIN_SYNC_DB"])
    return c
