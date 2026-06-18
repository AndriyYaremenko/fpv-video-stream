import logging
import threading
import time

from rx5808 import set_frequency, nearest_rx_channel

LOG = logging.getLogger("scan.rx5808")


class Rx5808Controller:
    """Hops the RX5808 across channels on its own timer. Detected analog-5.8 channels when
    `update_targets` was given any; otherwise all configured channels. Never raises into callers."""

    def __init__(self, backend, publisher, scanner_id, channels, dwell_s,
                 settle_ms=35, clock=None, sleep=None):
        self.backend = backend
        self.publisher = publisher
        self.scanner_id = scanner_id
        self._channels = list(channels)
        self.dwell_s = dwell_s
        self.settle_ms = settle_ms
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._targets = []                       # [(name, freq)]
        self._lock = threading.Lock()
        self._idx = -1
        self._stop = threading.Event()
        self._thread = None

    def update_targets(self, center_mhzs):
        chans = []
        seen = set()
        for c in center_mhzs:
            ch = nearest_rx_channel(c)
            if ch and ch[1] not in seen:
                seen.add(ch[1])
                chans.append(ch)
        with self._lock:
            self._targets = chans

    def _next(self):
        with self._lock:
            lst = self._targets or self._channels
            mode = "detected" if self._targets else "scan"
            self._idx = (self._idx + 1) % len(lst)
            name, freq = lst[self._idx]
            target_freqs = [f for _, f in self._targets]
        return name, freq, mode, target_freqs

    def tune(self, name, freq, mode, target_freqs, ts):
        try:
            set_frequency(self.backend, freq, self.settle_ms, sleep=self._sleep)
        except Exception:
            LOG.exception("rx5808 tune failed @ %s MHz", freq)
            return
        try:
            if self.publisher is not None:
                self.publisher.publish_rxtune(ts, freq, name, mode, target_freqs)
        except Exception:
            LOG.exception("rx5808 publish failed @ %s MHz", freq)

    def run_once(self):
        name, freq, mode, target_freqs = self._next()
        self.tune(name, freq, mode, target_freqs, ts=int(self._clock()))

    def run(self):
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                LOG.exception("rx5808 controller tick failed")
            self._sleep(self.dwell_s)

    def start(self):
        self._thread = threading.Thread(target=self.run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
