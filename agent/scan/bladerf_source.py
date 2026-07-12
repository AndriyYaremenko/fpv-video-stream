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


def plan_windows(low_mhz: float, high_mhz: float, window_mhz: float) -> list[float]:
    """Window center frequencies (MHz) that tile [low, high] in `window_mhz` steps.
    Centers are clamped to high - window/2 so the device is never tuned past the band
    top: a 4700-6000 band would otherwise end on a 6005 center, and the bladeRF tops
    out at exactly 6 GHz (set_frequency RangeError). A band narrower than one window
    gets a single center in the band middle."""
    if high_mhz <= low_mhz or window_mhz <= 0:
        return []
    half = window_mhz / 2.0
    if high_mhz - low_mhz <= window_mhz:
        return [round((low_mhz + high_mhz) / 2.0, 3)]
    centers = []
    c = low_mhz + half
    while c - half < high_mhz:
        v = round(min(c, high_mhz - half), 3)
        if not centers or centers[-1] != v:
            centers.append(v)
        c += window_mhz
    return centers


def window_spectrum(iq: np.ndarray, center_hz: float, sample_rate_hz: float, seg: int = 1024) -> tuple[np.ndarray, np.ndarray]:
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
        finally:
            # Release the libusb handle too: a leaked open handle makes every reopen in
            # the same process fail with NoDevError until the process is restarted.
            try:
                self._radio.close()
            except Exception:
                LOG.exception("bladeRF close failed")


def open_bladerf_capture(gain_db, bandwidth_hz) -> BladerfDevice:
    """Open the first bladeRF, resolve channel/enums, and return a configured BladerfDevice.
    The only function that imports `bladerf`. Raises on no device."""
    import bladerf
    from bladerf import _bladerf            # BladeRF/CHANNEL_RX are re-exported at top level;
    radio = bladerf.BladeRF()               # the GainMode/Format/ChannelLayout enums are not.
    return BladerfDevice(
        radio, bladerf.CHANNEL_RX(0), gain_db, bandwidth_hz,
        gain_mode=_bladerf.GainMode.Manual,
        layout=_bladerf.ChannelLayout.RX_X1,
        fmt=_bladerf.Format.SC16_Q11,
    )


import threading
import time

from iqring import IqRing

VIEW_RING_SECONDS = 2.0            # rx ring depth: absorbs demod hiccups, bounds memory
VIEW_READ_SAMPLES = 65536         # samples per sync_rx pull (~8 ms at 8 MS/s): bounds stop latency


class BladerfViewSource:
    """CaptureSource for the view stream over an injected streaming radio factory.

    Mirrors HackrfSource, but bladeRF has no rx callback: a reader thread pulls
    fixed sub-chunks via radio.read() (blocking sync_rx) and writes raw SC16_Q11
    into the shared IqRing, so capture overlaps demod (the demod side drains the
    ring). read_chunk returns RAW bytes; run_stream_source decodes via to_iq."""

    bytes_per_sample = 4          # SC16_Q11: 2 x int16 per complex sample

    def __init__(self, open_radio, sample_rate_hz, read_samples=VIEW_READ_SAMPLES,
                 ring_s=VIEW_RING_SECONDS):
        self._open_radio = open_radio
        self._fs = float(sample_rate_hz)
        self._read_samples = int(read_samples)
        self._ring = IqRing(int(self._fs * self.bytes_per_sample * ring_s))
        self._radio = None
        self._freq_hz = None
        self._reader = None
        self._stop_reader = None

    @staticmethod
    def to_iq(raw):
        return iq_from_sc16q11(raw)

    @property
    def dropped_bytes(self):
        return self._ring.dropped_bytes

    def pending_bytes(self):
        return self._ring.pending()

    def tune(self, freq_hz):
        if self._radio is None:
            self._radio = self._open_radio()
            self._start_reader(self._radio)
        self._radio.set_frequency(int(freq_hz))
        self._freq_hz = int(freq_hz)
        self._ring.clear()               # the tune transient must not reach the demod

    def read_chunk(self, n_bytes, timeout_s):
        return self._ring.read(n_bytes, timeout_s)

    def recover(self):
        """USB-wedge watchdog action: close + reopen + retune."""
        freq = self._freq_hz
        self.close()
        if freq is not None:
            self.tune(freq)

    def close(self):
        r, self._radio = self._radio, None
        if r is None:
            return
        if self._stop_reader is not None:
            self._stop_reader.set()
        if self._reader is not None:
            self._reader.join(timeout=2.0)
        self._reader = None
        self._stop_reader = None
        try:
            r.close()
        except Exception:
            LOG.exception("bladeRF view close failed")

    def _start_reader(self, radio):
        self._stop_reader = threading.Event()
        self._reader = threading.Thread(target=self._read_loop, args=(radio, self._stop_reader),
                                        daemon=True)
        self._reader.start()

    def _read_loop(self, radio, stop):
        while not stop.is_set():
            try:
                buf = radio.read(self._read_samples)
            except Exception:
                if stop.is_set():
                    return
                time.sleep(0.01)         # rx timeout / transient: back off, re-check stop
                continue
            if buf:
                self._ring.write(buf)


class BladeRfViewRadio:
    """Streaming RX handle for BladerfViewSource: continuous sync_rx, retune via
    set_frequency. Enables the module on the first tune. The radio/channel/enums are
    injected (see open_bladerf_view_radio) so this class imports nothing from `bladerf`."""

    def __init__(self, radio, channel):
        self._radio = radio
        self._ch = channel
        self._enabled = False

    def set_frequency(self, hz):
        self._radio.set_frequency(self._ch, int(hz))
        if not self._enabled:
            self._radio.enable_module(self._ch, True)
            self._enabled = True

    def read(self, num_samples):
        buf = bytearray(int(num_samples) * 4)          # SC16_Q11 = 2 x int16 per sample
        self._radio.sync_rx(buf, int(num_samples))
        return bytes(buf)

    def close(self):
        try:
            if self._enabled:
                self._radio.enable_module(self._ch, False)
                self._enabled = False
        except Exception:
            LOG.exception("bladeRF view disable failed")
        finally:
            try:
                self._radio.close()
            except Exception:
                LOG.exception("bladeRF view radio close failed")


def open_bladerf_view_radio(gain_db, sample_rate_hz, bandwidth_hz) -> BladeRfViewRadio:
    """Open the first bladeRF configured for continuous view streaming.
    The only function here that imports `bladerf`. Raises on no device."""
    import bladerf
    from bladerf import _bladerf
    radio = bladerf.BladeRF()
    ch = bladerf.CHANNEL_RX(0)
    radio.set_gain_mode(ch, _bladerf.GainMode.Manual)
    radio.set_gain(ch, int(gain_db))
    radio.set_sample_rate(ch, int(sample_rate_hz))
    radio.set_bandwidth(ch, int(bandwidth_hz))
    radio.sync_config(
        layout=_bladerf.ChannelLayout.RX_X1, fmt=_bladerf.Format.SC16_Q11,
        num_buffers=16, buffer_size=8192, num_transfers=8, stream_timeout=3500,
    )
    return BladeRfViewRadio(radio, ch)
