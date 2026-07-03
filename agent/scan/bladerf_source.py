import logging

import numpy as np

from models import Spectrum

LOG = logging.getLogger("scan.bladerf")
_EPS = 1e-12


def iq_from_sc16q11(raw: bytes) -> np.ndarray:
    """bladeRF SC16_Q11 (interleaved int16, 11 fractional bits) -> normalized complex64."""
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    return ((x[0::2] + 1j * x[1::2]) / 2048.0).astype(np.complex64)


def welch_psd(iq: np.ndarray, seg: int = 1024) -> np.ndarray:
    """Welch-style averaged, fftshifted power spectral density (NOT normalized)."""
    n = len(iq)
    if n < seg:
        seg = n
    if seg <= 0:
        return np.zeros(0)
    win = np.hanning(seg)
    nseg = max(1, n // seg)
    acc = np.zeros(seg)
    used = 0
    for k in range(nseg):
        chunk = iq[k * seg:(k + 1) * seg]
        if len(chunk) < seg:
            break
        acc += np.abs(np.fft.fftshift(np.fft.fft(chunk * win))) ** 2
        used += 1
    return acc / used if used else np.zeros(seg)


def plan_windows(low_mhz: float, high_mhz: float, window_mhz: float) -> list:
    """Window center frequencies (MHz) that tile [low, high] in `window_mhz` steps."""
    if high_mhz <= low_mhz or window_mhz <= 0:
        return []
    centers = []
    c = low_mhz + window_mhz / 2.0
    while c - window_mhz / 2.0 < high_mhz:
        centers.append(round(c, 3))
        c += window_mhz
    return centers


def window_spectrum(iq: np.ndarray, center_hz: float, sample_rate_hz: float, seg: int = 1024):
    """One tuned window -> (freqs_mhz, power_db) with absolute frequency axis."""
    psd = welch_psd(iq, seg)
    n = len(psd)
    if n == 0:
        return np.zeros(0), np.zeros(0)
    offsets = (np.arange(n) - n // 2) * (sample_rate_hz / n)
    freqs_mhz = (center_hz + offsets) / 1e6
    power_db = 10.0 * np.log10(psd + _EPS)
    return freqs_mhz, power_db


def assemble_band_spectrum(parts, band: str) -> Spectrum:
    """Concatenate per-window (freqs_mhz, power_db) parts into one sorted Spectrum."""
    if not parts:
        return Spectrum(band=band, freqs_mhz=np.zeros(0), power_dbm=np.zeros(0))
    f = np.concatenate([p[0] for p in parts])
    p = np.concatenate([p[1] for p in parts])
    order = np.argsort(f)
    return Spectrum(band=band, freqs_mhz=f[order], power_dbm=p[order])


class BladerfBackend:
    """Turns tuned IQ captures into Spectrum sweeps and dwell IQ. `capture` is injected so
    the sweep/dwell logic is fully testable without hardware; production passes the real
    bladeRF capture (see open_bladerf_capture)."""

    def __init__(self, sample_rate_hz, window_mhz, sweep_samples, capture):
        self.sample_rate_hz = float(sample_rate_hz)
        self.window_mhz = float(window_mhz)
        self.sweep_samples = int(sweep_samples)
        self._capture = capture

    def sweep_band(self, low_mhz, high_mhz, band) -> Spectrum:
        parts = []
        for c_mhz in plan_windows(low_mhz, high_mhz, self.window_mhz):
            iq = self._capture(c_mhz * 1e6, self.sample_rate_hz, self.sweep_samples)
            parts.append(window_spectrum(iq, c_mhz * 1e6, self.sample_rate_hz))
        return assemble_band_spectrum(parts, band)

    def dwell(self, center_mhz, sample_rate_hz, num_samples) -> np.ndarray:
        return self._capture(center_mhz * 1e6, float(sample_rate_hz), int(num_samples))


class BladerfDevice:
    """Holds an open bladeRF RX channel and captures a block of IQ per call, retuning as needed.
    The radio handle, channel object, and libbladeRF enums are injected (see open_bladerf_capture)
    so this class imports nothing from `bladerf` and is fully testable with a fake radio."""

    def __init__(self, radio, channel, gain_db, bandwidth_hz, gain_mode, layout, fmt):
        self._radio = radio
        self._ch = channel
        self._enabled = False
        self._sr = None
        radio.set_gain_mode(channel, gain_mode)
        radio.set_gain(channel, int(gain_db))
        radio.set_bandwidth(channel, int(bandwidth_hz))
        radio.sync_config(
            layout=layout, fmt=fmt,
            num_buffers=16, buffer_size=8192, num_transfers=8, stream_timeout=3500,
        )

    def capture(self, center_hz, sample_rate_hz, num_samples) -> np.ndarray:
        sr = int(sample_rate_hz)
        if sr != self._sr:
            self._radio.set_sample_rate(self._ch, sr)
            self._sr = sr
        self._radio.set_frequency(self._ch, int(center_hz))
        if not self._enabled:
            self._radio.enable_module(self._ch, True)
            self._enabled = True
        buf = bytearray(int(num_samples) * 4)          # SC16_Q11 = 2 x int16 per sample
        self._radio.sync_rx(buf, int(num_samples))
        return iq_from_sc16q11(bytes(buf))

    def close(self):
        try:
            if self._enabled:
                self._radio.enable_module(self._ch, False)
                self._enabled = False
        except Exception:
            LOG.exception("bladeRF disable failed")


def open_bladerf_capture(gain_db, bandwidth_hz) -> BladerfDevice:
    """Open the first bladeRF, resolve channel/enums, and return a configured BladerfDevice.
    The only function that imports `bladerf`. Raises on no device."""
    import bladerf
    radio = bladerf.BladeRF()
    return BladerfDevice(
        radio, bladerf.CHANNEL_RX(0), gain_db, bandwidth_hz,
        gain_mode=bladerf.GainMode.Manual,
        layout=bladerf.ChannelLayout.RX_X1,
        fmt=bladerf.Format.SC16_Q11,
    )
