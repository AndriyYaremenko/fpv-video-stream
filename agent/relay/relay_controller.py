"""Auto-relay mode: sync-locked analog-video hits from the sweep -> RX(src)/TX(dst) passthrough.

Twin of agent/tx/tx_controller.py:TxController. The MQTT thread calls set_command() (arm/disarm);
the scan loop calls update_hits() at the end of every sweep cycle, polls pending() between cycles
and calls run_relay(), which frees the sweep's bladeRF (reset), opens full-duplex, loops until
disarm / a manual (view/TX) command / source lost / max_s / error, then hands the device back.
Manual commands always win: no start while one is pending, and an active session yields to one.
Never raises into callers."""
import logging
import math
import threading
import time

LOG = logging.getLogger("relay.controller")

FREQ_MIN_MHZ = 100.0
FREQ_MAX_MHZ = 6000.0            # bladeRF tuning range
REF_WINDOW_S = 1.0               # raw-RMS reference = median of the first second of a session
LEVEL_PUBLISH_S = 2.0            # rx_level_db refresh cadence in relaystate


class _LevelWatch:
    """Source-alive tracker on RAW input RMS: 'lost' once RMS sits lost_db below the session
    reference for lost_s continuously (recovers if it comes back before that). Reference RMS from
    the spike is ~460 for a real source (-13 dBFS), so min_rms defaults to a generous floor: if the
    session reference itself lands below min_rms, the source was already dead when we started
    listening (this session's "reference" is just noise floor) and we mark lost immediately."""

    def __init__(self, lost_db, lost_s, clock, min_rms):
        self._lost_db = lost_db
        self._lost_s = lost_s
        self._clock = clock
        self._min_rms = min_rms
        self._t0 = clock()
        self._ref_samples = []
        self.ref = None
        self._below_since = None
        self.lost = False

    def feed(self, rms):
        now = self._clock()
        if self.ref is None:
            self._ref_samples.append(float(rms))
            if now - self._t0 >= REF_WINDOW_S:
                s = sorted(self._ref_samples)
                self.ref = s[len(s) // 2] or 1.0
                if self.ref < self._min_rms:
                    self.lost = True
            return
        drop_db = 20.0 * math.log10(self.ref / max(float(rms), 1e-6))
        if drop_db >= self._lost_db:
            if self._below_since is None:
                self._below_since = now
            elif now - self._below_since >= self._lost_s:
                self.lost = True
        else:
            self._below_since = None

    @staticmethod
    def level_db(rms):
        return round(20.0 * math.log10(max(float(rms), 1e-6) / 2047.0), 1)


class RelayController:
    def __init__(self, cfg, publisher, open_fn, loop_fn, reset=None, manual_pending=None, clock=None):
        self._cfg = cfg
        self._publisher = publisher
        self._open_fn = open_fn               # bladerf_relay.open_bladerf_relay_radio(src_hz, dst_hz, fs, rxg, txg, block)
        self._loop_fn = loop_fn               # bladerf_relay.relay_loop(radio, block, stop_check, on_level, target)
        self._reset = reset or (lambda: None)
        self._manual_pending = manual_pending or (lambda: False)
        self._clock = clock or time.time
        self._lock = threading.Lock()
        self.armed = bool(cfg.armed)
        self.dst_mhz = float(cfg.dst_mhz)
        self._pending = None                  # chosen source dict for the next session
        self._stop = threading.Event()
        self._last = self._idle_state()       # for announce()

    # ---- command intake (MQTT thread) ----
    def set_command(self, data):
        r = data.get("relay") if isinstance(data, dict) else None
        if not isinstance(r, dict):
            LOG.warning("relay: ignoring malformed command %r", data)
            return
        action = r.get("action")
        if action == "arm":
            d = r.get("dst_mhz")
            if isinstance(d, (int, float)) and FREQ_MIN_MHZ <= float(d) <= FREQ_MAX_MHZ:
                self.dst_mhz = float(d)
            self.armed = True
            LOG.info("relay armed (dst=%.1f MHz)", self.dst_mhz)
            self._last = {**self._last, "error": None}     # clear a stale error from a prior session
            self._pub(self._last, self._now())      # re-publish with the new armed/dst
            return
        if action == "disarm":
            self.armed = False
            with self._lock:
                self._pending = None
            self._stop.set()                        # active session exits on its next block
            LOG.info("relay disarmed")
            self._pub(self._last, self._now())
            return
        LOG.warning("relay: ignoring unknown action %r", action)

    # ---- sweep feed (scan thread, end of cycle) ----
    def update_hits(self, hits):
        """hits: iterable of (band, center_mhz, sync_snr_db) for sync-locked analog video seen this
        cycle. Picks the strongest outside dst±guard; only when armed and nothing manual pends."""
        dst = self.dst_mhz                          # snapshot: an arm landing mid-loop must not
        best = None                                 # mix an old-dst guard check with a new-dst store
        if self.armed and not self._manual_pending():
            for band, center, snr in hits:
                try:
                    center = float(center)
                    snr = float(snr) if snr is not None else 0.0
                except (TypeError, ValueError):
                    continue
                if abs(center - dst) <= float(self._cfg.guard_mhz):
                    continue                        # TX would feed straight back into RX
                if best is None or snr > best[2]:
                    best = (band, center, snr)
        with self._lock:
            self._pending = None if best is None else {
                "band": best[0], "src_mhz": best[1], "dst_mhz": dst, "sync_snr_db": best[2]}

    # ---- arbitration hooks (scan loop) ----
    def pending(self):
        with self._lock:
            p, self._pending = self._pending, None
        return p

    def has_pending(self):
        with self._lock:
            return self._pending is not None

    # ---- session (scan loop, blocking) ----
    def run_relay(self, req):
        if not self.armed or self._manual_pending():
            return None                   # request went stale between pending() and here
        error = None
        radio = None
        c = self._cfg
        src = float(req["src_mhz"]); dst = float(req["dst_mhz"]); band = req.get("band")
        self._stop.clear()
        try:
            self._reset()                     # free the sweep's bladeRF before opening full-duplex
            radio = self._open_fn(int(src * 1e6), int(dst * 1e6), int(c.fs_hz),
                                  int(c.rx_gain_db), int(c.tx_gain_db), int(c.block_samples))
            since = self._now()
            deadline = float(since + int(c.max_s))
            st = {"active": True, "status": "relaying", "src_mhz": src, "band": band, "dst_mhz": dst,
                  "since_ts": since, "until_ts": int(deadline), "rx_level_db": None, "error": None}
            self._pub(st, since)
            watch = _LevelWatch(float(c.lost_db), float(c.lost_s), self._clock, float(c.min_rms))
            last_pub = [self._clock()]

            def on_level(rms):
                watch.feed(rms)
                now = self._clock()
                if now - last_pub[0] >= LEVEL_PUBLISH_S:
                    last_pub[0] = now
                    self._pub({**st, "rx_level_db": _LevelWatch.level_db(rms)}, int(now))

            def stop_check():
                return (self._stop.is_set() or not self.armed or self._manual_pending()
                        or watch.lost or self._clock() >= deadline)

            self._loop_fn(radio, int(c.block_samples), stop_check, on_level, float(c.agc_target))
            if watch.lost:
                if watch.ref is not None and watch.ref < float(c.min_rms):
                    LOG.info("relay: source %.1f MHz already gone at open (ref=%.1f < min_rms=%.1f)",
                             src, watch.ref, c.min_rms)
                else:
                    LOG.info("relay: source %.1f MHz lost (%.0f dB below reference for %.0fs)",
                             src, c.lost_db, c.lost_s)
        except Exception as e:
            LOG.exception("relay run_relay failed")
            error = str(e)
        finally:
            if radio is not None:
                try: radio.close()
                except Exception: LOG.exception("relay radio close failed")
            self._pub({**self._idle_state(), "error": error}, self._now())
            try: self._reset()                # leave the device clean for the next sweep
            except Exception: LOG.exception("relay: device reset failed")
            self._stop.clear()
        return error

    def announce(self):
        """(Re)publish last-known retained relaystate — capability announce on (re)connect;
        also clears a stale active:true after a crash."""
        self._pub(self._last, self._now())

    # ---- helpers ----
    def _now(self):
        return int(self._clock())

    def _idle_state(self):
        return {"active": False, "status": "idle", "src_mhz": None, "band": None,
                "dst_mhz": self.dst_mhz, "since_ts": None, "until_ts": None,
                "rx_level_db": None, "error": None}

    def _pub(self, state, ts):
        # armed/dst always reflect the live setting; an ACTIVE session keeps the dst it opened with
        state = {**state, "armed": self.armed,
                 "dst_mhz": state.get("dst_mhz") if state.get("active") else self.dst_mhz}
        self._last = state
        if self._publisher is None:
            return
        try:
            self._publisher.publish_relaystate(ts, state)
        except Exception:
            LOG.exception("relay state publish failed")
