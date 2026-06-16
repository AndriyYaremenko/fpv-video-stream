import numpy as np

from sweeper import parse_sweep_output


def test_parse_sweep_output_basic():
    lines = [
        "2024-01-01, 12:00:00.0, 5645000000, 5650000000, 1000000.0, 20, -90.0, -88.0, -50.0, -89.0, -90.0",
    ]
    spec = parse_sweep_output(lines, "5.8G")
    assert spec.band == "5.8G"
    assert len(spec.freqs_mhz) == 5
    assert abs(spec.freqs_mhz[0] - 5645.5) < 1e-6
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
    # powers must stay aligned to their (now sorted) frequencies
    assert spec.power_dbm[0] == -90.0     # 5645.5 MHz bin
    assert spec.power_dbm[2] == -70.0     # 5650.5 MHz bin


def test_parse_sweep_output_empty_input():
    spec = parse_sweep_output([], "5.8G")
    assert len(spec.freqs_mhz) == 0
    assert len(spec.power_dbm) == 0


def test_parse_sweep_output_skips_malformed_values():
    lines = [
        "2024-01-01, 12:00:00.0, 5645000000, 5647000000, 1000000.0, 20, -90.0, N/A",
        "2024-01-01, 12:00:00.0, 5650000000, 5652000000, 1000000.0, 20, -70.0, -71.0",
    ]
    spec = parse_sweep_output(lines, "5.8G")
    assert len(spec.freqs_mhz) == 2          # malformed row skipped entirely
    assert spec.power_dbm[0] == -70.0
