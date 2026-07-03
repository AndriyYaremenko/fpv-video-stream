import numpy as np

from bladerf_source import (
    iq_from_sc16q11, welch_psd, plan_windows, window_spectrum, assemble_band_spectrum,
)
from models import Spectrum


def test_iq_from_sc16q11_scales_and_deinterleaves():
    raw = np.array([2048, 0, 0, -2048], dtype=np.int16).tobytes()
    iq = iq_from_sc16q11(raw)
    assert iq.shape == (2,)
    assert abs(iq[0] - (1.0 + 0.0j)) < 1e-6
    assert abs(iq[1] - (0.0 - 1.0j)) < 1e-6


def test_plan_windows_covers_range():
    centers = plan_windows(5645.0, 5945.0, 30.0)
    assert centers[0] == 5660.0                       # low + window/2
    assert all(round(centers[i+1] - centers[i], 3) == 30.0 for i in range(len(centers) - 1))
    assert centers[-1] + 15.0 >= 5945.0               # last window reaches the top
    assert centers[0] - 15.0 <= 5645.0                # first window reaches the bottom


def test_plan_windows_rejects_bad_input():
    assert plan_windows(100.0, 100.0, 30.0) == []
    assert plan_windows(200.0, 100.0, 30.0) == []
    assert plan_windows(100.0, 200.0, 0.0) == []


def test_window_spectrum_peaks_at_signal_frequency():
    fs = 40_000_000.0
    center = 5_800_000_000.0
    n = 8192
    t = np.arange(n) / fs
    iq = np.exp(2j * np.pi * 5.0e6 * t)               # +5 MHz tone within the window
    freqs_mhz, power_db = window_spectrum(iq, center, fs, seg=1024)
    peak_mhz = freqs_mhz[int(np.argmax(power_db))]
    assert abs(peak_mhz - (center + 5.0e6) / 1e6) < 0.2


def test_assemble_band_spectrum_sorts_and_concatenates():
    a = (np.array([5810.0, 5800.0]), np.array([-40.0, -80.0]))
    b = (np.array([5700.0, 5710.0]), np.array([-70.0, -75.0]))
    spec = assemble_band_spectrum([a, b], "5.8G")
    assert isinstance(spec, Spectrum)
    assert spec.band == "5.8G"
    assert list(spec.freqs_mhz) == sorted(spec.freqs_mhz)
    assert spec.power_dbm[0] == -70.0                 # 5700 MHz bin
    assert spec.power_dbm[list(spec.freqs_mhz).index(5800.0)] == -80.0


def test_assemble_band_spectrum_empty():
    spec = assemble_band_spectrum([], "2.4G")
    assert spec.band == "2.4G"
    assert len(spec.freqs_mhz) == 0 and len(spec.power_dbm) == 0
