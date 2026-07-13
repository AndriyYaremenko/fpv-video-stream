"""Dashboard-controlled TX generator mode. Twin of agent/scan/view_controller.py:ViewController.

The MQTT thread calls set_command(); the scan loop polls pending() between cycles and calls
run_tx(), which frees the sweep's bladeRF (reset), renders the clip once (cached by baked-param
key), opens the TX radio, and loop-transmits until stop / the tx_max_s deadline — then the sweep
resumes. Frequency/gain retune live without re-render. Never raises into callers."""
import logging
import os
import threading
import time

LOG = logging.getLogger("tx.controller")

FREQ_MIN_MHZ = 100.0
FREQ_MAX_MHZ = 6000.0            # bladeRF tuning range


class TxController:
    def __init__(self, cfg, publisher, render_fn, open_tx_fn, transmit_fn,
                 reset=None, clock=None, exists_fn=None):
        self._cfg = cfg
        self._publisher = publisher
        self._render_fn = render_fn          # tx_render.render(path, out_bin, **kw)
        self._open_tx_fn = open_tx_fn         # bladerf_tx.open_bladerf_tx_radio(freq_hz, fs_hz, gain, bw_hz)
        self._transmit_fn = transmit_fn       # bladerf_tx.transmit_loop(radio, path, block_bytes, stop_check)
        self._reset = reset or (lambda: None)
        self._clock = clock or time.time
        self._exists = exists_fn or os.path.exists
        self._lock = threading.Lock()
        self._pending = None                  # a validated start request dict
        self._retune = None                   # a live {freq_mhz?, gain_db?} to apply mid-session
        self._stop = threading.Event()
        self._last_render_key = None
        self._last = self._idle_state()       # for announce()

    # ---- command intake (MQTT thread) ----
    def set_command(self, data):
        tx = data.get("tx") if isinstance(data, dict) else None
        if not isinstance(tx, dict):
            LOG.warning("tx: ignoring malformed command %r", data)
            return
        action = tx.get("action")
        if action == "stop":
            with self._lock:
                self._pending = None
                self._retune = None
            self._stop.set()
            return
        if action == "retune":
            r = {}
            f = tx.get("freq_mhz")
            if isinstance(f, (int, float)) and FREQ_MIN_MHZ <= float(f) <= FREQ_MAX_MHZ:
                r["freq_mhz"] = float(f)
            g = tx.get("gain_db")
            if isinstance(g, (int, float)):
                r["gain_db"] = int(g)
            if r:
                with self._lock:
                    self._retune = r
                self._stop.set()              # break transmit_loop to apply; active-only (ignored if idle)
            return
        if action != "start":
            LOG.warning("tx: ignoring unknown action %r", action)
            return
        req = self._build_start(tx)
        if req is None:
            return
        with self._lock:
            self._pending = req
        self._stop.set()

    def _build_start(self, tx):
        file = tx.get("file")
        if not isinstance(file, str) or not file:
            LOG.warning("tx: start missing file"); return None
        path = os.path.join(self._cfg.tx_dir, file)
        if not self._exists(path):
            LOG.warning("tx: start file not found %r", path); return None
        f = tx.get("freq_mhz")
        if not isinstance(f, (int, float)) or not (FREQ_MIN_MHZ <= float(f) <= FREQ_MAX_MHZ):
            LOG.warning("tx: start bad freq_mhz %r", f); return None
        c = self._cfg
        g = tx.get("gain_db"); gain = int(g) if isinstance(g, (int, float)) else c.gain_db
        dv = tx.get("deviation_mhz")
        dev_hz = float(dv) * 1e6 if isinstance(dv, (int, float)) and dv > 0 else c.deviation_hz
        std = tx.get("standard"); standard = std.strip().upper() if isinstance(std, str) and std else c.standard
        sc = tx.get("secs"); secs = float(sc) if isinstance(sc, (int, float)) and sc > 0 else c.secs
        return {"file": file, "file_path": path, "freq_mhz": float(f), "gain_db": gain,
                "deviation_hz": dev_hz, "standard": standard, "secs": secs,
                "fs_hz": c.fs_hz, "width": c.width, "height": c.height, "fps": c.fps,
                "vbi_lines": c.vbi_lines}

    # ---- arbitration hooks (scan loop) ----
    def pending(self):
        with self._lock:
            p, self._pending = self._pending, None
        return p

    def has_pending(self):
        with self._lock:
            return self._pending is not None

    def _consume_retune(self):
        with self._lock:
            r, self._retune = self._retune, None
        return r

    # ---- session (scan loop, blocking) ----
    def run_tx(self, req):
        error = None
        radio = None
        freq = req["freq_mhz"]; gain = req["gain_db"]
        try:
            self._reset()                     # free the sweep's bladeRF before opening TX
            key = self._render_key(req)
            if key != self._last_render_key:
                self._pub(self._now(), True, "rendering", req, freq, gain, until_ts=None)
                self._render_fn(req["file_path"], self._cfg.tx_cache_bin,
                                standard=req["standard"], fs=req["fs_hz"],
                                deviation_hz=req["deviation_hz"], width=req["width"],
                                height=req["height"], fps=req["fps"], max_secs=req["secs"],
                                vbi_lines=req["vbi_lines"])
                self._last_render_key = key
            radio = self._open_tx_fn(int(freq * 1e6), int(req["fs_hz"]), int(gain), int(req["fs_hz"]))
            while True:
                self._stop.clear()
                since = self._now()
                until = since + int(self._cfg.tx_max_s)
                self._pub(since, True, "transmitting", req, freq, gain, until_ts=until, since_ts=since)
                deadline = float(until)
                stop_check = lambda: self._stop.is_set() or self._clock() >= deadline
                try:
                    self._transmit_fn(radio, self._cfg.tx_cache_bin, 32768 * 4, stop_check)
                except Exception as e:
                    LOG.exception("tx transmit crashed"); error = str(e); break
                r = self._consume_retune()
                if r is None:
                    break                     # stop / deadline -> back to sweep
                if "freq_mhz" in r:
                    freq = r["freq_mhz"]
                    try: radio.set_frequency(int(freq * 1e6))
                    except Exception: LOG.exception("tx retune set_frequency failed")
                if "gain_db" in r:
                    gain = r["gain_db"]
                    try: radio.set_gain(int(gain))
                    except Exception: LOG.exception("tx retune set_gain failed")
                LOG.info("tx retune -> %.1f MHz gain=%s", freq, gain)
        except Exception as e:
            LOG.exception("tx run_tx failed")
            error = str(e)
        finally:
            if radio is not None:
                try: radio.close()
                except Exception: LOG.exception("tx radio close failed")
            self._pub(self._now(), False, "idle", req, None, None, until_ts=None, error=error)
            try: self._reset()                # leave the device clean for the next sweep
            except Exception: LOG.exception("tx: device reset failed")
            self._stop.clear()
        return error

    def announce(self):
        """(Re)publish last-known retained txstate — capability announce on (re)connect;
        also clears a stale active:true after a crash."""
        self._publish(self._last)

    # ---- helpers ----
    def _render_key(self, req):
        return (req["file"], req["standard"], req["fs_hz"], req["deviation_hz"],
                req["width"], req["height"], req["fps"], req["secs"], req["vbi_lines"])

    def _now(self):
        return int(self._clock())

    def _idle_state(self):
        return {"active": False, "status": "idle", "file": None, "freq_mhz": None,
                "gain_db": None, "deviation_mhz": None, "standard": None,
                "since_ts": None, "until_ts": None, "error": None}

    def _pub(self, ts, active, status, req, freq, gain, until_ts=None, since_ts=None, error=None):
        state = {"active": bool(active), "status": status,
                 "file": req.get("file"), "freq_mhz": freq, "gain_db": gain,
                 "deviation_mhz": (req.get("deviation_hz") / 1e6) if req.get("deviation_hz") else None,
                 "standard": req.get("standard"), "since_ts": since_ts, "until_ts": until_ts,
                 "error": error}
        self._last = state
        self._publish(state, ts)

    def _publish(self, state, ts=None):
        if self._publisher is None:
            return
        try:
            self._publisher.publish_txstate(ts if ts is not None else self._now(), state)
        except Exception:
            LOG.exception("tx state publish failed")
