# RX5808 Carrier Targeting — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Feed the RX5808 controller from `find_candidates` run with looser thresholds on the 5.8 spectrum, so any strong narrow FPV carrier (not just wide analog-classified video) makes the receiver lock onto its channel.

**Architecture:** Add two config thresholds; in `run_cycle`, compute 5.8 "carriers" with `find_candidates(spec, rx5808_carrier_snr_db, rx5808_carrier_min_bw_mhz)` and feed their centers to `controller.update_targets`, replacing the previous `signal_class=="analog"` filter. Main detection/classify/dashboard untouched.

**Tech Stack:** Python (numpy/pytest). Test interpreter `agent/scan/.venv/Scripts/python.exe`; run from `agent/scan`.

**Reference spec:** `docs/superpowers/specs/2026-06-18-rx5808-carrier-targeting-design.md`

---

### Task 1: Config — carrier thresholds

**Files:**
- Modify: `agent/scan/config.py`
- Modify: `agent/scan/tests/test_config.py`

- [ ] **Step 1: Write the failing test** — append to `agent/scan/tests/test_config.py`:

```python
def test_rx5808_carrier_thresholds_defaults_and_env():
    c = load_config({})
    assert c.rx5808_carrier_snr_db == 15.0
    assert c.rx5808_carrier_min_bw_mhz == 0.5
    c2 = load_config({"RX5808_CARRIER_SNR_DB": "18", "RX5808_CARRIER_MIN_BW_MHZ": "1.0"})
    assert c2.rx5808_carrier_snr_db == 18.0
    assert c2.rx5808_carrier_min_bw_mhz == 1.0
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd agent/scan && ./.venv/Scripts/python.exe -m pytest tests/test_config.py -q`
Expected: FAIL (no attribute 'rx5808_carrier_snr_db').

- [ ] **Step 3: Implement** — in `agent/scan/config.py`.

Add two fields to the `Config` dataclass (next to the other `rx5808_*` fields, before `thresholds`):

```python
    rx5808_carrier_snr_db: float = 15.0
    rx5808_carrier_min_bw_mhz: float = 0.5
```

Add to `load_config`, with the other `RX5808_*` reads (before `return c`):

```python
    if "RX5808_CARRIER_SNR_DB" in env:
        c.rx5808_carrier_snr_db = float(env["RX5808_CARRIER_SNR_DB"])
    if "RX5808_CARRIER_MIN_BW_MHZ" in env:
        c.rx5808_carrier_min_bw_mhz = float(env["RX5808_CARRIER_MIN_BW_MHZ"])
```

- [ ] **Step 4: Run it to confirm it passes**

Run: `cd agent/scan && ./.venv/Scripts/python.exe -m pytest tests/test_config.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/scan/config.py agent/scan/tests/test_config.py
git commit -m "feat(rx5808): config carrier-targeting thresholds (snr/min-bw)"
```

---

### Task 2: `run_cycle` — feed the controller from 5.8 carriers

**Files:**
- Modify: `agent/scan/main.py`
- Modify: `agent/scan/tests/test_run_cycle.py`

- [ ] **Step 1: Replace the two RX5808 run_cycle tests** in `agent/scan/tests/test_run_cycle.py`.

Find and DELETE the existing `test_run_cycle_feeds_rx5808_analog_58` and
`test_run_cycle_rx5808_empty_when_not_analog` functions, and add these in their place (the
`_FakeController` class defined just above them stays):

```python
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
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `cd agent/scan && ./.venv/Scripts/python.exe -m pytest tests/test_run_cycle.py -q`
Expected: FAIL — `test_run_cycle_feeds_rx5808_carriers_regardless_of_class` fails (today the feed
filters on `signal_class=="analog"`, so a digital class yields an empty target list).

- [ ] **Step 3: Implement** — in `agent/scan/main.py`, `run_cycle`.

After `spectrum_summary = {}` (just before the `for band, brange in cfg.bands.items():` loop), add:

```python
    rx_carrier_centers = []
```

Inside the band loop, right after the existing `cands.sort(key=lambda c: c.power_dbm, reverse=True)`
line, add the 5.8 carrier computation:

```python
        if band == "5.8G":
            rx_carrier_centers = [
                c.center_mhz for c in find_candidates(
                    spec, cfg.rx5808_carrier_snr_db, cfg.rx5808_carrier_min_bw_mhz)
            ]
```

Replace the existing controller feed block:

```python
    if controller is not None:
        try:
            controller.update_targets(
                [d.center_mhz for d in detections
                 if d.band == "5.8G" and d.signal_class == "analog"]
            )
        except Exception:
            LOG.exception("rx5808 update_targets failed")
```

with:

```python
    if controller is not None:
        try:
            controller.update_targets(rx_carrier_centers)
        except Exception:
            LOG.exception("rx5808 update_targets failed")
```

- [ ] **Step 4: Run them to confirm they pass**

Run: `cd agent/scan && ./.venv/Scripts/python.exe -m pytest tests/test_run_cycle.py -q`
Expected: PASS (both new tests; the fixture's strong 5.8 signal yields a carrier at ~5800 regardless of class).

- [ ] **Step 5: Full scan + video suite (no regressions)**

Run: `cd agent/scan && ./.venv/Scripts/python.exe -m pytest tests ../video/tests -q`
Expected: PASS (all green).

- [ ] **Step 6: Commit**

```bash
git add agent/scan/main.py agent/scan/tests/test_run_cycle.py
git commit -m "feat(rx5808): target any strong 5.8 carrier (decouple from analog class)"
```

---

## Self-Review notes (for the implementer)

- **Spec coverage:** Task 1 = config thresholds (§4.1); Task 2 = run_cycle carrier feed replacing the
  analog filter (§4.2) + the two decoupling tests (§5). Main detection/classify/dashboard untouched
  (no task changes them — correct).
- **Commit trailers:** append to every commit:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01Fr3LCjweDyLf1WRPz9PNUX`.
- `find_candidates` is already imported in `main.py` — no new import. The second call is cheap (mask +
  run-length over the in-memory 5.8 spectrum; no dwell/capture).
- **Live verification needs the HackRF working** (currently wedging — power fix pending, user's
  hardware task): with a 5.8 TX present, `fpv/hackrf/rxtune` flips to `mode=detected` on the TX channel.
```
