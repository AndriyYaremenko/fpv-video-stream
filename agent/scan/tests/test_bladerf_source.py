import numpy as np

from bladerf_source import (
    iq_from_sc16q11, welch_psd, plan_windows, window_spectrum, assemble_band_spectrum, BladerfBackend,
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


def test_plan_windows_never_tunes_past_band_top():
    # B4 is 4700-6000 and the bladeRF tops out at EXACTLY 6 GHz: the unclamped last
    # center was 6005 -> set_frequency RangeError killed every sweep cycle on hardware.
    centers = plan_windows(4700.0, 6000.0, 30.0)
    assert max(centers) <= 6000.0 - 15.0              # center stays a half-window below top
    assert max(centers) + 15.0 >= 6000.0              # ...but the last window still covers the top edge
    assert centers == sorted(set(centers))            # ascending, no duplicates from clamping


def test_plan_windows_band_narrower_than_window_gets_mid_center():
    # A 10 MHz band with a 30 MHz window must tune the BAND middle, not low + window/2
    # (which would sit outside the band).
    assert plan_windows(100.0, 110.0, 30.0) == [105.0]


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


def _fake_capture_factory(fs):
    # Emit a +3 MHz tone ONLY in the window centered at 5810 MHz — a real plan_windows
    # center for 5645..5945 @ 30 MHz (there is no window exactly at 5800). Other windows
    # return near-silence, so the assembled spectrum has one clear peak at 5813 MHz.
    def capture(center_hz, sample_rate_hz, num_samples):
        t = np.arange(num_samples) / sample_rate_hz
        in_target = abs(center_hz - 5_810_000_000.0) < 1_000_000.0
        amp = 1.0 if in_target else 0.001
        return (amp * np.exp(2j * np.pi * 3.0e6 * t)).astype(np.complex64)
    return capture


def test_backend_sweep_band_finds_bump():
    fs = 40_000_000.0
    calls = []
    cap = _fake_capture_factory(fs)
    def counting_cap(c, s, n):
        calls.append((c, s, n))
        return cap(c, s, n)
    be = BladerfBackend(sample_rate_hz=fs, window_mhz=30.0, sweep_samples=8192, capture=counting_cap)
    spec = be.sweep_band(5645.0, 5945.0, "5.8G")
    assert spec.band == "5.8G"
    assert len(spec.freqs_mhz) > 0
    # every window captured at the configured sweep sample rate + sample count
    assert all(s == fs and n == 8192 for _, s, n in calls)
    # the strongest bin sits at the injected signal (5810 window + 3 MHz = 5813 MHz)
    peak_mhz = spec.freqs_mhz[int(np.argmax(spec.power_dbm))]
    assert abs(peak_mhz - 5813.0) < 1.0


def test_backend_dwell_passes_through_capture():
    seen = {}
    def cap(center_hz, sample_rate_hz, num_samples):
        seen.update(center_hz=center_hz, sr=sample_rate_hz, n=num_samples)
        return np.ones(num_samples, dtype=np.complex64)
    be = BladerfBackend(sample_rate_hz=40e6, window_mhz=30.0, sweep_samples=8192, capture=cap)
    iq = be.dwell(5800.0, 20_000_000.0, 4096)
    assert len(iq) == 4096
    assert seen == {"center_hz": 5_800_000_000.0, "sr": 20_000_000.0, "n": 4096}


def test_bladerf_device_retunes_and_converts():
    import bladerf_source as bs

    events = []

    class _FakeRadio:
        def set_sample_rate(self, ch, v): events.append(("sr", int(v)))
        def set_bandwidth(self, ch, v): events.append(("bw", int(v)))
        def set_frequency(self, ch, v): events.append(("freq", int(v)))
        def set_gain_mode(self, ch, m): events.append(("gainmode", m))
        def set_gain(self, ch, v): events.append(("gain", int(v)))
        def sync_config(self, **kw): events.append(("sync_config", kw.get("num_buffers")))
        def enable_module(self, ch, on): events.append(("enable", on))
        def sync_rx(self, buf, n):
            # two samples: (2048,0) -> 1+0j, (0,2048) -> 0+1j
            buf[:] = np.array([2048, 0, 0, 2048], dtype="int16").tobytes()
        def close(self): events.append(("close",))

    dev = bs.BladerfDevice(_FakeRadio(), channel="RX0", gain_db=30, bandwidth_hz=18_000_000.0,
                           gain_mode="manual", layout="RX_X1", fmt="SC16_Q11")
    iq = dev.capture(5_800_000_000.0, 40_000_000.0, 2)

    assert ("freq", 5_800_000_000) in events
    assert ("sr", 40_000_000) in events
    assert ("gain", 30) in events
    assert ("bw", 18_000_000) in events
    assert ("gainmode", "manual") in events
    assert any(e[0] == "sync_config" for e in events)
    assert len(iq) == 2
    assert abs(iq[0] - (1 + 0j)) < 1e-6 and abs(iq[1] - (0 + 1j)) < 1e-6

    # sample-rate is cached: a second capture at the same rate does not re-set it,
    # and the module is enabled only once.
    dev.capture(5_800_000_000.0, 40_000_000.0, 2)
    assert events.count(("sr", 40_000_000)) == 1
    assert events.count(("enable", True)) == 1
    # close() disables the module AND releases the radio handle: a leaked handle made
    # every reopen in the same process fail with NoDevError after a device error.
    dev.close()
    assert ("enable", False) in events
    assert ("close",) in events
