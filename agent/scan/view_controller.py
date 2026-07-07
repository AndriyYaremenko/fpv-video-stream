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

    def __init__(self, publisher, run_stream, max_s=600.0, reset=None, clock=None, stream=None):
        self._publisher = publisher
        self._run_stream = run_stream        # fn(freq_mhz, stop_event, max_s) -> error|None
        self._max_s = max_s
        self._reset = reset or (lambda: None)
        self._clock = clock or time.time
        self._stream = stream                # WHEP stream name, echoed in every state publish
        self._last = (False, None, None, None)   # (active, freq_mhz, until_ts, error)
        self._lock = threading.Lock()
        self._pending = None
        self._stop = threading.Event()

    def set_command(self, data):
        action = data.get("view")
        if action == "stop":
            self._stop.set()
            return
        if action != "start":
            LOG.warning("view: ignoring unknown action %r", action)
            return
        freq = data.get("freq_mhz")
        if not isinstance(freq, (int, float)) or not (FREQ_MIN_MHZ <= float(freq) <= FREQ_MAX_MHZ):
            LOG.warning("view: ignoring start with bad freq_mhz %r", freq)
            return
        with self._lock:
            self._pending = float(freq)

    def pending(self):
        with self._lock:
            p, self._pending = self._pending, None
        return p

    def announce(self):
        """(Re)publish the last-known retained state — startup capability announce
        (also clears a stale retained active:true after a crash) and reconnect refresh."""
        active, freq_mhz, until_ts, error = self._last
        self._pub(int(self._clock()), active, freq_mhz, until_ts, error)

    def run_view(self, freq_mhz):
        self._stop.clear()                   # a stale idle-time stop must not kill this session
        ts = int(self._clock())
        self._pub(ts, True, freq_mhz, ts + int(self._max_s))
        error = None
        try:
            error = self._run_stream(freq_mhz, self._stop, self._max_s)
        except Exception as e:
            LOG.exception("view stream crashed")
            error = str(e)
        finally:
            self._pub(int(self._clock()), False, None, None, error)
            try:
                self._reset()                # leave the device clean for the next sweep
            except Exception:
                LOG.exception("view: device reset failed")
            self._stop.clear()
        return error

    def _pub(self, ts, active, freq_mhz, until_ts, error=None):
        self._last = (active, freq_mhz, until_ts, error)
        if self._publisher is None:
            return
        try:
            self._publisher.publish_view(ts, active, freq_mhz=freq_mhz,
                                         until_ts=until_ts, error=error,
                                         stream=self._stream)
        except Exception:
            LOG.exception("view state publish failed")
