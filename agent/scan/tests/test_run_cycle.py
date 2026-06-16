import json

import numpy as np

from config import Config
import main


def _write_fixtures(tmp_path):
    # Sweep CSV: flat -90 dB with a 22 MHz bump at -50 dB around 5800 MHz, 1 MHz bins.
    lo = 5645_000000
    bins = []
    for i in range(300):                       # 5645..5945 MHz, 1 MHz bins
        f_mhz = 5645 + i
        bins.append(-50.0 if 5789 <= f_mhz <= 5811 else -90.0)
    row = ["2024-01-01", "12:00:00.0", str(lo), str(lo + 300_000000), "1000000.0", "20"]
    row += [str(x) for x in bins]
    (tmp_path / "sweep_5.8G.csv").write_text(", ".join(row) + "\n", encoding="utf-8")

    # IQ blob: a strong tone (int8) so the dwell has real samples to analyze.
    fs = 20_000_000.0
    n = 40_000
    t = np.arange(n) / fs
    tone = np.exp(2j * np.pi * 1.0e6 * t)
    iq8 = np.empty(2 * n, dtype=np.int8)
    iq8[0::2] = np.clip(np.real(tone) * 100, -127, 127).astype(np.int8)
    iq8[1::2] = np.clip(np.imag(tone) * 100, -127, 127).astype(np.int8)
    (tmp_path / "iq_5.8G.bin").write_bytes(iq8.tobytes())


def _config(tmp_path):
    c = Config()
    c.source = "replay"
    c.fixtures_dir = str(tmp_path)
    c.state_path = str(tmp_path / "scan.json")
    c.server_url = "http://127.0.0.1:1"        # unreachable -> POST silently fails
    c.bands = {"5.8G": (5645.0, 5945.0)}        # single band for the test
    return c


def test_run_cycle_end_to_end(tmp_path):
    _write_fixtures(tmp_path)
    cfg = _config(tmp_path)

    payload = main.run_cycle(cfg, now_ts=1718530000)

    assert payload["scanner_id"] == "scan-01"
    assert len(payload["detections"]) == 1
    det = payload["detections"][0]
    assert det["band"] == "5.8G"
    assert abs(det["center_mhz"] - 5800.0) < 2.0
    assert det["class"] in {"analog", "digital", "unknown"}
    assert payload["occupancy"]["5.8G"] > 0.0

    saved = json.loads((tmp_path / "scan.json").read_text(encoding="utf-8"))
    assert saved == payload
