import json

import numpy as np

from config import Config
import main


def _write_fixtures(tmp_path):
    # Sweep CSV: flat -90 dB with a 22 MHz bump at -50 dB around 5800 MHz, 1 MHz bins.
    lo = 5645_000000
    bins = []
    for i in range(300):                       # 5645..5945 MHz, 1 MHz bins
        f_mhz = 5645 + i
        bins.append(-50.0 if 5789 <= f_mhz <= 5811 else -90.0)
    row = ["2024-01-01", "12:00:00.0", str(lo), str(lo + 300_000000), "1000000.0", "20"]
    row += [str(x) for x in bins]
    (tmp_path / "sweep_5.8G.csv").write_text(", ".join(row) + "\n", encoding="utf-8")

    # IQ blob: a strong tone (int8) so the dwell has real samples to analyze.
    fs = 20_000_000.0
    n = 40_000
    t = np.arange(n) / fs
    tone = np.exp(2j * np.pi * 1.0e6 * t)
    iq8 = np.empty(2 * n, dtype=np.int8)
    iq8[0::2] = np.clip(np.real(tone) * 100, -127, 127).astype(np.int8)
    iq8[1::2] = np.clip(np.imag(tone) * 100, -127, 127).astype(np.int8)
    (tmp_path / "iq_5.8G.bin").write_bytes(iq8.tobytes())


def _config(tmp_path):
    c = Config()
    c.source = "replay"
    c.fixtures_dir = str(tmp_path)
    c.state_path = str(tmp_path / "scan.json")
    c.bands = {"5.8G": (5645.0, 5945.0)}        # single band for the test
    return c


class _FakePub:
    def __init__(self):
        self.spectra = []        # (ts, band, low, high, psd)
        self.detections = []     # (ts, detections, occupancy)

    def publish_spectrum(self, ts, band_id, low_mhz, high_mhz, psd):
        self.spectra.append((ts, band_id, low_mhz, high_mhz, psd))

    def publish_detection(self, ts, detections, occupancy):
        self.detections.append((ts, detections, occupancy))


def test_run_cycle_end_to_end(tmp_path):
    _write_fixtures(tmp_path)
    cfg = _config(tmp_path)
    pub = _FakePub()

    payload = main.run_cycle(cfg, now_ts=1718530000, publisher=pub)

    assert payload["scanner_id"] == "scan-01"
    assert len(payload["detections"]) == 1
    det = payload["detections"][0]
    assert det["band"] == "5.8G"
    assert abs(det["center_mhz"] - 5800.0) < 2.0
    assert det["class"] in {"analog", "digital", "unknown"}
    assert payload["occupancy"]["5.8G"] > 0.0

    saved = json.loads((tmp_path / "scan.json").read_text(encoding="utf-8"))
    assert saved == payload

    # one self-describing spectrum frame per band, published with the band's range
    assert len(pub.spectra) == len(cfg.bands)
    ts, band, low, high, psd = pub.spectra[0]
    assert band == "5.8G" and low == 5645.0 and high == 5945.0
    assert len(psd) == 128                 # MQTT frame is 128-pt (state file stays 64) — lock the split
    # exactly one detection publish per cycle, carrying the occupancy map
    assert len(pub.detections) == 1
    assert pub.detections[0][2]["5.8G"] > 0.0


def test_run_cycle_without_publisher_still_writes_state(tmp_path):
    # The broker-down fallback: main() passes publisher=None; the cycle must still
    # detect, write the state file, and return the payload (no publish, no crash).
    _write_fixtures(tmp_path)
    cfg = _config(tmp_path)

    payload = main.run_cycle(cfg, now_ts=1718530000)     # publisher defaults to None

    assert len(payload["detections"]) == 1
    saved = json.loads((tmp_path / "scan.json").read_text(encoding="utf-8"))
    assert saved == payload


class _FakeEmitter:
    def __init__(self):
        self.calls = []      # (fs, center_mhz, now_ts)
        self.last_frame_path = None

    def maybe_emit(self, iq, fs, center_mhz, now_ts):
        self.calls.append((fs, center_mhz, now_ts))
        self.last_frame_path = "/frames/%d_%d.png" % (now_ts, round(center_mhz))
        return "published"


