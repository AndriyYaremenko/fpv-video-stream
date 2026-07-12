import logging
import threading
import time

LOG = logging.getLogger("scan.view")

FREQ_MIN_MHZ = 100.0
FREQ_MAX_MHZ = 6000.0        # HackRF tuning range


def stream_name_from_push_url(url):
    """Last path segment of the RTSP push URL = MediaMTX path = WHEP stream name."""
    tail = (url or "").split("?")[0].rstrip("/").rsplit("/", 1)
    return tail[1] if len(tail) == 2 and tail[1] else None


class ViewController:
    """Manual SDR live-view mode. The MQTT thread calls set_command(); the scan
    loop polls pending() between cycles and calls run_view(), which blocks until
    the stop command, the max_s deadline, or a streamer error — then the sweep
    resumes. Never raises into callers."""

    def __init__(self, publisher, run_stream, max_s=600.0, reset=None, clock=None, stream=None, on_idle=None):
        self._publisher = publisher
        self._run_stream = run_stream        # fn(freq_mhz, bandwidth_mhz, stop_event, max_s) -> error|None
        self._max_s = max_s
        self._reset = reset or (lambda: None)
        self._on_idle = on_idle or (lambda: None)
        self._clock = clock or time.time
        self._stream = stream                # WHEP stream name, echoed in every state publish
        self._last = (False, None, None, None, None)   # (active, freq_mhz, until_ts, error, bandwidth_mhz)
        self._lock = threading.Lock()
        self._pending = None
        self._stop = threading.Event()

    def set_command(self, data):
        action = data.get("view")
        if action == "stop":
            with self._lock:
                self._pending = None     # stop also cancels a not-yet-consumed retune/start
            self._stop.set()
            return
        if action != "start":
            LOG.warning("view: ignoring unknown action %r", action)
            return
        freq = data.get("freq_mhz")
        if not isinstance(freq, (int, float)) or not (FREQ_MIN_MHZ <= float(freq) <= FREQ_MAX_MHZ):
            LOG.warning("view: ignoring start with bad freq_mhz %r", freq)
            return
        bw = data.get("bandwidth_mhz")
        bw = float(bw) if isinstance(bw, (int, float)) else None
        with self._lock:
            self._pending = (float(freq), bw)
        self._stop.set()                 # active session -> retune now; idle -> cleared on entry

    def pending(self):
        with self._lock:
            p, self._pending = self._pending, None
        return p

    def has_pending(self):
        """Non-consuming pending check — the sweep's abort hook."""
        with self._lock:
            return self._pending is not None

    def announce(self):
        """(Re)publish the last-known retained state — startup capability announce
        (also clears a stale retained active:true after a crash) and reconnect refresh."""
        active, freq_mhz, until_ts, error, bandwidth_mhz = self._last
        self._pub(int(self._clock()), active, freq_mhz, until_ts, error, bandwidth_mhz)

    def run_view(self, req):
        freq, bw = req
        error = None
        try:
            while True:
                self._stop.clear()       # a stale stop (or our own retune flag) must not kill this session
                ts = int(self._clock())
                self._pub(ts, True, freq, ts + int(self._max_s), bandwidth_mhz=bw)
                try:
                    error = self._run_stream(freq, bw, self._stop, self._max_s)
                except Exception as e:
                    LOG.exception("view stream crashed")
                    error = str(e)
                nxt = self.pending()
                if nxt is None:
                    break                # stop / timeout / unrecovered error -> back to sweep
                if error is not None:
                    try:
                        self._reset()    # leave the device clean before retrying at the new freq
                    except Exception:
                        LOG.exception("view: device reset failed")
                    error = None
                freq, bw = nxt
                LOG.info("view retune -> %.1f MHz (bw=%s)", freq, bw)
        finally:
            try:
                self._on_idle()          # persistent engine: blank the stream to black
            except Exception:
                LOG.exception("view: on_idle failed")
            self._pub(int(self._clock()), False, None, None, error, bandwidth_mhz=None)
            try:
                self._reset()            # leave the device clean for the next sweep
            except Exception:
                LOG.exception("view: device reset failed")
            self._stop.clear()
        return error

    def _pub(self, ts, active, freq_mhz, until_ts, error=None, bandwidth_mhz=None):
        self._last = (active, freq_mhz, until_ts, error, bandwidth_mhz)
        if self._publisher is None:
            return
        try:
            self._publisher.publish_view(ts, active, freq_mhz=freq_mhz,
                                         until_ts=until_ts, error=error,
                                         stream=self._stream, bandwidth_mhz=bandwidth_mhz)
        except Exception:
            LOG.exception("view state publish failed")
