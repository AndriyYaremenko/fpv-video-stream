# Dashboard-tunable detection thresholds (sensitivity) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the operator live-tune 5 detection thresholds from the «Детекції» screen; applied to the running scan, disk-persisted, and echoed back for the UI.

**Architecture:** A `ThresholdController` mutates the agent's `cfg` on a `{thresholds:{...}}` command (routed via the existing `fpv/<id>/rxcmd`, non-retained), clamps + persists to a JSON in the StateDirectory, and publishes the active thresholds on a new retained `fpv/<id>/scancfg`. The dashboard reduces `scancfg` into the store and renders a per-scanner threshold panel. No ACL change.

**Tech Stack:** Python 3 (agent), vanilla ES modules (dashboard), pytest + node:test, dev-preview.

## Global Constraints

- 5 thresholds (payload key → cfg attr, default, clamp):
  | key | cfg attr | default | clamp |
  |---|---|---|---|
  | `snr_threshold_db` | `cfg.thresholds.snr_threshold_db` | 20 | [3, 60] |
  | `min_bandwidth_mhz` | `cfg.thresholds.min_bandwidth_mhz` | 5 | [0.1, 30] |
  | `occupancy_snr_db` | `cfg.thresholds.occupancy_snr_db` | 10 | [3, 40] |
  | `carrier_snr_db` | `cfg.rx5808_carrier_snr_db` | 15 | [3, 60] |
  | `carrier_min_bw_mhz` | `cfg.rx5808_carrier_min_bw_mhz` | 0.5 | [0.1, 10] |
