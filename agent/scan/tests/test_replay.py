import numpy as np

from sweeper import sweep_replay
from dweller import dwell_replay


def test_sweep_replay_reads_csv(tmp_path):
    csv = tmp_path / "sweep_5.8G.csv"
    csv.write_text(
        "2024-01-01, 12:00:00.0, 5645000000, 5650000000, 1000000.0, 20, -90.0, -50.0, -90.0\n",
        encoding="utf-8",
    )
    spec = sweep_replay(str(csv), "5.8G")
    assert spec.band == "5.8G"
    assert len(spec.power_dbm) == 3
    assert spec.power_dbm[1] == -50.0


def test_dwell_replay_reads_iq(tmp_path):
    binp = tmp_path / "iq_5.8G.bin"
    binp.write_bytes(bytes([127, 0, 0, 127]))
    iq = dwell_replay(str(binp))
    assert iq.shape == (2,)
