import json
import types

import numpy as np
import pytest

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


def test_run_cycle_uses_bladerf_backend(tmp_path, monkeypatch):
    # Verify the acquisition seam routes through the bladeRF backend when cfg.sdr=="bladerf".
    # A fake backend returns a crafted wide bump (same shape as the HackRF sweep fixture) so the
    # detector finds one candidate — no hardware, no dependence on DSP window geometry.
    cfg = Config()
    cfg.source = "live"
    cfg.sdr = "bladerf"
    cfg.state_path = str(tmp_path / "scan.json")
    cfg.bands = {"5.8G": (5645.0, 5945.0)}

    from models import Spectrum

    class _FakeBackend:
        def __init__(self):
            self.swept = []
            self.dwelled = []

        def sweep_band(self, low_mhz, high_mhz, band):
            self.swept.append((low_mhz, high_mhz, band))
            freqs = np.arange(5645.0, 5945.0, 1.0)                              # 1 MHz bins
            power = np.where((freqs >= 5789) & (freqs <= 5811), -50.0, -90.0)   # 22 MHz bump @ 5800
            return Spectrum(band=band, freqs_mhz=freqs, power_dbm=power)

        def dwell(self, center_mhz, sample_rate_hz, num_samples):
            self.dwelled.append((center_mhz, sample_rate_hz, num_samples))
            t = np.arange(num_samples) / sample_rate_hz
            return np.exp(2j * np.pi * 1.0e6 * t).astype(np.complex64)

    be = _FakeBackend()
    monkeypatch.setattr(main, "_get_bladerf_backend", lambda c: be)

    payload = main.run_cycle(cfg, now_ts=1718530000, publisher=_FakePub())

    assert be.swept == [(5645.0, 5945.0, "5.8G")]                 # seam used the bladeRF backend's sweep
    assert len(be.dwelled) >= 1                                   # and its dwell for the candidate IQ
    assert payload["occupancy"]["5.8G"] > 0.0
    assert len(payload["detections"]) == 1
    assert abs(payload["detections"][0]["center_mhz"] - 5800.0) < 2.0


def test_main_exits_after_consecutive_bladerf_failures(monkeypatch):
    # After a USB reset (undervoltage) the bladeRF reopen keeps failing with NoDevError
    # INSIDE the same process, while a fresh process opens fine. So repeated cycle
    # failures in bladerf mode must exit the process and let systemd restart it clean.
    cfg = Config()
    cfg.source = "live"
    cfg.sdr = "bladerf"
    cfg.mqtt_enabled = False
    cfg.local_http_port = 0
    cfg.rx5808_enabled = False
    monkeypatch.setattr(main, "load_config", lambda: cfg)

    def _boom(*a, **k):
        raise RuntimeError("No devices available")
    monkeypatch.setattr(main, "run_cycle", _boom)

    resets = []
    monkeypatch.setattr(main, "_reset_bladerf_backend", lambda: resets.append(1))

    sleeps = [0]
    def _sleep(_s):
        sleeps[0] += 1
        if sleeps[0] > 50:
            raise AssertionError("main() kept looping; expected SystemExit after repeated bladeRF failures")
    monkeypatch.setattr(main, "time", types.SimpleNamespace(time=main.time.time, sleep=_sleep))

    with pytest.raises(SystemExit):
        main.main()

    assert len(resets) == main._BLADERF_FAIL_LIMIT      # backend reset attempted each failed cycle


def test_main_bladerf_failure_counter_resets_on_success(monkeypatch):
    # A successful cycle between failures must clear the counter — only CONSECUTIVE
    # failures escalate to a process exit.
    cfg = Config()
    cfg.source = "live"
    cfg.sdr = "bladerf"
    cfg.mqtt_enabled = False
    cfg.local_http_port = 0
    cfg.rx5808_enabled = False
    monkeypatch.setattr(main, "load_config", lambda: cfg)
    monkeypatch.setattr(main, "_reset_bladerf_backend", lambda: None)

    calls = [0]
    def _flaky(*a, **k):
        calls[0] += 1
        # fail, fail, succeed, repeatedly: never _BLADERF_FAIL_LIMIT in a row
        if calls[0] % 3 != 0:
            raise RuntimeError("No devices available")
        if calls[0] >= 12:
            raise KeyboardInterrupt()               # stop the test loop cleanly
        return {"ok": True}
    monkeypatch.setattr(main, "run_cycle", _flaky)
    monkeypatch.setattr(main, "time", types.SimpleNamespace(time=main.time.time, sleep=lambda s: None))

    with pytest.raises(KeyboardInterrupt):
        main.main()                                  # KeyboardInterrupt ≠ SystemExit: no escalation happened