- Command: `fpv/<id>/rxcmd` `{thresholds: {<partial keys>}}` OR `{thresholds: "reset"}`, **qos 1, retain FALSE** (RX5808 owns the retained slot). NO new command topic (ACL: pub reads only `fpv/+/rxcmd`).
- Echo: `fpv/<id>/scancfg` **retained**, payload `{scanner_id, ts, snr_threshold_db, min_bandwidth_mhz, occupancy_snr_db, carrier_snr_db, carrier_min_bw_mhz}` (active values). New STATE topic; no ACL change (pub writes `fpv/#`, sub reads `fpv/#`).
- Persist: JSON at `cfg`-configured path (env `FPV_THRESHOLDS_PATH`, default `/var/lib/fpv/thresholds.json`); atomic write (tmp+rename); precedence env < file < live command. Failures are logged, never fatal.
- cfg mutation from the MQTT thread is read by the scan loop; independent floats, GIL-atomic — no lock.
- Reconcile note: `views/detections.js` is a NON-live route (renders on mount/refresh only), so its inputs are not tick-churned; the retained `scancfg` is delivered on subscribe (page load), so it is present by the time the operator opens the screen → prefill input VALUES from it.
- `npm test` + pytest per-package green each task.
- Commit footer:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01JLJdXSvLMBojY7w2dcUwuN
  ```
- Branch: `feat/detection-sensitivity-control` (spec committed there).

## File structure
- Create `agent/scan/threshold_controller.py` — apply/clamp/persist/load/announce.
- Modify `agent/scan/publisher.py` — `on_thresholds_command` hook, `_on_message` branch, `publish_scancfg`, `_t_scancfg`.
- Modify `agent/scan/config.py` — `thresholds_path` field + `FPV_THRESHOLDS_PATH` env.
- Modify `agent/scan/main.py` — boot overlay `load()`, instantiate controller, wire hook + announce on connect/start.
- Create `dashboard/public/thresholds.js` — pure UI helpers (field specs, `clampThreshold`, `scannerThresholdCards`).
- Modify `dashboard/public/mqtt-scan.js` — subscribe `scancfg`, reduce, `buildThresholdCommand`, `publishThresholds`.
- Modify `dashboard/public/views/detections.js` — per-scanner threshold panel.
- Modify `dashboard/public/app.js` — `onThresholds` handler.
- Modify `dashboard/public/fixtures.js`, `dashboard/public/styles.css`.
- Tests: `agent/scan/tests/test_threshold_controller.py` (new), `test_publisher.py`, `test_run_cycle.py`, `test/mqtt-scan.test.js`, `test/thresholds.test.js` (new).

---

### Task 1: `ThresholdController` (apply / clamp / persist / load / announce)

**Files:**
- Create: `agent/scan/threshold_controller.py`
- Test: `agent/scan/tests/test_threshold_controller.py`

**Interfaces:**
- Produces:
  - `ThresholdController(cfg, publisher, scanner_id, persist_path, clock=time.time)` with `.apply(data: dict)` and `.announce()`.
  - `apply(data)`: `data["thresholds"]` is a dict of `{key: value}` (partial OK) or the string `"reset"`. Clamps each known key, assigns into `cfg`, persists, announces. Unknown keys / non-numeric values ignored. Never raises.
  - `announce()`: `publisher.publish_scancfg(ts, active_dict)`; never raises.
  - `load_thresholds(path, cfg)` (module fn): overlay a saved JSON onto `cfg` (clamped); missing/corrupt file → no-op.
  - `active(cfg) -> dict` (module fn): the 5 active values read from `cfg`.

- [ ] **Step 1: Write the failing tests**

Create `agent/scan/tests/test_threshold_controller.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest agent/scan/tests/test_threshold_controller.py -v`
Expected: FAIL — `No module named 'threshold_controller'`.

- [ ] **Step 3: Implement**

Create `agent/scan/threshold_controller.py`:

```python
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
```

Note the `_defaults` snapshot: it is taken at construction. **main.py (Task 3, as shipped) constructs the controller BEFORE `load_thresholds` overlays the persisted file**, so `_defaults` = the factory/env config and a dashboard "Reset" restores factory sensitivity (NOT the last-saved values). Do NOT reorder `load_thresholds` before the controller construction — that would make Reset a no-op after a restart (fixed in commit `9162136`).

- [ ] **Step 4: Run tests**

Run: `python -m pytest agent/scan/tests/test_threshold_controller.py -v`
Expected: PASS (all 8).

- [ ] **Step 5: Commit**

```bash
git add agent/scan/threshold_controller.py agent/scan/tests/test_threshold_controller.py
git commit -m "feat(scan): ThresholdController — apply/clamp/persist/announce thresholds

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JLJdXSvLMBojY7w2dcUwuN"
```

---

### Task 2: `publisher.py` — route thresholds command + `publish_scancfg`

**Files:**
- Modify: `agent/scan/publisher.py`
- Test: `agent/scan/tests/test_publisher.py`

**Interfaces:**
- Consumes: `on_thresholds_command` hook (set by main.py → `tc.apply`).
- Produces: `_t_scancfg` topic; `_on_message` routes `{"thresholds":...}` to `on_thresholds_command`; `publish_scancfg(ts, thresholds)` retained.

- [ ] **Step 1: Write the failing tests**

Add to `agent/scan/tests/test_publisher.py` (use existing `FakeClient` + `_pub(fake)`):
```python
def test_on_message_routes_thresholds_command():
    fake = FakeClient()
    p = _pub(fake); p.connect(ts=1)
    seen = []
    p.on_thresholds_command = lambda d: seen.append(d)
    p.on_command = lambda m, c: seen.append(("rx", m, c))     # must NOT fire for thresholds
    import types
    msg = types.SimpleNamespace(payload=json.dumps({"thresholds": {"snr_threshold_db": 12}}).encode())
    p._on_message(fake, None, msg)
    assert seen == [{"thresholds": {"snr_threshold_db": 12}}]  # routed to thresholds, not rx

def test_publish_scancfg_topic_retained_payload():
    fake = FakeClient()
    p = _pub(fake); p.connect(ts=1)
    p.publish_scancfg(500, {"snr_threshold_db": 12.0, "min_bandwidth_mhz": 5.0,
                            "occupancy_snr_db": 10.0, "carrier_snr_db": 15.0, "carrier_min_bw_mhz": 0.5})
    msg = [m for m in fake.published if m[0] == "fpv/hackrf/scancfg"][-1]
    topic, payload, qos, retain = msg
    assert retain is True
    body = json.loads(payload)
    assert body["scanner_id"] == "hackrf" and body["ts"] == 500
    assert body["snr_threshold_db"] == 12.0 and body["carrier_snr_db"] == 15.0
```
(Check how `_on_message` is invoked in existing tests — if it takes different args, match them. The existing view-command routing has a similar test; mirror it.)

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest agent/scan/tests/test_publisher.py -k "thresholds or scancfg" -v`
Expected: FAIL — no `on_thresholds_command` / `publish_scancfg` / scancfg routing.

