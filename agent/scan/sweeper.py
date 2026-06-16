import subprocess
from typing import Iterable, List

import numpy as np

from models import Spectrum


def parse_sweep_output(lines: Iterable[str], band: str) -> Spectrum:
    freqs: List[float] = []
    powers: List[float] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 7:
            continue
        try:
            hz_low = float(parts[2])
            bin_w = float(parts[4])
            db_vals = [float(x) for x in parts[6:]]
        except ValueError:
            continue
        for i, db in enumerate(db_vals):
            center_hz = hz_low + bin_w * (i + 0.5)
            freqs.append(center_hz / 1e6)
            powers.append(db)
    f = np.array(freqs, dtype=float)
    p = np.array(powers, dtype=float)
    order = np.argsort(f)
    return Spectrum(band=band, freqs_mhz=f[order], power_dbm=p[order])


def build_sweep_cmd(low_mhz: float, high_mhz: float, bin_hz: float) -> list:
    return [
        "hackrf_sweep",
        "-f", f"{int(low_mhz)}:{int(high_mhz)}",
        "-w", str(int(bin_hz)),
        "-1",
    ]


def sweep_live(low_mhz: float, high_mhz: float, bin_hz: float, timeout: float = 15.0) -> list:
    cmd = build_sweep_cmd(low_mhz, high_mhz, bin_hz)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"hackrf_sweep failed (exit {e.returncode}): {e.stderr or ''}") from e
    return proc.stdout.splitlines()


def sweep_replay(csv_path: str, band: str) -> Spectrum:
    with open(csv_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    return parse_sweep_output(lines, band)