def test_reset_bladerf_backend_invalidates_and_closes():
    closed = []
    class _FakeDev:
        def close(self):
            closed.append(True)
    main._BLADERF_DEVICE = _FakeDev()
    main._BLADERF_BACKEND = object()
    main._reset_bladerf_backend()
    assert main._BLADERF_BACKEND is None
    assert main._BLADERF_DEVICE is None
    assert closed == [True]


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


def test_run_cycle_aborts_immediately_when_abort_is_true(tmp_path):
    _write_fixtures(tmp_path)
    cfg = _config(tmp_path)
    pub = _FakePub()
    payload = main.run_cycle(cfg, now_ts=1718530000, publisher=pub, abort=lambda: True)
    assert payload is None
    assert pub.spectra == [] and pub.detections == []


def test_run_cycle_aborts_between_dwells_after_band_sweep(tmp_path):
    # First abort check (top of the band loop) passes; the pending view arrives
    # "mid-band": the band's spectrum is already published, but the cycle returns
    # None and never publishes detections.
    _write_fixtures(tmp_path)
    cfg = _config(tmp_path)
    pub = _FakePub()
    answers = iter([False])
    payload = main.run_cycle(cfg, now_ts=1718530000, publisher=pub,
                             abort=lambda: next(answers, True))
    assert payload is None
    assert len(pub.spectra) == 1          # band already swept/published before the abort
    assert pub.detections == []           # aggregate publish skipped


def test_run_cycle_aborts_before_loose_carrier_demods(tmp_path):
    _write_narrow_fixtures(tmp_path)
    cfg = _config(tmp_path)
    pub = _FakePub()
    em = _FakeEmitter()
    answers = iter([False])                        # band-loop check passes, then abort
    payload = main.run_cycle(cfg, now_ts=1718530000, publisher=pub, emitter=em,
                             abort=lambda: next(answers, True))
    assert payload is None
    assert em.calls == []                           # no extra dwell ran after the abort
    assert pub.detections == []


def test_main_skips_holder_update_when_cycle_aborts(monkeypatch):
    cfg = Config()
    cfg.source = "replay"
    cfg.mqtt_enabled = False
    cfg.local_http_port = 0
    cfg.rx5808_enabled = False
    monkeypatch.setattr(main, "load_config", lambda: cfg)

    holders = []
    class _H:
        def __init__(self):
            self.payload = "sentinel"
            holders.append(self)
    monkeypatch.setattr(main, "Holder", _H)

    calls = [0]
    def _cycle(*a, **k):
        calls[0] += 1
        if calls[0] == 1:
            return None                   # aborted cycle
        raise KeyboardInterrupt()         # stop the loop on the 2nd iteration
    monkeypatch.setattr(main, "run_cycle", _cycle)
    monkeypatch.setattr(main.time, "sleep", lambda s: None)

    with pytest.raises(KeyboardInterrupt):
        main.main()
    assert holders[0].payload == "sentinel"    # None never overwrote the holder


