from typing import List

import numpy as np

from models import Spectrum, Candidate


def find_candidates(
    spectrum: Spectrum,
    snr_threshold_db: float,
    min_bandwidth_mhz: float,
    noise_percentile: float = 50.0,
) -> List[Candidate]:
    power = spectrum.power_dbm
    freqs = spectrum.freqs_mhz
    if len(power) == 0:
        return []
    noise_floor = float(np.percentile(power, noise_percentile))
    mask = power > (noise_floor + snr_threshold_db)

    candidates: List[Candidate] = []
    n = len(mask)
    idx = 0
    while idx < n:
        if not mask[idx]:
            idx += 1
            continue
        start = idx
        while idx < n and mask[idx]:
            idx += 1
        end = idx - 1
        lo = float(freqs[start])
        hi = float(freqs[end])
        bw = hi - lo
        if bw >= min_bandwidth_mhz:
            run_power = power[start:end + 1]
            peak = float(np.max(run_power))
            candidates.append(Candidate(
                band=spectrum.band,
                center_mhz=(lo + hi) / 2.0,
                bandwidth_mhz=bw,
                power_dbm=peak,
                snr_db=peak - noise_floor,
            ))
    return candidates
