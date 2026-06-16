"""Generate small SYNTHETIC replay fixtures (sweep CSV + IQ bin) for all three bands.

These let `SCAN_SOURCE=replay` run end-to-end on a dev box with no HackRF. They are NOT
real captures -- replace them with real recordings on the Pi (see README "Record real
fixtures") before tuning classifier thresholds.

Run from agent/scan/:  .\\.venv\\Scripts\\python.exe tests/fixtures/generate_fixtures.py
"""
import os

import numpy as np

HERE = os.path.dirname(__file__)

# (band, low_mhz, high_mhz, bump_center_mhz | None, bump_half_width_mhz)
BANDS = [
    ("5.8G", 5645, 5945, 5800, 11),
    ("1.2G", 1080, 1360, 1200, 6),
    ("2.4G", 2370, 2510, None, 0),     # flat: no carrier -> no detection
]


def write_sweep(band, low, high, bump_center, half):
    bins = []
    for i in range(high - low):            # 1 MHz bins
        f = low + i
        if bump_center is not None and abs(f - bump_center) <= half:
            bins.append(-50.0)
        else:
            bins.append(-90.0)
    row = ["2024-01-01", "12:00:00.0", f"{low}000000", f"{high}000000", "1000000.0", "20"]
    row += [f"{b:.1f}" for b in bins]
    with open(os.path.join(HERE, f"sweep_{band}.csv"), "w", encoding="utf-8") as fh:
        fh.write(", ".join(row) + "\n")


def write_iq(band):
    fs = 20_000_000.0
    n = 8192
    t = np.arange(n) / fs
    tone = np.exp(2j * np.pi * 1.0e6 * t)
    iq8 = np.empty(2 * n, dtype=np.int8)
    iq8[0::2] = np.clip(np.real(tone) * 100, -127, 127).astype(np.int8)
    iq8[1::2] = np.clip(np.imag(tone) * 100, -127, 127).astype(np.int8)
    with open(os.path.join(HERE, f"iq_{band}.bin"), "wb") as fh:
        fh.write(iq8.tobytes())


def main():
    for band, low, high, center, half in BANDS:
        write_sweep(band, low, high, center, half)
        write_iq(band)
    print("wrote fixtures to", HERE)


if __name__ == "__main__":
    main()