def test_main_enters_pending_view_without_the_idle_sleep(monkeypatch):
    # A view command that lands in the cycle's tail (after the last abort checkpoint)
    # must be entered right after the completed cycle — without the 1 s idle sleep.
    cfg = Config()
    cfg.source = "live"
    cfg.sdr = "hackrf"
    cfg.mqtt_enabled = True
    cfg.local_http_port = 0
    cfg.rx5808_enabled = False
    monkeypatch.setattr(main, "load_config", lambda: cfg)

    class _FakePublisher:
        def __init__(self, *a, **k):
            self.on_command = None
            self.on_view_command = None
            self.on_connected = None
        def connect(self, ts): pass
        def publish_view(self, *a, **k): pass
    monkeypatch.setattr(main, "MqttPublisher", _FakePublisher)

    monkeypatch.setenv("VIEW_ENABLED", "1")
    monkeypatch.setenv("VIEW_PUSH_URL", "rtsp://u:p@h:8554/hackrf-view")
    monkeypatch.setenv("FPV_VIDEO_ENABLED", "0")     # no emitter

    class _FakeView:
        def __init__(self, *a, **k):
            self.pending_flag = False
            self.entered = []
        def set_command(self, d): pass
        def announce(self): pass
        def has_pending(self): return self.pending_flag
        def pending(self):
            if self.pending_flag:
                self.pending_flag = False
                return (5865.0, None)
            return None
        def run_view(self, req): self.entered.append(req)
    import view_controller as vc_mod
    fakes = []
    def _mk(*a, **k):
        fakes.append(_FakeView())
        return fakes[-1]
    monkeypatch.setattr(vc_mod, "ViewController", _mk)

    calls = [0]
    def _cycle(*a, **k):
        calls[0] += 1
        if calls[0] == 1:
            fakes[0].pending_flag = True      # command arrives in the cycle's tail
            return {"ok": True}
        raise KeyboardInterrupt()             # end the test loop
    monkeypatch.setattr(main, "run_cycle", _cycle)

    sleeps = []
    monkeypatch.setattr(main, "time", types.SimpleNamespace(time=main.time.time, sleep=lambda s: sleeps.append(s)))

    with pytest.raises(KeyboardInterrupt):
        main.main()

    assert fakes and fakes[0].entered == [(5865.0, None)]   # the pending view WAS entered
    assert sleeps == []                             # ...without the 1 s idle sleep first


def test_main_viewer_only_skips_sweep(monkeypatch):
    # SCAN_ENABLED off: run_cycle is never called; the loop idles awaiting view commands.
    cfg = Config()
    cfg.source = "live"
    cfg.sdr = "hackrf"
    cfg.scan_enabled = False
    cfg.mqtt_enabled = False
    cfg.local_http_port = 0
    cfg.rx5808_enabled = False
    monkeypatch.setattr(main, "load_config", lambda: cfg)
    monkeypatch.setenv("FPV_VIDEO_ENABLED", "0")     # deterministic: skip emitter init

    cycles = [0]
    def _cycle(*a, **k):
        cycles[0] += 1
        raise AssertionError("run_cycle must not run when scan_enabled is False")
    monkeypatch.setattr(main, "run_cycle", _cycle)

    sleeps = []
    def _sleep(s):
        sleeps.append(s)
        raise KeyboardInterrupt()           # end the loop at the idle sleep
    monkeypatch.setattr(main, "time", types.SimpleNamespace(time=main.time.time, sleep=_sleep))

    with pytest.raises(KeyboardInterrupt):
        main.main()
    assert cycles[0] == 0                    # sweep gated off
    assert sleeps == [0.2]                   # hit the viewer-only idle sleep


def test_main_wires_bladerf_view_source(monkeypatch):
    # sdr=bladerf + view enabled must construct BladerfViewSource (not the hackrf_transfer path).
    cfg = Config()
    cfg.source = "live"
    cfg.sdr = "bladerf"
    cfg.mqtt_enabled = True
    cfg.local_http_port = 0
    cfg.rx5808_enabled = False
    monkeypatch.setattr(main, "load_config", lambda: cfg)

    class _FakePublisher:
        def __init__(self, *a, **k):
            self.on_command = None
            self.on_view_command = None
            self.on_connected = None
        def connect(self, ts): pass
        def publish_view(self, *a, **k): pass
    monkeypatch.setattr(main, "MqttPublisher", _FakePublisher)

    monkeypatch.setenv("VIEW_ENABLED", "1")
    monkeypatch.setenv("VIEW_PUSH_URL", "rtsp://u:p@h:8554/bladerf-view")
    monkeypatch.setenv("FPV_VIDEO_ENABLED", "0")         # no emitter

    import view_encoder
    class _FakeEnc:
        def __init__(self, *a, **k): pass
        def start(self): pass
        def idle(self): pass
    monkeypatch.setattr(view_encoder, "ViewEncoder", _FakeEnc)

    import bladerf_source
    made = []
    class _SpySource:
        bytes_per_sample = 4
        def __init__(self, *a, **k): made.append((a, k))
        def close(self): pass
    monkeypatch.setattr(bladerf_source, "BladerfViewSource", _SpySource)
    monkeypatch.setattr(bladerf_source, "open_bladerf_view_radio", lambda *a, **k: None)

    def _cycle(*a, **k):
        raise KeyboardInterrupt()            # end the loop right after wiring/setup
    monkeypatch.setattr(main, "run_cycle", _cycle)
    monkeypatch.setattr(main, "time", types.SimpleNamespace(time=main.time.time, sleep=lambda s: None))

    with pytest.raises(KeyboardInterrupt):
        main.main()
    assert len(made) == 1                    # bladeRF view source WAS wired


