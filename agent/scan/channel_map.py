from typing import Optional

# Representative FPV channel centers (MHz). Informational only.
CHANNELS = {
    # 5.8 GHz — Raceband (R) + Fatshark (F)
    "R1": 5658, "R2": 5695, "R3": 5732, "R4": 5769,
    "R5": 5806, "R6": 5843, "R7": 5880, "R8": 5917,
    "F1": 5740, "F2": 5760, "F3": 5780, "F4": 5800,
    "F5": 5820, "F6": 5840, "F7": 5860, "F8": 5880,
    # 1.2 GHz (L)
    "L1": 1080, "L2": 1120, "L3": 1160, "L4": 1200,
    "L5": 1240, "L6": 1280, "L7": 1320, "L8": 1360,
    # 2.4 GHz (G)
    "G1": 2414, "G2": 2432, "G3": 2450, "G4": 2468, "G5": 2490,
}


def nearest_channel(center_mhz: float, tolerance_mhz: float = 10.0) -> Optional[str]:
    best = None
    best_d = tolerance_mhz
    for name, freq in CHANNELS.items():
        d = abs(freq - center_mhz)
        if d <= best_d:
            best_d = d
            best = name
    return best