def test_run_cycle_emits_video_for_analog_only(tmp_path, monkeypatch):
    _write_fixtures(tmp_path)
    cfg = _config(tmp_path)
    em = _FakeEmitter()
    monkeypatch.setattr(main, "classify", lambda feat, thr: ("analog", 0.9))

    main.run_cycle(cfg, now_ts=1718530000, publisher=_FakePub(), emitter=em)

    assert len(em.calls) == 1                     # one analog candidate in the fixture
    fs, center_mhz, now_ts = em.calls[0]
    assert fs == cfg.dwell_sample_rate_hz
    assert abs(center_mhz - 5800.0) < 2.0
    assert now_ts == 1718530000


def test_run_cycle_skips_video_for_non_analog(tmp_path, monkeypatch):
    _write_fixtures(tmp_path)
    cfg = _config(tmp_path)
    em = _FakeEmitter()
    monkeypatch.setattr(main, "classify", lambda feat, thr: ("digital", 0.7))

    main.run_cycle(cfg, now_ts=1718530000, publisher=_FakePub(), emitter=em)

    assert em.calls == []                         # non-analog -> no video emit


class _FakeController:
    def __init__(self):
        self.targets = None

    def update_targets(self, freqs):
        self.targets = list(freqs)


def test_run_cycle_feeds_rx5808_carriers(tmp_path):
    _write_fixtures(tmp_path)
    cfg = _config(tmp_path)
    ctl = _FakeController()

    main.run_cycle(cfg, now_ts=1718530000, publisher=_FakePub(), controller=ctl)

    assert ctl.targets is not None and len(ctl.targets) == 1
    assert abs(ctl.targets[0] - 5800.0) < 2.0      # carrier center of the fixture's 5.8 signal


def test_run_cycle_feeds_rx5808_carriers_regardless_of_class(tmp_path, monkeypatch):
    # The RX5808 feed comes from the carrier finder, NOT classify: a non-analog class
    # must still target the carrier (the receiver demodulates whatever is there).
    _write_fixtures(tmp_path)
    cfg = _config(tmp_path)
    ctl = _FakeController()
    monkeypatch.setattr(main, "classify", lambda f, t: ("digital", 0.7))

    main.run_cycle(cfg, now_ts=1718530000, publisher=_FakePub(), controller=ctl)

    assert len(ctl.targets) == 1
    assert abs(ctl.targets[0] - 5800.0) < 2.0


def _write_narrow_fixtures(tmp_path):
    # A narrow (~3 MHz) carrier @5865: passes the looser carrier finder but fails
    # the strict analog-video bandwidth gate (min 5 MHz).
    lo = 5645_000000
    bins = []
    for i in range(300):                        # 5645..5945 MHz, 1 MHz bins
        f_mhz = 5645 + i
        bins.append(-50.0 if 5864 <= f_mhz <= 5866 else -90.0)   # 3 MHz spike @ 5865
    row = ["2024-01-01", "12:00:00.0", str(lo), str(lo + 300_000000), "1000000.0", "20"]
    row += [str(x) for x in bins]
    (tmp_path / "sweep_5.8G.csv").write_text(", ".join(row) + "\n", encoding="utf-8")
    # replay dwell ignores center; any IQ blob is fine
    fs = 20_000_000.0
    n = 40_000
    t = np.arange(n) / fs
    tone = np.exp(2j * np.pi * 1.0e6 * t)
    iq8 = np.empty(2 * n, dtype=np.int8)
    iq8[0::2] = np.clip(np.real(tone) * 100, -127, 127).astype(np.int8)
    iq8[1::2] = np.clip(np.imag(tone) * 100, -127, 127).astype(np.int8)
    (tmp_path / "iq_5.8G.bin").write_bytes(iq8.tobytes())


class _NotVideoEmitter(_FakeEmitter):
    def maybe_emit(self, iq, fs, center_mhz, now_ts):
        self.calls.append((fs, center_mhz, now_ts))
        return "not_video"


def test_run_cycle_demods_narrow_5_8_carrier_missed_by_strict_detector(tmp_path):
    # The demod attempt happens on the loose carrier, but when the line-sync gate
    # says "not video" NO detection is fabricated (that's the noise filter).
    _write_narrow_fixtures(tmp_path)
    cfg = _config(tmp_path)
    em = _NotVideoEmitter()

    payload = main.run_cycle(cfg, now_ts=1718530000, publisher=_FakePub(), emitter=em)

    assert not any(d["class"] == "analog" and abs(d["center_mhz"] - 5865) <= 2
                   for d in payload["detections"])
    # ...but the emitter still got a demod attempt on the loose 5.8 carrier near 5865
    assert len(em.calls) >= 1
    assert any(abs(center - 5865) <= 2 for _, center, _ in em.calls)