def test_main_serves_pending_view_even_when_scan_disabled(monkeypatch):
    # The SCAN_ENABLED gate sits AFTER pending-view entry: a pure viewer
    # (scan_enabled=False) must STILL enter a pending view, not idle past it.
    cfg = Config()
    cfg.source = "live"
    cfg.sdr = "hackrf"
    cfg.scan_enabled = False
    cfg.mqtt_enabled = True
    cfg.local_http_port = 0
    cfg.rx5808_enabled = False
    monkeypatch.setattr(main, "load_config", lambda: cfg)

    class _FakePublisher:
        def __init__(self, *a, **k):
            self.on_command = None
            self.on_view_command = None
            self.on_connected = None
        def connect(self, ts): pass
        def publish_view(self, *a, **k): pass
    monkeypatch.setattr(main, "MqttPublisher", _FakePublisher)

    monkeypatch.setenv("VIEW_ENABLED", "1")
    monkeypatch.setenv("VIEW_PUSH_URL", "rtsp://u:p@h:8554/hackrf-view")
    monkeypatch.setenv("FPV_VIDEO_ENABLED", "0")

    import view_encoder
    class _FakeEnc:
        def __init__(self, *a, **k): pass
        def start(self): pass
        def idle(self): pass
    monkeypatch.setattr(view_encoder, "ViewEncoder", _FakeEnc)

    cycles = [0]
    def _cycle(*a, **k):
        cycles[0] += 1
        raise AssertionError("run_cycle must not run when scan_enabled is False")
    monkeypatch.setattr(main, "run_cycle", _cycle)

    import view_controller as vc_mod
    fakes = []
    class _FakeView:
        def __init__(self, *a, **k):
            self.entered = []
            self._p = [(5865.0, None)]
            fakes.append(self)
        def set_command(self, d): pass
        def announce(self): pass
        def has_pending(self): return False
        def pending(self):
            return self._p.pop(0) if self._p else None
        def run_view(self, req):
            self.entered.append(req)
            raise KeyboardInterrupt()          # end the loop once the view is entered
    monkeypatch.setattr(vc_mod, "ViewController", _FakeView)

    # If the gate were WRONGLY placed before the view check, the loop would hit this
    # sleep first and raise here with entered == [] -> the asserts below fail (no hang).
    def _sleep(s):
        raise KeyboardInterrupt()
    monkeypatch.setattr(main, "time", types.SimpleNamespace(time=main.time.time, sleep=_sleep))

    with pytest.raises(KeyboardInterrupt):
        main.main()
    assert fakes and fakes[0].entered == [(5865.0, None)]   # pending view served despite scan off
    assert cycles[0] == 0                            # sweep never ran


def test_view_lpf_clamp():
    from main import _view_lpf
    assert _view_lpf(3, 8e6) == 3e6           # in-range: bw MHz -> Hz
    assert _view_lpf(10, 8e6) == 4e6          # above fs/2 -> clamped to fs/2
    assert _view_lpf(0.1, 8e6) == 0.5e6       # below 0.5 MHz -> floor
    assert _view_lpf(None, 8e6) is None       # None -> caller defaults
    assert _view_lpf("x", 8e6) is None        # non-number -> None
    assert _view_lpf(2, 6e6) == 2e6           # hackrf fs=6 MS/s, in-range
    assert _view_lpf(5, 6e6) == 3e6           # above fs/2 (3 MHz) -> 3e6