- [ ] **Step 3: Implement**

In `agent/scan/publisher.py`:
- In `__init__`, next to the other topics: `self._t_scancfg = f"fpv/{scanner_id}/scancfg"` and next to the other hooks: `self.on_thresholds_command = None`.
- In `_on_message`, add a branch BEFORE the RX5808 `on_command` dispatch and after the `view` branch:
  ```python
          if "thresholds" in data:        # sensitivity command — not routed to the RX5808 handler
              if self.on_thresholds_command is not None:
                  try:
                      self.on_thresholds_command(data)
                  except Exception:
                      LOG.exception("on_thresholds_command handler failed")
              return
  ```
- Add the publisher method (mirror `publish_rxtune`'s `self._publish(topic, {...}, self.QOS_DETECTION)` form):
  ```python
      def publish_scancfg(self, ts, thresholds):
          self._publish(
              self._t_scancfg,
              {"scanner_id": self.scanner_id, "ts": ts, **thresholds},
              self.QOS_DETECTION,
          )
  ```
  (`self._publish(..., retain=True)` is already how `_publish` publishes — confirm `_publish` always sets `retain=True`; it does per the existing code.)

- [ ] **Step 4: Run tests**

Run: `python -m pytest agent/scan/tests/test_publisher.py -v`
Expected: PASS (new + all existing).

- [ ] **Step 5: Commit**

```bash
git add agent/scan/publisher.py agent/scan/tests/test_publisher.py
git commit -m "feat(scan): publisher routes {thresholds} cmd + publish_scancfg (retained)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JLJdXSvLMBojY7w2dcUwuN"
```

---

### Task 3: `config.py` + `main.py` — persist path, boot overlay, wiring, announce

**Files:**
- Modify: `agent/scan/config.py` (add `thresholds_path` + `FPV_THRESHOLDS_PATH`)
- Modify: `agent/scan/main.py` (boot overlay, controller instance, wire hook, announce on connect + start)
- Test: `agent/scan/tests/test_config.py`, `agent/scan/tests/test_run_cycle.py`

**Interfaces:**
- Consumes: `ThresholdController`, `load_thresholds` (Task 1); `publisher.on_thresholds_command`, `publish_scancfg` (Task 2).

- [ ] **Step 1: Write the failing tests**

`test_config.py`:
```python
def test_thresholds_path_default_and_env():
    assert load_config({}).thresholds_path == "/var/lib/fpv/thresholds.json"
    assert load_config({"FPV_THRESHOLDS_PATH": "/tmp/t.json"}).thresholds_path == "/tmp/t.json"
```
`test_run_cycle.py` — a `main()`-wiring test that the threshold controller is constructed and wired (mirror the existing `test_main_*` monkeypatch pattern): monkeypatch `main.ThresholdController` to a spy, `load_config` to a cfg with `mqtt_enabled=True` + a `_FakePublisher` exposing `on_thresholds_command`/`publish_scancfg`, run `main()` (break via KeyboardInterrupt in `run_cycle`), assert the spy was constructed once and `publisher.on_thresholds_command` was set to its `apply`.

```python
def test_main_wires_threshold_controller(monkeypatch):
    cfg = Config(); cfg.source = "replay"; cfg.mqtt_enabled = True
    cfg.local_http_port = 0; cfg.rx5808_enabled = False
    monkeypatch.setattr(main, "load_config", lambda: cfg)

    class _FakePublisher:
        def __init__(self, *a, **k):
            self.on_command = None; self.on_view_command = None
            self.on_connected = None; self.on_thresholds_command = None
        def connect(self, ts): pass
        def publish_view(self, *a, **k): pass
        def publish_scancfg(self, *a, **k): pass
    monkeypatch.setattr(main, "MqttPublisher", _FakePublisher)
    monkeypatch.setenv("FPV_VIDEO_ENABLED", "0")

    made = []
    class _SpyTC:
        def __init__(self, *a, **k): made.append((a, k)); self.apply = lambda d: None
        def announce(self): pass
    monkeypatch.setattr(main, "ThresholdController", _SpyTC)
    monkeypatch.setattr(main, "load_thresholds", lambda p, c: None)

    def _cycle(*a, **k): raise KeyboardInterrupt()
    monkeypatch.setattr(main, "run_cycle", _cycle)
    monkeypatch.setattr(main, "time", types.SimpleNamespace(time=main.time.time, sleep=lambda s: None))

    with pytest.raises(KeyboardInterrupt):
        main.main()
    assert len(made) == 1                          # controller constructed
```
(Ensure `types`/`pytest` imported at top of test_run_cycle.py — they are.)

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest agent/scan/tests/test_config.py::test_thresholds_path_default_and_env agent/scan/tests/test_run_cycle.py::test_main_wires_threshold_controller -v`
Expected: FAIL (no `thresholds_path`; `main.ThresholdController` missing).

- [ ] **Step 3: Implement**

`config.py`:
- In the `Config` dataclass, add: `thresholds_path: str = "/var/lib/fpv/thresholds.json"`
- In `load_config`, add: `c.thresholds_path = env.get("FPV_THRESHOLDS_PATH", c.thresholds_path)`

`main.py`:
- Import near the top: `from threshold_controller import ThresholdController, load_thresholds`
- In `main()`, AFTER `cfg = load_config()` and BEFORE the publisher connects, overlay the persisted file:
  ```python
      load_thresholds(cfg.thresholds_path, cfg)     # operator's saved sensitivity survives restart
  ```
- Construct + wire the controller **AFTER the view-setup block** (which sets `publisher.on_connected = view.announce`) and near the existing `publisher.on_command = controller.set_command` wiring — so the `on_connected` composition below captures the view announce as `prev` and the view block cannot later overwrite it:
  ```python
      threshold_ctl = None
      if publisher is not None:
          threshold_ctl = ThresholdController(cfg, publisher, cfg.scanner_id, cfg.thresholds_path)
          publisher.on_thresholds_command = threshold_ctl.apply
  ```
- Ensure the active thresholds are announced on (re)connect. `publisher.on_connected` is already used (view announce). Wire the scancfg announce alongside it WITHOUT dropping the view announce — e.g. compose:
  ```python
      if threshold_ctl is not None:
          prev = publisher.on_connected
          def _on_connected():
              if prev is not None:
                  prev()
              threshold_ctl.announce()
          publisher.on_connected = _on_connected
  ```
  (If `on_connected` is unset when there is no view, this still works — `prev` is None.)
- Also announce once right after a successful `publisher.connect(...)` (covers the initial retained publish even if `on_connected` timing differs): `if threshold_ctl is not None: threshold_ctl.announce()` after the connect success log.

- [ ] **Step 4: Run tests**

Run: `python -m pytest agent/scan/tests/test_config.py agent/scan/tests/test_run_cycle.py -v`, then `python -m pytest agent/scan/tests -q`.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/scan/config.py agent/scan/main.py agent/scan/tests/test_config.py agent/scan/tests/test_run_cycle.py
git commit -m "feat(scan): boot-overlay persisted thresholds + wire ThresholdController + announce

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JLJdXSvLMBojY7w2dcUwuN"
```

---

### Task 4: Dashboard data layer — `thresholds.js` + `mqtt-scan.js`

**Files:**
- Create: `dashboard/public/thresholds.js`
- Modify: `dashboard/public/mqtt-scan.js`
- Test: `test/thresholds.test.js` (new), `test/mqtt-scan.test.js`

**Interfaces:**
- Produces: `THRESHOLD_FIELDS` (specs), `clampThreshold(key, v)`, `scannerThresholdCards(store)` (in `thresholds.js`); `buildThresholdCommand(obj)`, `publishThresholds(id, obj)`, `reduce` scancfg branch (in `mqtt-scan.js`).

- [ ] **Step 1: Write the failing tests**

`test/thresholds.test.js` (new):
```javascript
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { THRESHOLD_FIELDS, clampThreshold, scannerThresholdCards } from '../dashboard/public/thresholds.js';

test('THRESHOLD_FIELDS has the five keys with ranges + labels', () => {
  const keys = THRESHOLD_FIELDS.map((f) => f.key);
  assert.deepEqual(keys, ['snr_threshold_db', 'min_bandwidth_mhz', 'occupancy_snr_db', 'carrier_snr_db', 'carrier_min_bw_mhz']);
  const snr = THRESHOLD_FIELDS.find((f) => f.key === 'snr_threshold_db');
  assert.equal(snr.lo, 3); assert.equal(snr.hi, 60); assert.ok(snr.label);
});

test('clampThreshold clamps to the field range; non-number -> null', () => {
  assert.equal(clampThreshold('snr_threshold_db', 999), 60);
  assert.equal(clampThreshold('min_bandwidth_mhz', -1), 0.1);
  assert.equal(clampThreshold('snr_threshold_db', 'x'), null);
  assert.equal(clampThreshold('bogus', 5), null);
});

test('scannerThresholdCards lists online scanners with their scancfg', () => {
  const store = {
    bladerf: { online: true, view: {}, scancfg: { snr_threshold_db: 12 } },
    hackrf: { online: false, scancfg: { snr_threshold_db: 20 } },
    cam: { online: true },  // no scancfg -> still listed? no: only scanners with scancfg OR online scanner
  };
  const cards = scannerThresholdCards(store);
  assert.deepEqual(cards.map((c) => c.id), ['bladerf']);   // online + has scancfg
  assert.equal(cards[0].scancfg.snr_threshold_db, 12);
});
```

Add to `test/mqtt-scan.test.js`:
```javascript
test('buildThresholdCommand carries valid numeric fields, "reset" passthrough', () => {
  assert.deepEqual(buildThresholdCommand({ snr_threshold_db: 12, min_bandwidth_mhz: '' }),
    { thresholds: { snr_threshold_db: 12 } });      // empty omitted
  assert.deepEqual(buildThresholdCommand('reset'), { thresholds: 'reset' });
});

test('reduce scancfg populates store[id].scancfg', () => {
  const store = {};
  reduce(store, 'fpv/bladerf/scancfg', JSON.stringify({ ts: 1, snr_threshold_db: 12, min_bandwidth_mhz: 5,
    occupancy_snr_db: 10, carrier_snr_db: 15, carrier_min_bw_mhz: 0.5 }));
  assert.equal(store.bladerf.scancfg.snr_threshold_db, 12);
  assert.equal(store.bladerf.scancfg.carrier_min_bw_mhz, 0.5);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `node --test test/thresholds.test.js test/mqtt-scan.test.js`
Expected: FAIL (module + functions missing).

- [ ] **Step 3: Implement**

Create `dashboard/public/thresholds.js`:
```javascript
// dashboard/public/thresholds.js — pure helpers for the detection-sensitivity panel.
export const THRESHOLD_FIELDS = [
  { key: 'snr_threshold_db',   label: 'SNR',        lo: 3,   hi: 60, step: 1 },
  { key: 'min_bandwidth_mhz',  label: 'мін BW',     lo: 0.1, hi: 30, step: 0.5 },
  { key: 'occupancy_snr_db',   label: 'occ SNR',    lo: 3,   hi: 40, step: 1 },
  { key: 'carrier_snr_db',     label: 'carrier SNR', lo: 3,  hi: 60, step: 1 },
  { key: 'carrier_min_bw_mhz', label: 'carrier BW', lo: 0.1, hi: 10, step: 0.5 },
];
const _BY = Object.fromEntries(THRESHOLD_FIELDS.map((f) => [f.key, f]));

export function clampThreshold(key, v) {
  const f = _BY[key];
  const n = Number(v);
  if (!f || !Number.isFinite(n)) return null;
  return Math.max(f.lo, Math.min(n, f.hi));
}

// Online scanners that have announced a scancfg — one threshold card each, sorted by id.
export function scannerThresholdCards(store) {
  return Object.keys(store || {})
    .filter((id) => store[id] && store[id].online && store[id].scancfg)
    .sort()
    .map((id) => ({ id, scancfg: store[id].scancfg }));
}
```

In `mqtt-scan.js`:
- Add `scancfg` to the topic regex in `reduce` and to the subscribe list (line ~113: append `'fpv/+/scancfg'`). Regex: add `|scancfg` to the alternation.
- Add the reduce branch:
  ```javascript
    } else if (kind === 'scancfg') {
      s.scancfg = {
        ts: data.ts || 0,
        snr_threshold_db: data.snr_threshold_db == null ? null : Number(data.snr_threshold_db),
        min_bandwidth_mhz: data.min_bandwidth_mhz == null ? null : Number(data.min_bandwidth_mhz),
        occupancy_snr_db: data.occupancy_snr_db == null ? null : Number(data.occupancy_snr_db),
        carrier_snr_db: data.carrier_snr_db == null ? null : Number(data.carrier_snr_db),
        carrier_min_bw_mhz: data.carrier_min_bw_mhz == null ? null : Number(data.carrier_min_bw_mhz),
      };
  ```
- Add `scancfg: null` to the `ensure()` initial store shape.
- Add the command builder + publisher near `buildViewCommand`/`publishView`:
  ```javascript
  export function buildThresholdCommand(obj) {
    if (obj === 'reset') return { thresholds: 'reset' };
    const th = {};
    for (const [k, v] of Object.entries(obj || {})) {
      if (v !== '' && v != null && Number.isFinite(Number(v))) th[k] = Number(v);
    }
    return { thresholds: th };
  }
  ```
  ```javascript
    // Detection-sensitivity command — same rxcmd topic, NOT retained (RX5808 owns the retained slot).
    publishThresholds(id, obj) {
      if (!this.client || !id) return;
      this.client.publish(`fpv/${id}/rxcmd`, JSON.stringify(buildThresholdCommand(obj)),
        { qos: 1, retain: false });
    }
  ```

- [ ] **Step 4: Run tests + syntax**

Run: `node --check dashboard/public/thresholds.js && node --check dashboard/public/mqtt-scan.js && node --test test/thresholds.test.js test/mqtt-scan.test.js && npm test`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add dashboard/public/thresholds.js dashboard/public/mqtt-scan.js test/thresholds.test.js test/mqtt-scan.test.js
git commit -m "feat(scan-ui): threshold helpers + scancfg reduce + publishThresholds

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JLJdXSvLMBojY7w2dcUwuN"
```

---

### Task 5: «Детекції» panel + app.js wiring + fixtures + CSS + visual gate

**Files:**
- Modify: `dashboard/public/views/detections.js` (per-scanner threshold panel above the history table)
- Modify: `dashboard/public/app.js` (`onThresholds` handler)
- Modify: `dashboard/public/fixtures.js` (scancfg on a scanner), `dashboard/public/styles.css`

**Interfaces:**
- Consumes: `scannerThresholdCards`, `THRESHOLD_FIELDS` (Task 4); `ctx.handlers.onThresholds(id, obj)`.

- [ ] **Step 1: Implement the panel**

In `dashboard/public/app.js`, add to `ctx.handlers` (near `viewerRowClick`):
```javascript
  onThresholds: (id, obj) => { if (!PREVIEW) scanClient.publishThresholds(id, obj); },
```

In `dashboard/public/views/detections.js`:
- Import: `import { scannerThresholdCards, THRESHOLD_FIELDS } from '/thresholds.js';`
- In `render(container, ctx)`, BEFORE the history head/table, build a threshold panel per online scanner-with-scancfg. Since this screen is non-live (renders on mount/refresh only), a plain build is safe (inputs won't be tick-churned). Build a `.thresholds-panel` per card, each field an `<input type="number">` prefilled with the scancfg value, plus «Застосувати» + «Скинути» buttons. Wire clicks to `ctx.handlers.onThresholds`:
  ```javascript
  const cards = scannerThresholdCards(ctx.scanStore());
  if (cards.length) {
    const wrap = el('div', 'thresholds-wrap');
    for (const c of cards) {
      const panel = el('div', 'thresholds-panel');
      const inputs = THRESHOLD_FIELDS.map((f) => {
        const cur = c.scancfg[f.key];
        return `<label class="th-field"><span>${escapeHtml(f.label)}</span>`
          + `<input class="th-in" data-key="${f.key}" type="number" min="${f.lo}" max="${f.hi}" step="${f.step}"`
          + ` value="${cur == null ? '' : cur}" /></label>`;
      }).join('');
      panel.innerHTML = `<div class="th-head mono">${escapeHtml(c.id)} · чутливість</div>`
        + `<div class="th-fields">${inputs}</div>`
        + `<div class="th-actions"><button type="button" class="btn th-apply">Застосувати</button>`
        + `<button type="button" class="btn th-reset">Скинути</button></div>`;
      panel.querySelector('.th-apply').addEventListener('click', () => {
        const obj = {};
        panel.querySelectorAll('.th-in').forEach((i) => { obj[i.dataset.key] = i.value; });
        ctx.handlers.onThresholds(c.id, obj);
      });
      panel.querySelector('.th-reset').addEventListener('click', () => ctx.handlers.onThresholds(c.id, 'reset'));
      wrap.appendChild(panel);
    }
    container.appendChild(wrap);
  }
  ```
  Place this block right after `container.innerHTML='';` and before the history `head` is appended.

- [ ] **Step 2: Fixtures + CSS**

`fixtures.js`: add a `scancfg` to the `bladerf` scanStore entry (so the preview panel renders):
```javascript
    scancfg: { ts: NOW, snr_threshold_db: 20, min_bandwidth_mhz: 5, occupancy_snr_db: 10, carrier_snr_db: 15, carrier_min_bw_mhz: 0.5 },
```
`styles.css`: append minimal styling:
```css
.thresholds-wrap { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 14px; }
.thresholds-panel { border: 1px solid var(--line); border-radius: 6px; padding: 8px 10px; }
.thresholds-panel .th-head { font-size: 12px; margin-bottom: 6px; color: var(--muted); }
.thresholds-panel .th-fields { display: flex; flex-wrap: wrap; gap: 8px; }
.thresholds-panel .th-field { display: flex; flex-direction: column; font-size: 11px; }
.thresholds-panel .th-in { width: 74px; }
.thresholds-panel .th-actions { margin-top: 8px; display: flex; gap: 8px; }
```

- [ ] **Step 3: Syntax + regression**

Run: `node --check dashboard/public/views/detections.js && node --check dashboard/public/app.js && node --check dashboard/public/fixtures.js && npm test && python -m pytest agent/scan/tests agent/video/tests -q`
Expected: all green.

- [ ] **Step 4: Visual gate (dev-preview) — controller-run**

Fresh dev-serve on a new port; load `?preview=1#/detections`. Verify:
1. A «bladerf · чутливість» panel renders above the history table with 5 number inputs prefilled (SNR 20, мін BW 5, occ SNR 10, carrier SNR 15, carrier BW 0.5) + «Застосувати»/«Скинути».
2. Editing an input then a rerender (navigate away/back or `__rerender` — note: detections is non-live, so `__rerender` on a different screen won't wipe it; verify the panel is present and inputs editable).
3. No console errors.
Record results.

- [ ] **Step 5: Commit**

```bash
git add dashboard/public/views/detections.js dashboard/public/app.js dashboard/public/fixtures.js dashboard/public/styles.css
git commit -m "feat(scan-ui): «Детекції» per-scanner sensitivity panel + apply/reset

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JLJdXSvLMBojY7w2dcUwuN"
```

---

## Deploy (after merge)
- Pi (`rpi-4` @192.168.1.204, key fpv_deploy): `cd /opt/fpv-video-stream && sudo git pull && sudo systemctl restart fpv-scan fpv-scan-hackrf`. (thresholds.json created on first Apply, under StateDirectory `/var/lib/fpv`.)
- Server `traefik`: `git pull` + `sudo docker compose build dashboard && sudo docker compose up -d --no-deps dashboard` (wg-easy/mediamtx/mosquitto untouched). NO ACL change.
- Over-WG acceptance: on «Детекції», lower `carrier_snr_db`/`snr_threshold_db` → more detections/video-demods; Reset restores; restart the Pi → the tuning persists (from thresholds.json).

## Self-review (spec coverage)
- ✅ 5 thresholds, clamp ranges (Task 1 `_FIELDS`).
- ✅ Command via rxcmd `{thresholds}` non-retained; routed not to RX5808 (Task 2); publishThresholds retain:false (Task 4).
- ✅ Echo retained `scancfg` (Task 2 publish + Task 3 announce on connect/start; Task 4 reduce).
- ✅ Live-apply mutates cfg (Task 1); disk persist + boot overlay (Task 1 + Task 3).
- ✅ Per-scanner «Детекції» panel, prefilled from scancfg (Task 5); non-live screen → no tick-churn.
- ✅ Reset to startup defaults (Task 1).
- ✅ No ACL change (rxcmd cmd + scancfg under fpv/# read).
- ✅ pytest + npm green each task; visual gate (Task 5).
