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
        hz_low = float(parts[2])
        bin_w = float(parts[4])
        db_vals = [float(x) for x in parts[6:]]
        for i, db in enumerate(db_vals):
            center_hz = hz_low + bin_w * (i + 0.5)
            freqs.append(center_hz / 1e6)
            powers.append(db)
    f = np.array(freqs, dtype=float)
    p = np.array(powers, dtype=float)
    order = np.argsort(f)
    return Spectrum(band=band, freqs_mhz=f[order], power_dbm=p[order])
