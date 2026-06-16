from sweeper import build_sweep_cmd
from dweller import build_transfer_cmd


def test_build_sweep_cmd():
    cmd = build_sweep_cmd(5645.0, 5945.0, 100_000.0)
    assert cmd[0] == "hackrf_sweep"
    assert "-f" in cmd and "5645:5945" in cmd
    assert "-w" in cmd and "100000" in cmd
    assert "-1" in cmd            # one-shot


def test_build_transfer_cmd():
    cmd = build_transfer_cmd(5_800_000_000.0, 20_000_000.0, 2_000_000, "/tmp/iq.bin")
    assert cmd[0] == "hackrf_transfer"
    assert "-r" in cmd and "/tmp/iq.bin" in cmd
    assert "-f" in cmd and "5800000000" in cmd
    assert "-s" in cmd and "20000000" in cmd
    assert "-n" in cmd and "2000000" in cmd
