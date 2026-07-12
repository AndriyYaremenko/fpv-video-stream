import json
from config import Config
from threshold_controller import ThresholdController, load_thresholds, active


class _Pub:
    def __init__(self):
        self.scancfg = []
    def publish_scancfg(self, ts, thresholds):
        self.scancfg.append((ts, dict(thresholds)))


def _tc(tmp_path, cfg=None):
    cfg = cfg or Config()
    pub = _Pub()
    tc = ThresholdController(cfg, pub, "bladerf", str(tmp_path / "th.json"), clock=lambda: 1000)
    return cfg, pub, tc


def test_active_reads_the_five_fields():
    cfg = Config()
    a = active(cfg)
    assert a == {"snr_threshold_db": 20.0, "min_bandwidth_mhz": 5.0, "occupancy_snr_db": 10.0,
                 "carrier_snr_db": 15.0, "carrier_min_bw_mhz": 0.5}


def test_apply_partial_clamps_and_mutates_cfg(tmp_path):
    cfg, pub, tc = _tc(tmp_path)
    tc.apply({"thresholds": {"snr_threshold_db": 12, "carrier_snr_db": 8}})
    assert cfg.thresholds.snr_threshold_db == 12.0
    assert cfg.rx5808_carrier_snr_db == 8.0
    assert cfg.thresholds.min_bandwidth_mhz == 5.0            # untouched
    assert pub.scancfg[-1][1]["snr_threshold_db"] == 12.0     # announced


def test_apply_clamps_out_of_range(tmp_path):
    cfg, pub, tc = _tc(tmp_path)
    tc.apply({"thresholds": {"snr_threshold_db": 999, "min_bandwidth_mhz": -5,
                             "occupancy_snr_db": 1, "carrier_min_bw_mhz": 50}})
    assert cfg.thresholds.snr_threshold_db == 60.0           # hi clamp
    assert cfg.thresholds.min_bandwidth_mhz == 0.1           # lo clamp
    assert cfg.thresholds.occupancy_snr_db == 3.0            # lo clamp
    assert cfg.rx5808_carrier_min_bw_mhz == 10.0             # hi clamp


def test_apply_ignores_unknown_and_nonnumeric(tmp_path):
    cfg, pub, tc = _tc(tmp_path)
    tc.apply({"thresholds": {"bogus": 5, "snr_threshold_db": "x", "min_bandwidth_mhz": 7}})
    assert cfg.thresholds.min_bandwidth_mhz == 7.0
    assert cfg.thresholds.snr_threshold_db == 20.0          # unchanged (non-numeric ignored)
    assert not hasattr(cfg, "bogus")


def test_reset_restores_startup_defaults(tmp_path):
    cfg, pub, tc = _tc(tmp_path)
    tc.apply({"thresholds": {"snr_threshold_db": 5}})
    assert cfg.thresholds.snr_threshold_db == 5.0
    tc.apply({"thresholds": "reset"})
    assert cfg.thresholds.snr_threshold_db == 20.0
    assert active(cfg)["carrier_snr_db"] == 15.0


def test_persist_and_load_roundtrip(tmp_path):
    cfg, pub, tc = _tc(tmp_path)
    tc.apply({"thresholds": {"snr_threshold_db": 11, "carrier_min_bw_mhz": 1.5}})
    saved = json.loads((tmp_path / "th.json").read_text())
    assert saved["snr_threshold_db"] == 11.0 and saved["carrier_min_bw_mhz"] == 1.5
    cfg2 = Config()
    load_thresholds(str(tmp_path / "th.json"), cfg2)
    assert cfg2.thresholds.snr_threshold_db == 11.0
    assert cfg2.rx5808_carrier_min_bw_mhz == 1.5


def test_load_missing_or_corrupt_is_noop(tmp_path):
    cfg = Config()
    load_thresholds(str(tmp_path / "nope.json"), cfg)          # missing
    assert cfg.thresholds.snr_threshold_db == 20.0
    (tmp_path / "bad.json").write_text("{not json")
    load_thresholds(str(tmp_path / "bad.json"), cfg)           # corrupt
    assert cfg.thresholds.snr_threshold_db == 20.0


def test_announce_publishes_active(tmp_path):
    cfg, pub, tc = _tc(tmp_path)
    tc.announce()
    ts, d = pub.scancfg[-1]
    assert ts == 1000 and d == active(cfg)
