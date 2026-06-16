import numpy as np

from sweeper import parse_sweep_output


def test_parse_sweep_output_basic():
    lines = [
        "2024-01-01, 12:00:00.0, 5645000000, 5650000000, 1000000.0, 20, -90.0, -88.0, -50.0, -89.0, -90.0",
    ]
    spec = parse_sweep_output(lines, "5.8G")
    assert spec.band == "5.8G"
    assert len(spec.freqs_mhz) == 5
    assert abs(spec.freqs_mhz[0] - 5645.5) < 1e-6     # first bin center
    assert spec.power_dbm[2] == -50.0


def test_parse_sweep_output_sorts_and_skips_blank():
    lines = [
        "",
        "2024-01-01, 12:00:00.0, 5650000000, 5652000000, 1000000.0, 20, -70.0, -71.0",
        "2024-01-01, 12:00:00.0, 5645000000, 5647000000, 1000000.0, 20, -90.0, -91.0",
    ]
    spec = parse_sweep_output(lines, "5.8G")
    assert len(spec.freqs_mhz) == 4
    assert list(spec.freqs_mhz) == sorted(spec.freqs_mhz)
    assert spec.freqs_mhz[0] < spec.freqs_mhz[-1]
