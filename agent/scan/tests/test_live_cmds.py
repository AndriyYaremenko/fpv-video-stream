from sweeper import build_sweep_cmd
from dweller import build_transfer_cmd


def _val_after(cmd, flag):
    return cmd[cmd.index(flag) + 1]


def test_build_sweep_cmd():
    cmd = build_sweep_cmd(5645.0, 5945.0, 100_000.0)
    assert cmd[0] == "hackrf_sweep"
    assert _val_after(cmd, "-f") == "5645:5945"
    assert _val_after(cmd, "-w") == "100000"
    assert "-1" in cmd            # one-shot


def test_build_transfer_cmd():
    cmd = build_transfer_cmd(5_800_000_000.0, 20_000_000.0, 2_000_000, "/tmp/iq.bin")
    assert cmd[0] == "hackrf_transfer"
    assert _val_after(cmd, "-r") == "/tmp/iq.bin"
    assert _val_after(cmd, "-f") == "5800000000"
    assert _val_after(cmd, "-s") == "20000000"
    assert _val_after(cmd, "-n") == "2000000"
    assert _val_after(cmd, "-a") == "1"
