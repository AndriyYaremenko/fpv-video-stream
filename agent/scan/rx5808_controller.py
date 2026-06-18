import logging
import os
import random
import threading
import time

from rx5808 import set_frequency, nearest_rx_channel

LOG = logging.getLogger("scan.rx5808")


class Rx5808Controller:
    """Hops the RX5808 across channels on its own timer. Detected analog-5.8 channels when
    `update_targets` was given any; otherwise all configured channels. Never raises into callers."""

    def __init__(self, backend, publisher, scanner_id, channels, dwell_s,
                 settle_ms=35, clock=None, sleep=None, rng=None, osd_file=""):
        self.backend = backend
        self.publisher = publisher
        self.scanner_id = scanner_id
        self._channels = list(channels)
        self.dwell_s = dwell_s
        self.settle_ms = settle_ms
        # Source of the published rxtune timestamp — wall-clock epoch (matches every other
        # topic's `ts`). Injectable for deterministic tests. NOT the dwell timer (that is _sleep).
        self._clock = clock or time.time
        self._sleep = sleep or time.sleep
        self._rng = rng or random.Random()
        self._osd_file = osd_file                # current channel written here for ffmpeg drawtext
        self._targets = []                       # [(name, freq)] — auto-mode detected carriers
        self._mode = "auto"                      # auto | scan | random | manual
        self._manual = None                      # (name, freq) for manual mode
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

    def set_command(self, mode, channel=None):
        """Apply a dashboard command. Unknown mode/channel is ignored (prior state kept)."""
        if mode not in ("auto", "scan", "random", "manual"):
            LOG.warning("rx5808 ignoring unknown mode %r", mode)
            return
        resolved = None
        if mode == "manual":
            resolved = next(((n, f) for n, f in self._channels if n == channel), None)
            if resolved is None:
                LOG.warning("rx5808 manual: unknown channel %r, keeping previous", channel)
        with self._lock:
            self._mode = mode
            if resolved is not None:
                self._manual = resolved
        LOG.info("rx5808 command applied: mode=%s channel=%s", mode, channel)

    def _next(self):
        with self._lock:
            mode = self._mode
            if mode == "manual":
                pick = self._manual or (self._channels[0] if self._channels else None)
                if pick is None:
                    return None, None, mode, []
                return pick[0], pick[1], mode, []
            if mode == "random":
                if not self._channels:
                    return None, None, mode, []
                name, freq = self._rng.choice(self._channels)
                return name, freq, mode, []
            if mode == "scan":
                if not self._channels:
                    return None, None, mode, []
                self._idx = (self._idx + 1) % len(self._channels)
                name, freq = self._channels[self._idx]
                return name, freq, mode, []
            # auto: detected carriers if any, else all channels
            lst = self._targets or self._channels
            if not lst:
                return None, None, "auto", []
            self._idx = (self._idx + 1) % len(lst)
            name, freq = lst[self._idx]
            return name, freq, "auto", [f for _, f in self._targets]

    def _write_osd(self, text):
        if not self._osd_file:
            return
        try:
            d = os.path.dirname(self._osd_file)
            if d:
                os.makedirs(d, exist_ok=True)
            tmp = self._osd_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, self._osd_file)      # atomic: drawtext never reads a partial line
        except Exception:
            LOG.exception("rx5808 OSD write failed")

    def tune(self, name, freq, mode, target_freqs, ts):
        try:
            set_frequency(self.backend, freq, self.settle_ms, sleep=self._sleep)
        except Exception:
            LOG.exception("rx5808 tune failed @ %s MHz", freq)
            return
        self._write_osd(f"{name} · {int(round(freq))} · {mode}")
        try:
            if self.publisher is not None:
                self.publisher.publish_rxtune(ts, freq, name, mode, target_freqs)
        except Exception:
            LOG.exception("rx5808 publish failed @ %s MHz", freq)

    def run_once(self):
        name, freq, mode, target_freqs = self._next()
        if freq is None:                          # no channels configured — nothing to tune
            return
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
