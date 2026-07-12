import json
import logging
import os
import time

LOG = logging.getLogger("scan.thresholds")

# payload key -> (cfg-object selector, attr name, lo, hi)
# selector(cfg) returns the object holding the attr (cfg.thresholds or cfg itself).
_FIELDS = {
    "snr_threshold_db":   (lambda c: c.thresholds, "snr_threshold_db",   3.0, 60.0),
    "min_bandwidth_mhz":  (lambda c: c.thresholds, "min_bandwidth_mhz",  0.1, 30.0),
    "occupancy_snr_db":   (lambda c: c.thresholds, "occupancy_snr_db",   3.0, 40.0),
    "carrier_snr_db":     (lambda c: c,            "rx5808_carrier_snr_db",     3.0, 60.0),
    "carrier_min_bw_mhz": (lambda c: c,            "rx5808_carrier_min_bw_mhz", 0.1, 10.0),
}


def active(cfg) -> dict:
    """The 5 active threshold values read from cfg, keyed by payload key."""
    out = {}
    for key, (sel, attr, _lo, _hi) in _FIELDS.items():
        out[key] = float(getattr(sel(cfg), attr))
    return out


def _set(cfg, key, value):
    """Clamp `value` into the field's range and assign it into cfg. Returns True if applied."""
    spec = _FIELDS.get(key)
    if spec is None:
        return False
    sel, attr, lo, hi = spec
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    v = max(lo, min(v, hi))
    setattr(sel(cfg), attr, v)
    return True


def load_thresholds(path, cfg) -> None:
    """Overlay a saved thresholds JSON onto cfg (clamped). Missing/corrupt -> no-op."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return
    except Exception:
        LOG.exception("thresholds load failed; using defaults")
        return
    if isinstance(data, dict):
        for key, value in data.items():
            _set(cfg, key, value)


class ThresholdController:
    """Applies dashboard threshold commands to the live cfg, persists to disk, and announces
    the active thresholds on fpv/<id>/scancfg. cfg is mutated in place; the scan loop reads it
    each cycle. Never raises into the MQTT callback."""

    def __init__(self, cfg, publisher, scanner_id, persist_path, clock=time.time):
        self._cfg = cfg
        self._pub = publisher
        self._id = scanner_id
        self._path = persist_path
        self._clock = clock
        self._defaults = active(cfg)          # snapshot for reset (post-env, pre-file overlay caller's choice)

    def apply(self, data):
        try:
            th = data.get("thresholds")
            if th == "reset":
                for key, value in self._defaults.items():
                    _set(self._cfg, key, value)
            elif isinstance(th, dict):
                for key, value in th.items():
                    _set(self._cfg, key, value)
            else:
                return
            self._persist()
            self.announce()
        except Exception:
            LOG.exception("threshold apply failed")

    def _persist(self):
        try:
            tmp = self._path + ".tmp"
            os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(active(self._cfg), f)
            os.replace(tmp, self._path)
        except Exception:
            LOG.exception("thresholds persist failed")

    def announce(self):
        if self._pub is None:
            return
        try:
            self._pub.publish_scancfg(int(self._clock()), active(self._cfg))
        except Exception:
            LOG.exception("scancfg announce failed")