def test_run_cycle_adds_detection_for_demod_confirmed_carrier(tmp_path):
    # A line-sync-locked demod IS an analog-video detection: narrow carriers the
    # strict detector misses must reach detections[] (→ MQTT → journal) once the
    # emitter confirms real video ("published").
    _write_narrow_fixtures(tmp_path)
    cfg = _config(tmp_path)
    em = _FakeEmitter()                            # always returns "published"
    pub = _FakePub()

    payload = main.run_cycle(cfg, now_ts=1718530000, publisher=pub, emitter=em)

    dets = [d for d in payload["detections"]
            if d["class"] == "analog" and abs(d["center_mhz"] - 5865) <= 2]
    assert len(dets) == 1
    d = dets[0]
    assert d["snr_db"] >= 15                       # metrics carried from the loose candidate
    assert d["bandwidth_mhz"] > 0
    assert d["channel"]                            # 5865 sits on an RX5808 channel
    # and the published MQTT payload carries it too (this is what feeds the journal)
    assert any(abs(pd.center_mhz - 5865) <= 2 and pd.signal_class == "analog"
               for pd in pub.detections[0][1])


def test_run_cycle_logs_each_detection_with_frame_link(tmp_path, monkeypatch, caplog):
    import logging
    _write_fixtures(tmp_path)                       # 22 MHz bump @5800 -> one candidate
    cfg = _config(tmp_path)
    em = _FakeEmitter()
    monkeypatch.setattr(main, "classify", lambda feat, thr: ("analog", 0.9))

    with caplog.at_level(logging.INFO):
        main.run_cycle(cfg, now_ts=1718530000, publisher=_FakePub(), emitter=em)

    det_lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("detection ")]
    assert len(det_lines) == 1                       # one log line per detection
    line = det_lines[0]
    assert "band=5.8G" in line and "class=analog" in line
    assert "frame=/frames/" in line and "frame=-" not in line   # frame linked to the analog detection


def test_run_cycle_logs_non_analog_detection_without_frame(tmp_path, monkeypatch, caplog):
    import logging
    _write_fixtures(tmp_path)
    cfg = _config(tmp_path)
    em = _FakeEmitter()
    monkeypatch.setattr(main, "classify", lambda feat, thr: ("digital", 0.7))

    with caplog.at_level(logging.INFO):
        main.run_cycle(cfg, now_ts=1718530000, publisher=_FakePub(), emitter=em)

    det_lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("detection ")]
    assert len(det_lines) == 1
    assert "class=digital" in det_lines[0] and "frame=-" in det_lines[0]   # no frame for non-analog


def test_run_cycle_demods_carrier_in_non_5_8_band(tmp_path):
    # The video demod runs in EVERY band, not just 5.8: a narrow carrier in a 3.3 GHz band
    # gets a demod attempt, and it is NOT sent to the RX5808 (a 5.8-only receiver).
    lo = 3400_000000
    bins = []
    for i in range(200):                        # 3400..3600 MHz, 1 MHz bins
        f_mhz = 3400 + i
        bins.append(-50.0 if 3469 <= f_mhz <= 3471 else -90.0)   # 3 MHz spike @ 3470
    row = ["2024-01-01", "12:00:00.0", str(lo), str(lo + 200_000000), "1000000.0", "20"]
    row += [str(x) for x in bins]
    (tmp_path / "sweep_3.3G.csv").write_text(", ".join(row) + "\n", encoding="utf-8")
    fs = 20_000_000.0
    n = 40_000
    t = np.arange(n) / fs
    tone = np.exp(2j * np.pi * 1.0e6 * t)
    iq8 = np.empty(2 * n, dtype=np.int8)
    iq8[0::2] = np.clip(np.real(tone) * 100, -127, 127).astype(np.int8)
    iq8[1::2] = np.clip(np.imag(tone) * 100, -127, 127).astype(np.int8)
    (tmp_path / "iq_3.3G.bin").write_bytes(iq8.tobytes())

    cfg = Config()
    cfg.source = "replay"
    cfg.fixtures_dir = str(tmp_path)
    cfg.state_path = str(tmp_path / "scan.json")
    cfg.bands = {"3.3G": (3400.0, 3600.0)}
    em = _FakeEmitter()
    ctl = _FakeController()

    main.run_cycle(cfg, now_ts=1718530000, publisher=_FakePub(), emitter=em, controller=ctl)

    # video demod attempted on the 3.47 GHz carrier
    assert any(abs(center - 3470) <= 2 for _, center, _ in em.calls)
    # ...but it is NOT fed to the RX5808 (out of its 5.8 GHz range)
    assert ctl.targets == []
