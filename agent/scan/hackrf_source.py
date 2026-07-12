"""In-process HackRF capture for the SDR live view.

libhackrf streams rx into a bounded ring; retune is hackrf_set_freq (ms) plus
a ring flush — no subprocess restart. Mirrors bladerf_source.py: everything
above the radio is injected/testable without hardware; LibHackrfRadio is the
only cffi-touching code."""
import logging

from iqring import IqRing

LOG = logging.getLogger("scan.hackrf")


RING_SECONDS = 2.0           # rx ring depth: absorbs demod hiccups, bounds memory


class HackrfSource:
    """CaptureSource for the view stream over an injected radio factory.

    tune() opens the device lazily (so the sweep can own it between sessions)
    and flushes the tune transient; close() releases it back to the sweep's
    one-shot hackrf_transfer subprocesses; recover() is the wedge watchdog's
    close+reopen+retune. A later bladeRF backend only needs the same duck type."""

    def __init__(self, open_radio, sample_rate_hz, ring_s=RING_SECONDS):
        self._open_radio = open_radio
        self._fs = float(sample_rate_hz)
        self._ring = IqRing(int(self._fs * 2 * ring_s))
        self._radio = None
        self._freq_hz = None

    @property
    def dropped_bytes(self):
        return self._ring.dropped_bytes

    def pending_bytes(self):
        return self._ring.pending()

    def tune(self, freq_hz):
        if self._radio is None:
            self._radio = self._open_radio()
            self._radio.start_rx(self._ring.write, self._fs)
        self._radio.set_freq(int(freq_hz))
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
        try:
            r.close()
        except Exception:
            LOG.exception("hackrf close failed")


_CDEF = """
typedef struct hackrf_device hackrf_device;
typedef struct {
    hackrf_device* device;
    uint8_t* buffer;
    int buffer_length;
    int valid_length;
    void* rx_ctx;
    void* tx_ctx;
} hackrf_transfer;
typedef int (*hackrf_sample_block_cb_fn)(hackrf_transfer* transfer);
int hackrf_init(void);
int hackrf_open(hackrf_device** device);
int hackrf_close(hackrf_device* device);
int hackrf_set_freq(hackrf_device* device, const uint64_t freq_hz);
int hackrf_set_sample_rate(hackrf_device* device, const double freq_hz);
int hackrf_set_lna_gain(hackrf_device* device, uint32_t value);
int hackrf_set_vga_gain(hackrf_device* device, uint32_t value);
int hackrf_set_amp_enable(hackrf_device* device, const uint8_t value);
int hackrf_start_rx(hackrf_device* device, hackrf_sample_block_cb_fn callback, void* rx_ctx);
int hackrf_stop_rx(hackrf_device* device);
"""


def _ck(rc, what):
    if rc != 0:
        raise RuntimeError(f"{what} failed (rc={rc})")


class LibHackrfRadio:
    """The only hardware-touching class: cffi over libhackrf.so.0.
    hackrf_set_sample_rate auto-selects the matching baseband filter (same
    default hackrf_transfer uses), so no explicit filter call is needed."""

    def __init__(self, lna_db, vga_db, amp):
        from cffi import FFI
        self._ffi = FFI()
        self._ffi.cdef(_CDEF)
        self._lib = self._ffi.dlopen("libhackrf.so.0")
        _ck(self._lib.hackrf_init(), "hackrf_init")
        dev = self._ffi.new("hackrf_device**")
        _ck(self._lib.hackrf_open(dev), "hackrf_open")
        self._dev = dev[0]
        self._lna, self._vga, self._amp = int(lna_db), int(vga_db), int(amp)
        self._cb = None

    def start_rx(self, sink, sample_rate_hz):
        lib, ffi = self._lib, self._ffi
        _ck(lib.hackrf_set_sample_rate(self._dev, float(sample_rate_hz)), "set_sample_rate")
        _ck(lib.hackrf_set_lna_gain(self._dev, self._lna), "set_lna_gain")
        _ck(lib.hackrf_set_vga_gain(self._dev, self._vga), "set_vga_gain")
        _ck(lib.hackrf_set_amp_enable(self._dev, self._amp), "set_amp_enable")

        @ffi.callback("int(hackrf_transfer*)")
        def _on_rx(transfer):
            n = transfer.valid_length
            if n > 0:
                sink(bytes(ffi.buffer(transfer.buffer, n)))
            return 0

        self._cb = _on_rx        # MUST keep a ref: cffi callbacks are GC'd otherwise
        _ck(lib.hackrf_start_rx(self._dev, self._cb, ffi.NULL), "start_rx")

    def set_freq(self, freq_hz):
        _ck(self._lib.hackrf_set_freq(self._dev, int(freq_hz)), "set_freq")

    def close(self):
        try:
            self._lib.hackrf_stop_rx(self._dev)
        except Exception:
            pass
        try:
            self._lib.hackrf_close(self._dev)
        except Exception:
            pass
        self._cb = None


def open_hackrf_radio(lna_db, vga_db, amp):
    """Factory passed to HackrfSource; import-time cffi cost stays out of tests."""
    return LibHackrfRadio(lna_db, vga_db, amp)
