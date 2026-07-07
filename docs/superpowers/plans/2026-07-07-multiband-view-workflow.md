# Multiband «scan→click→watch» Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One dashboard panel («FPV Viewer») that merges FPV detections from all bands/scanners into a clickable list with an embedded live SDR-demod player, plus agent-side retune-in-place and fast view entry.

**Architecture:** Browser merges `fpv/+/detection` payloads client-side (new `viewer.js`), routes view commands to the scanner that announces a retained `fpv/<id>/view` state, and plays the RTSP-pushed demod stream via the existing WHEP path. Agent-side: `ViewController` gets a retune loop (no sweep return between frequencies), `run_cycle` gets an `abort` hook so a pending view interrupts the sweep between bands/dwells, and the view state gains a `stream` field announced retained at startup.

**Tech Stack:** Python 3 (agent, pytest, flat-module imports per `agent/scan/conftest.py`), vanilla ES modules + `node:test` (dashboard), MQTT (mosquitto, unchanged ACL), MediaMTX WHEP.

**Spec:** `docs/superpowers/specs/2026-07-07-multiband-view-workflow-design.md`

## Global Constraints

- UI copy is Ukrainian; code identifiers/comments English (existing style).
- NO changes to `dashboard/server.js`, broker config, or mosquitto ACL.
- View **commands** on `fpv/<id>/rxcmd` stay NOT retained; view **state** on `fpv/<id>/view` stays retained QoS 1.
- The RX5808 `{mode, channel}` command contract must keep working unchanged.
- Detection field is named `class` (not `standard`); frequencies are MHz numbers; `ts` is epoch **seconds**.
- Python tests: `python -m pytest agent/scan/tests -q` (run from repo root). Node tests: `npm test` (or `node --test test/<file>.test.js`).
- Recent-detections TTL: 300 s; live-claim staleness: 120 s; retune restarts the `VIEW_MAX_S` (600 s) deadline.
- Commit after every task (conventional commits, `feat(...)`/`test(...)`).

---

### Task 1: `stream` field in the view-state payload + `on_connected` hook (publisher)

**Files:**
- Modify: `agent/scan/publisher.py` (`publish_view` ~line 139, `__init__` ~line 55, `_on_connect` ~line 73)
- Test: `agent/scan/tests/test_publisher.py`

**Interfaces:**
- Produces: `MqttPublisher.publish_view(ts, active, freq_mhz=None, until_ts=None, error=None, stream=None)` — payload gains `"stream"` key.
- Produces: `MqttPublisher.on_connected` — optional `fn()` callback attribute, invoked after every successful (re)connect housekeeping (Task 4 wires it to `ViewController.announce`).

- [ ] **Step 1: Write the failing tests**

Append to `agent/scan/tests/test_publisher.py`:

```python
def test_publish_view_carries_stream_name():
    p = _MP("h", 1883, "", "", "scan-01")
    p._client = _FakeViewClient()
    p.publish_view(123, True, freq_mhz=5865.0, until_ts=723, stream="hackrf-view")
    topic, data, qos, retain = p._client.published[0]
    assert topic == "fpv/scan-01/view" and qos == 1 and retain is True
    assert data["stream"] == "hackrf-view"
    p.publish_view(124, False)                      # stream defaults to None
    assert p._client.published[1][1]["stream"] is None


def test_on_connected_hook_fires_after_connect_housekeeping():
    fake = FakeClient()
    p = _pub(fake)
    p.connect(ts=100)
    calls = []
    p.on_connected = lambda: calls.append(1)
    p._on_connect(fake, None, None, 0)
    assert calls == [1]
    # refused CONNACK must NOT fire the hook
    p._on_connect(fake, None, None, 5)
    assert calls == [1]


def test_on_connected_hook_errors_are_swallowed():
    fake = FakeClient()
    p = _pub(fake)
    p.connect(ts=100)
    def boom():
        raise RuntimeError("boom")
    p.on_connected = boom
    p._on_connect(fake, None, None, 0)              # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest agent/scan/tests/test_publisher.py -q`
Expected: 3 new tests FAIL (`KeyError: 'stream'` / no `on_connected` behavior); existing tests PASS.

- [ ] **Step 3: Implement**

In `agent/scan/publisher.py`:

`__init__` — after `self.on_view_command = None` add:

```python
        self.on_connected = None        # set by the caller: fn() — runs after each (re)connect
```

`_on_connect` — after the `client.subscribe(self._t_rxcmd)` line, inside the same `try`, add:

```python
            if self.on_connected is not None:
                try:
                    self.on_connected()
                except Exception:
                    LOG.exception("on_connected hook failed")
```

`publish_view` — replace with:

```python
    def publish_view(self, ts, active, freq_mhz=None, until_ts=None, error=None, stream=None):
        self._publish(
            self._t_view,
            {"scanner_id": self.scanner_id, "ts": ts, "active": bool(active),
             "freq_mhz": freq_mhz, "until_ts": until_ts, "error": error, "stream": stream},
            self.QOS_DETECTION,
        )
```

Also update the EXISTING `test_publish_view_contract` expected dict to include `"stream": None`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest agent/scan/tests/test_publisher.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/scan/publisher.py agent/scan/tests/test_publisher.py
git commit -m "feat(scan): view-state stream field + on_connected publisher hook"
```

---

### Task 2: ViewController — stream name, last-state tracking, `announce()`

**Files:**
- Modify: `agent/scan/view_controller.py`
- Test: `agent/scan/tests/test_view_controller.py`

**Interfaces:**
- Consumes: `publish_view(..., stream=)` from Task 1.
- Produces: `stream_name_from_push_url(url) -> str|None` (module-level, `view_controller.py`).
- Produces: `ViewController(publisher, run_stream, max_s=600.0, reset=None, clock=None, stream=None)`.
- Produces: `ViewController.announce()` — republishes the last-known view state (initially inactive) retained; safe from the MQTT thread.

- [ ] **Step 1: Write the failing tests**

In `agent/scan/tests/test_view_controller.py`, update the `_Pub` fake to accept + record `stream`:

```python
class _Pub:
    def __init__(self):
        self.calls = []

    def publish_view(self, ts, active, freq_mhz=None, until_ts=None, error=None, stream=None):
        self.calls.append({"ts": ts, "active": active, "freq_mhz": freq_mhz,
                           "until_ts": until_ts, "error": error, "stream": stream})
```

Append new tests:

```python
def test_stream_name_from_push_url():
    from view_controller import stream_name_from_push_url
    assert stream_name_from_push_url("rtsp://u:p@10.8.0.1:8554/hackrf-view") == "hackrf-view"
    assert stream_name_from_push_url("rtsp://host:8554/a/b-view/") == "b-view"
    assert stream_name_from_push_url("") is None
    assert stream_name_from_push_url(None) is None


def test_announce_publishes_retained_inactive_state_with_stream():
    pub = _Pub()
    vc = ViewController(pub, lambda *a: None, stream="hackrf-view", clock=lambda: 111.0)
    vc.announce()
    assert pub.calls == [{"ts": 111, "active": False, "freq_mhz": None,
                          "until_ts": None, "error": None, "stream": "hackrf-view"}]


def test_announce_mid_session_republishes_the_active_state():
    pub = _Pub()

    def stream(freq, stop, max_s):
        vc.announce()                    # simulates an MQTT reconnect during a session
        return None

    vc = ViewController(pub, stream, max_s=60.0, reset=lambda: None,
                        clock=lambda: 1000.0, stream="hackrf-view")
    vc.run_view(5865.0)
    actives = [c for c in pub.calls if c["active"]]
    assert len(actives) == 2             # session start + reconnect re-announce
    assert actives[1]["freq_mhz"] == 5865.0 and actives[1]["until_ts"] == 1060
    assert all(c["stream"] == "hackrf-view" for c in pub.calls)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest agent/scan/tests/test_view_controller.py -q`
Expected: new tests FAIL (`ImportError: stream_name_from_push_url` / `TypeError: unexpected keyword 'stream'`).

- [ ] **Step 3: Implement**

In `agent/scan/view_controller.py`:

Module-level, after the `FREQ_MAX_MHZ` constant:

```python
def stream_name_from_push_url(url):
    """Last path segment of the RTSP push URL = MediaMTX path = WHEP stream name."""
    tail = (url or "").split("?")[0].rstrip("/").rsplit("/", 1)
    return tail[1] if len(tail) == 2 and tail[1] else None
```

`__init__` — new signature + state:

```python
    def __init__(self, publisher, run_stream, max_s=600.0, reset=None, clock=None, stream=None):
        self._publisher = publisher
        self._run_stream = run_stream        # fn(freq_mhz, stop_event, max_s) -> error|None
        self._max_s = max_s
        self._reset = reset or (lambda: None)
        self._clock = clock or time.time
        self._stream = stream                # WHEP stream name, echoed in every state publish
        self._last = (False, None, None, None)   # (active, freq_mhz, until_ts, error)
        self._lock = threading.Lock()
        self._pending = None
        self._stop = threading.Event()
```

New method:

```python
    def announce(self):
        """(Re)publish the last-known retained state — startup capability announce
        (also clears a stale retained active:true after a crash) and reconnect refresh."""
        active, freq_mhz, until_ts, error = self._last
        self._pub(int(self._clock()), active, freq_mhz, until_ts, error)
```

`_pub` — record the state and pass `stream`:

```python
    def _pub(self, ts, active, freq_mhz, until_ts, error=None):
        self._last = (active, freq_mhz, until_ts, error)
        if self._publisher is None:
            return
        try:
            self._publisher.publish_view(ts, active, freq_mhz=freq_mhz,
                                         until_ts=until_ts, error=error,
                                         stream=self._stream)
        except Exception:
            LOG.exception("view state publish failed")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest agent/scan/tests/test_view_controller.py -q`
Expected: all PASS (including the pre-existing lifecycle tests).

- [ ] **Step 5: Commit**

```bash
git add agent/scan/view_controller.py agent/scan/tests/test_view_controller.py
git commit -m "feat(scan): view capability announce (retained state + stream name)"
```

---

### Task 3: ViewController — retune loop + `has_pending`

**Files:**
- Modify: `agent/scan/view_controller.py` (`set_command`, `run_view`)
- Test: `agent/scan/tests/test_view_controller.py`

**Interfaces:**
- Produces: `ViewController.has_pending() -> bool` (non-consuming; Task 4 passes it as `run_cycle`'s `abort`).
- Behavior: a `{view:'start'}` during an active session interrupts `run_stream` and restarts it at the new frequency WITHOUT returning to the sweep; each retune republishes `active:true` with a fresh `until_ts`.

- [ ] **Step 1: Write the failing tests**

Append to `agent/scan/tests/test_view_controller.py`:

```python
def test_has_pending_is_non_consuming():
    vc = ViewController(None, run_stream=lambda *a: None)
    assert vc.has_pending() is False
    vc.set_command({"view": "start", "freq_mhz": 5865})
    assert vc.has_pending() is True
    assert vc.pending() == 5865.0
    assert vc.has_pending() is False


def test_run_view_retunes_in_place_on_start_command():
    pub = _Pub()
    freqs = []

    def stream(freq, stop, max_s):
        freqs.append(freq)
        if len(freqs) == 1:
            vc.set_command({"view": "start", "freq_mhz": 1280})
            assert stop.is_set()         # the running stream is interrupted immediately
        return None

    vc = ViewController(pub, stream, max_s=60.0, reset=lambda: None, clock=lambda: 1000.0)
    assert vc.run_view(5865.0) is None
    assert freqs == [5865.0, 1280.0]     # second session started WITHOUT leaving run_view
    actives = [c for c in pub.calls if c["active"]]
    assert [c["freq_mhz"] for c in actives] == [5865.0, 1280.0]
    assert all(c["until_ts"] == 1060 for c in actives)   # fresh 10-min deadline per retune
    assert pub.calls[-1]["active"] is False              # single final inactive publish


def test_retune_after_stream_error_resets_device_and_keeps_retune():
    pub = _Pub()
    calls = []
    resets = []

    def stream(freq, stop, max_s):
        calls.append(freq)
        if len(calls) == 1:
            vc.set_command({"view": "start", "freq_mhz": 2400})
            return "hackrf_transfer exited"
        return None

    vc = ViewController(pub, stream, max_s=60.0, reset=lambda: resets.append(len(calls)))
    assert vc.run_view(5865.0) is None                   # last session ended clean
    assert calls == [5865.0, 2400.0]
    assert resets and resets[0] == 1                     # reset BETWEEN error and retune
    assert pub.calls[-1]["error"] is None                # final state carries the last error only


def test_stop_command_during_session_exits_to_sweep():
    pub = _Pub()

    def stream(freq, stop, max_s):
        vc.set_command({"view": "stop"})
        return None

    vc = ViewController(pub, stream, max_s=60.0, reset=lambda: None)
    vc.run_view(5000.0)
    assert pub.calls[-1]["active"] is False
    assert vc.has_pending() is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest agent/scan/tests/test_view_controller.py -q`
Expected: `test_has_pending_is_non_consuming` FAILS (`AttributeError: has_pending`), retune test FAILS (`freqs == [5865.0]`).

- [ ] **Step 3: Implement**

In `agent/scan/view_controller.py`:

`set_command` — a valid start ALSO sets `_stop` (interrupts an active session; harmless when idle because `run_view` clears stale stops):

```python
    def set_command(self, data):
        action = data.get("view")
        if action == "stop":
            self._stop.set()
            return
        if action != "start":
            LOG.warning("view: ignoring unknown action %r", action)
            return
        freq = data.get("freq_mhz")
        if not isinstance(freq, (int, float)) or not (FREQ_MIN_MHZ <= float(freq) <= FREQ_MAX_MHZ):
            LOG.warning("view: ignoring start with bad freq_mhz %r", freq)
            return
        with self._lock:
            self._pending = float(freq)
        self._stop.set()                 # active session -> retune now; idle -> cleared on entry
```

New method after `pending()`:

```python
    def has_pending(self):
        """Non-consuming pending check — the sweep's abort hook."""
        with self._lock:
            return self._pending is not None
```

`run_view` — retune loop:

```python
    def run_view(self, freq_mhz):
        freq = freq_mhz
        error = None
        try:
            while True:
                self._stop.clear()       # a stale stop (or our own retune flag) must not kill this session
                ts = int(self._clock())
                self._pub(ts, True, freq, ts + int(self._max_s))
                try:
                    error = self._run_stream(freq, self._stop, self._max_s)
                except Exception as e:
                    LOG.exception("view stream crashed")
                    error = str(e)
                nxt = self.pending()
                if nxt is None:
                    break                # stop / timeout / unrecovered error -> back to sweep
                if error is not None:
                    try:
                        self._reset()    # leave the device clean before retrying at the new freq
                    except Exception:
                        LOG.exception("view: device reset failed")
                    error = None
                LOG.info("view retune -> %.1f MHz", nxt)
                freq = nxt
        finally:
            self._pub(int(self._clock()), False, None, None, error)
            try:
                self._reset()            # leave the device clean for the next sweep
            except Exception:
                LOG.exception("view: device reset failed")
            self._stop.clear()
        return error
```

- [ ] **Step 4: Run the full agent/scan suite**

Run: `python -m pytest agent/scan/tests -q`
Expected: all PASS (lifecycle/stale-stop tests must survive the loop rewrite).

- [ ] **Step 5: Commit**

```bash
git add agent/scan/view_controller.py agent/scan/tests/test_view_controller.py
git commit -m "feat(scan): retune-in-place view sessions + non-consuming has_pending"
```

---

### Task 4: Fast view entry (`run_cycle` abort) + main-loop wiring

**Files:**
- Modify: `agent/scan/main.py` (`run_cycle` ~line 95, view init ~line 248, main loop ~line 283)
- Test: `agent/scan/tests/test_run_cycle.py`

**Interfaces:**
- Consumes: `view.has_pending` (Task 3), `view.announce` (Task 2), `publisher.on_connected` (Task 1), `stream_name_from_push_url` (Task 2).
- Produces: `run_cycle(cfg, now_ts, publisher=None, emitter=None, controller=None, abort=None) -> dict|None` — returns `None` (nothing further published, no state write) when `abort()` fires between bands or between dwells.

- [ ] **Step 1: Write the failing tests**

Append to `agent/scan/tests/test_run_cycle.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest agent/scan/tests/test_run_cycle.py -q`
Expected: first two FAIL (`TypeError: run_cycle() got an unexpected keyword argument 'abort'`).

- [ ] **Step 3: Implement**

In `agent/scan/main.py`:

`run_cycle` signature:

```python
def run_cycle(cfg: Config, now_ts: int, publisher=None, emitter=None, controller=None,
              abort=None) -> dict:
```

Top of the band loop (first lines inside `for band, brange in cfg.bands.items():`):

```python
        if abort is not None and abort():
            LOG.info("scan cycle aborted before band %s (view pending)", band)
            return None
```

Top of the dwell loop (first lines inside `for i, c in enumerate(cands):`, before the budget check):

```python
            if abort is not None and abort():
                LOG.info("scan cycle aborted mid-band %s (view pending)", band)
                return None
```

View init block (~line 257) — pass the stream name and wire the announce:

```python
                from view_controller import ViewController, stream_name_from_push_url
                view = ViewController(
                    publisher,
                    run_stream=lambda freq, stop, max_s: stream_demod.run_stream(
                        viewcfg, freq, stop, max_s,
                        lna=cfg.lna_gain, vga=cfg.vga_gain, amp=cfg.amp_enable),
                    max_s=viewcfg.view_max_s,
                    reset=reset_hackrf,
                    stream=stream_name_from_push_url(viewcfg.view_push_url),
                )
                publisher.on_view_command = view.set_command
                publisher.on_connected = view.announce   # retained announce on every (re)connect
```

Main loop — pass `abort`, guard the `None` return (replace the `payload = run_cycle(...)` / `holder.payload = payload` lines):

```python
            payload = run_cycle(cfg, now_ts=int(time.time()), publisher=publisher,
                                emitter=emitter, controller=controller,
                                abort=view.has_pending if view is not None else None)
            if payload is None:
                continue                 # aborted for a pending view -> enter it immediately
            holder.payload = payload
```

- [ ] **Step 4: Run the full agent suite**

Run: `python -m pytest agent/scan/tests agent/video/tests -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/scan/main.py agent/scan/tests/test_run_cycle.py
git commit -m "feat(scan): abort sweep cycle for a pending view + announce wiring"
```

---

### Task 5: `stream` through the dashboard MQTT reducer

**Files:**
- Modify: `dashboard/public/mqtt-scan.js` (view branch of `reduce`, ~line 62)
- Modify: `docs/superpowers/specs/2026-07-07-multiband-view-workflow-design.md` (Minor section — the reducer must COPY the field, it does not flow through by itself)
- Test: `test/mqtt-scan.test.js`

**Interfaces:**
- Produces: `store[id].view.stream: string|null` for Tasks 6–8.

- [ ] **Step 1: Update the failing test**

In `test/mqtt-scan.test.js`, replace the existing view test with:

```js
test('reduce: fpv/<id>/view updates the view state incl. stream', () => {
  const s = reduce(emptyStore(), 'fpv/hackrf/view', JSON.stringify({
    scanner_id: 'hackrf', ts: 5, active: true, freq_mhz: 5865, until_ts: 605,
    error: null, stream: 'hackrf-view',
  }));
  assert.deepEqual(s.hackrf.view,
    { ts: 5, active: true, freq_mhz: 5865, until_ts: 605, error: null, stream: 'hackrf-view' });
  reduce(s, 'fpv/hackrf/view', JSON.stringify({ ts: 6, active: false, error: 'ffmpeg exited' }));
  assert.equal(s.hackrf.view.active, false);
  assert.equal(s.hackrf.view.freq_mhz, null);
  assert.equal(s.hackrf.view.stream, null);       // absent field -> null (old agents)
  assert.equal(s.hackrf.view.error, 'ffmpeg exited');
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test test/mqtt-scan.test.js`
Expected: FAIL (`stream` missing from the reduced object).

- [ ] **Step 3: Implement**

In `dashboard/public/mqtt-scan.js`, the `kind === 'view'` branch:

```js
  } else if (kind === 'view') {
    s.view = {
      ts: data.ts || 0,
      active: !!data.active,
      freq_mhz: data.freq_mhz == null ? null : Number(data.freq_mhz),
      until_ts: data.until_ts == null ? null : Number(data.until_ts),
      error: data.error || null,
      stream: data.stream || null,
    };
```

In the spec's «Minor» section, replace the sentence about `mqtt-scan.js` with: `- mqtt-scan.js: the view reduce copies the new stream field (stream: data.stream || null).`

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test test/mqtt-scan.test.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/public/mqtt-scan.js test/mqtt-scan.test.js docs/superpowers/specs/2026-07-07-multiband-view-workflow-design.md
git commit -m "feat(dashboard): carry the view stream name through the MQTT reducer"
```

---

### Task 6: `viewer.js` — merged multiband detection state (pure core)

**Files:**
- Create: `dashboard/public/viewer.js`
- Test: `test/viewer.test.js`

**Interfaces:**
- Consumes: `detectionKey(d)` from `dashboard/public/alert.js` (band+channel | band+5 MHz-rounded key).
- Produces (all pure, unit-tested; used by Tasks 7–8):
  - `RECENT_TTL_S = 300`, `LIVE_STALE_S = 120`
  - `emptyViewer() -> {entries: {}, seenTs: {}}`
  - `applyDetections(vs, scannerId, det, nowS) -> vs` — `det` = `store[id].detection` (`{ts, detections:[...]}`); idempotent per `(scannerId, det.ts)`.
  - `seedFromJournal(vs, events, nowS) -> vs` — `events` = `GET /api/detections` array (newest-first `{ts, scanner_id, event, band, center_mhz, channel, class, snr_db, power_dbm}`).
  - `viewerRows(vs, nowS) -> [{...entry, live: bool}]` — sorted: live by `power_dbm` desc, then recent by `last_seen` desc; expired pruned.
  - `pickViewer(store) -> string|null` — online scanner with a `view` state; prefers an idle one.
  - `pickRxScanner(store) -> string|null` — online scanner with an `rxtune` state.
  - `viewStream(store, id) -> string` — `store[id].view.stream || `${id}-view``.
  - `ageLabel(nowS, ts) -> 'щойно' | 'N хв тому'`

- [ ] **Step 1: Write the failing tests**

Create `test/viewer.test.js`:

```js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  emptyViewer, applyDetections, seedFromJournal, viewerRows,
  pickViewer, pickRxScanner, viewStream, ageLabel, RECENT_TTL_S, LIVE_STALE_S,
} from '../dashboard/public/viewer.js';

const det = (over = {}) => ({
  band: '5.8G', center_mhz: 5865, channel: 'A1', class: 'analog',
  snr_db: 18, power_dbm: -50, ...over,
});

test('applyDetections merges the same signal from two scanners into one row', () => {
  const vs = emptyViewer();
  applyDetections(vs, 'bladerf', { ts: 100, detections: [det()] }, 100);
  applyDetections(vs, 'hackrf', { ts: 101, detections: [det({ center_mhz: 5864.2 })] }, 101);
  const rows = viewerRows(vs, 102);
  assert.equal(rows.length, 1);
  assert.deepEqual(Object.keys(rows[0].scanners).sort(), ['bladerf', 'hackrf']);
  assert.equal(rows[0].live, true);
});

test('applyDetections is idempotent for the same payload ts', () => {
  const vs = emptyViewer();
  const payload = { ts: 100, detections: [det()] };
  applyDetections(vs, 'bladerf', payload, 100);
  applyDetections(vs, 'bladerf', payload, 150);
  assert.equal(viewerRows(vs, 150).length, 1);
});

test('a detection missing from the next cycle goes recent, then expires after TTL', () => {
  const vs = emptyViewer();
  applyDetections(vs, 'bladerf', { ts: 100, detections: [det()] }, 100);
  applyDetections(vs, 'bladerf', { ts: 140, detections: [] }, 140);
  let rows = viewerRows(vs, 141);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].live, false);                    // dimmed but clickable
  rows = viewerRows(vs, 100 + RECENT_TTL_S + 1);
  assert.equal(rows.length, 0);                         // expired
});

test('a stale claim from a dead scanner does not keep an entry live', () => {
  const vs = emptyViewer();
  applyDetections(vs, 'bladerf', { ts: 100, detections: [det()] }, 100);
  const rows = viewerRows(vs, 100 + LIVE_STALE_S + 1);  // scanner went silent
  assert.equal(rows.length, 1);
  assert.equal(rows[0].live, false);
});

test('viewerRows sorts live-by-power then recent-by-freshness', () => {
  const vs = emptyViewer();
  applyDetections(vs, 'bladerf', {
    ts: 100,
    detections: [
      det({ band: '1.2G', center_mhz: 1280, channel: null, power_dbm: -70 }),
      det({ band: '4.9G', center_mhz: 4240, channel: null, power_dbm: -40 }),
    ],
  }, 100);
  seedFromJournal(vs, [
    { ts: 90, scanner_id: 'hackrf', event: 'gone', band: '2.4G', center_mhz: 2450, channel: null, class: 'digital', snr_db: 9 },
    { ts: 60, scanner_id: 'hackrf', event: 'gone', band: '3.3G', center_mhz: 3470, channel: null, class: 'analog', snr_db: 12 },
  ], 100);
  const rows = viewerRows(vs, 101);
  assert.deepEqual(rows.map((r) => r.center_mhz), [4240, 1280, 2450, 3470]);
  assert.deepEqual(rows.map((r) => r.live), [true, true, false, false]);
});

test('seedFromJournal keeps only fresh gone events and never overwrites live entries', () => {
  const vs = emptyViewer();
  applyDetections(vs, 'bladerf', { ts: 100, detections: [det()] }, 100);
  seedFromJournal(vs, [
    { ts: 95, scanner_id: 'bladerf', event: 'gone', band: '5.8G', center_mhz: 5865, channel: 'A1', class: 'analog', snr_db: 5 },
    { ts: 100 - RECENT_TTL_S - 5, scanner_id: 'bladerf', event: 'gone', band: '1.2G', center_mhz: 1280, channel: null, class: 'analog', snr_db: 7 },
    { ts: 99, scanner_id: 'bladerf', event: 'appeared', band: '3.3G', center_mhz: 3470, channel: null, class: 'analog', snr_db: 8 },
  ], 100);
  const rows = viewerRows(vs, 100);
  assert.equal(rows.length, 1);                 // live 5865 kept (snr 18, not 5); stale+appeared skipped
  assert.equal(rows[0].snr_db, 18);
});

test('pickViewer wants an ONLINE scanner with a view state, idle preferred', () => {
  assert.equal(pickViewer({}), null);
  const store = {
    bladerf: { online: true, view: null, rxtune: null },
    hackrf: { online: true, view: { active: true, stream: 'hackrf-view' }, rxtune: {} },
  };
  assert.equal(pickViewer(store), 'hackrf');
  store.second = { online: true, view: { active: false, stream: 's2-view' } };
  assert.equal(pickViewer(store), 'second');    // idle wins over busy
  store.hackrf.online = false;
  store.second.online = false;
  assert.equal(pickViewer(store), null);        // offline viewers don't count
});

test('pickRxScanner finds the online scanner driving an RX5808', () => {
  assert.equal(pickRxScanner({ a: { online: true, rxtune: null } }), null);
  assert.equal(pickRxScanner({ a: { online: true, rxtune: { freq_mhz: 5865 } } }), 'a');
});

test('viewStream falls back to <id>-view for old agents', () => {
  assert.equal(viewStream({ h: { view: { stream: 'custom' } } }, 'h'), 'custom');
  assert.equal(viewStream({ h: { view: { stream: null } } }, 'h'), 'h-view');
});

test('ageLabel', () => {
  assert.equal(ageLabel(100, 70), 'щойно');
  assert.equal(ageLabel(400, 100), '5 хв тому');
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test test/viewer.test.js`
Expected: FAIL — `Cannot find module .../dashboard/public/viewer.js`.

- [ ] **Step 3: Implement**

Create `dashboard/public/viewer.js`:

```js
// dashboard/public/viewer.js — «FPV Viewer»: merged multiband detection list.
// Pure state/list helpers (unit-tested) + list HTML builder + DOM render (browser only).
import { detectionKey } from './alert.js';

export const RECENT_TTL_S = 300;      // dimmed-but-clickable window after a signal disappears
export const LIVE_STALE_S = 120;      // a scanner claim older than this no longer counts as live

export function emptyViewer() {
  return { entries: {}, seenTs: {} };  // entries: key -> entry; seenTs: scannerId -> last applied payload ts
}

function entryLive(e, nowS) {
  return Object.values(e.scanners).some((ts) => nowS - ts <= LIVE_STALE_S);
}

function prune(vs, nowS) {
  for (const [k, e] of Object.entries(vs.entries)) {
    if (!entryLive(e, nowS) && nowS - e.last_seen > RECENT_TTL_S) delete vs.entries[k];
  }
}

// Apply one scanner's fpv/<id>/detection payload ({ts, detections:[...]}) — idempotent per ts.
export function applyDetections(vs, scannerId, det, nowS) {
  if (!det || !Array.isArray(det.detections)) return vs;
  if (vs.seenTs[scannerId] === det.ts) return vs;
  vs.seenTs[scannerId] = det.ts;
  const ts = Number(det.ts) || nowS;
  for (const d of det.detections) {
    const key = detectionKey(d);
    const e = vs.entries[key] || (vs.entries[key] = { key, scanners: {}, seen_by: {}, last_seen: 0 });
    e.scanners[scannerId] = ts;
    e.seen_by[scannerId] = true;
    e.last_seen = Math.max(e.last_seen, ts);
    // freshest report wins the display fields
    e.band = d.band;
    e.center_mhz = d.center_mhz;
    e.channel = d.channel || null;
    e.class = d.class;
    e.snr_db = d.snr_db == null ? null : d.snr_db;
    e.power_dbm = d.power_dbm == null ? null : d.power_dbm;
  }
  // whatever this scanner did NOT report this cycle, it no longer sees
  for (const e of Object.values(vs.entries)) {
    if (e.scanners[scannerId] !== undefined && e.scanners[scannerId] !== ts) delete e.scanners[scannerId];
  }
  prune(vs, nowS);
  return vs;
}

// Seed «recent» rows from the detection journal (GET /api/detections, newest-first)
// so they survive a page reload. Live rows re-arrive via retained MQTT anyway.
export function seedFromJournal(vs, events, nowS) {
  for (const ev of events || []) {
    if (ev.event !== 'gone' || nowS - ev.ts > RECENT_TTL_S) continue;
    const key = detectionKey(ev);
    if (vs.entries[key]) continue;                    // never overwrite live/newer state
    vs.entries[key] = {
      key, scanners: {}, seen_by: { [ev.scanner_id]: true }, last_seen: ev.ts,
      band: ev.band, center_mhz: ev.center_mhz, channel: ev.channel || null,
      class: ev.class, snr_db: ev.snr_db == null ? null : ev.snr_db,
      power_dbm: ev.power_dbm == null ? null : ev.power_dbm,
    };
  }
  return vs;
}

// Rows for rendering: live first (strongest on top), then recent (freshest on top).
export function viewerRows(vs, nowS) {
  prune(vs, nowS);
  const rows = Object.values(vs.entries).map((e) => ({ ...e, live: entryLive(e, nowS) }));
  rows.sort((a, b) => {
    if (a.live !== b.live) return a.live ? -1 : 1;
    if (a.live) return (b.power_dbm ?? -999) - (a.power_dbm ?? -999);
    return b.last_seen - a.last_seen;
  });
  return rows;
}

// The scanner to send view commands to: online + announced view capability; idle preferred.
export function pickViewer(store) {
  const ids = Object.keys(store || {}).filter((id) => store[id] && store[id].online && store[id].view);
  if (!ids.length) return null;
  return ids.find((id) => !store[id].view.active) || ids[0];
}

// The scanner driving a physical RX5808 (for the 5.8G dual action).
export function pickRxScanner(store) {
  const ids = Object.keys(store || {}).filter((id) => store[id] && store[id].online && store[id].rxtune);
  return ids.length ? ids[0] : null;
}

export function viewStream(store, id) {
  const v = store && store[id] && store[id].view;
  return (v && v.stream) || `${id}-view`;
}

export function ageLabel(nowS, ts) {
  const s = Math.max(0, nowS - ts);
  if (s < 60) return 'щойно';
  return `${Math.round(s / 60)} хв тому`;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test test/viewer.test.js`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add dashboard/public/viewer.js test/viewer.test.js
git commit -m "feat(dashboard): merged multiband detection state for the FPV Viewer"
```

---

### Task 7: `viewer.js` — list HTML builder

**Files:**
- Modify: `dashboard/public/viewer.js`
- Test: `test/viewer.test.js`

**Interfaces:**
- Consumes: `classColor`, `fmtFreq` from `dashboard/public/spectrum.js`; rows from `viewerRows` (Task 6).
- Produces: `viewerListHtml(rows, nowS, activeFreq = null, canView = true) -> string` — table with `data-vwfreq`/`data-vwband` on each row; `.vw-recent` on non-live rows; `.is-viewing` on the row matching the active view frequency (±3 MHz); a hint paragraph when `canView` is false.

- [ ] **Step 1: Write the failing tests**

Append to `test/viewer.test.js`:

```js
import { viewerListHtml } from '../dashboard/public/viewer.js';

test('viewerListHtml renders clickable rows with band/freq data attrs', () => {
  const rows = [
    { key: 'k1', band: '4.9G', center_mhz: 4240, channel: null, class: 'analog',
      snr_db: 22, power_dbm: -40, scanners: { bladerf: 100 }, seen_by: { bladerf: true },
      last_seen: 100, live: true },
    { key: 'k2', band: '5.8G', center_mhz: 5865, channel: 'A1', class: 'analog',
      snr_db: 18, power_dbm: -50, scanners: {}, seen_by: { bladerf: true, hackrf: true },
      last_seen: 40, live: false },
  ];
  const html = viewerListHtml(rows, 100, 4240, true);
  assert.match(html, /data-vwfreq="4240" data-vwband="4\.9G"/);
  assert.match(html, /data-vwfreq="5865" data-vwband="5\.8G"/);
  assert.match(html, /is-viewing/);              // 4240 row highlighted (active view)
  assert.match(html, /vw-recent/);               // 5865 row dimmed
  assert.match(html, /5865 МГц \(A1\)/);
  assert.match(html, /1 хв тому/);
  assert.match(html, /bladerf/);
  assert.doesNotMatch(html, /SDR view недоступний/);
});

test('viewerListHtml without a viewer shows the hint and no play markers', () => {
  const rows = [{ key: 'k', band: '5.8G', center_mhz: 5865, channel: null, class: 'analog',
    snr_db: 18, power_dbm: -50, scanners: { b: 100 }, seen_by: { b: true }, last_seen: 100, live: true }];
  const html = viewerListHtml(rows, 100, null, false);
  assert.match(html, /SDR view недоступний/);
  assert.doesNotMatch(html, /▶/);
});

test('viewerListHtml with no rows renders the empty note', () => {
  assert.match(viewerListHtml([], 100), /детекцій немає/);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test test/viewer.test.js`
Expected: FAIL — `viewerListHtml` is not exported.

- [ ] **Step 3: Implement**

Append to `dashboard/public/viewer.js` (imports go to the top of the file):

```js
import { classColor, fmtFreq } from './spectrum.js';

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// Merged detection list as an HTML table. activeFreq = the active view's freq_mhz (or null);
// canView = a view-capable scanner is online (rows get ▶ and are clickable).
export function viewerListHtml(rows, nowS, activeFreq = null, canView = true) {
  if (!rows.length) return '<p class="scan-empty">детекцій немає — чекаємо на скан</p>';
  const hint = canView ? '' : '<p class="scan-empty">SDR view недоступний (view-сканер офлайн)</p>';
  const body = rows.map((e) => {
    const viewing = activeFreq != null && Math.abs(e.center_mhz - activeFreq) < 3;
    const cls = `${e.live ? '' : 'vw-recent'}${viewing ? ' is-viewing' : ''}`.trim();
    const freq = `${fmtFreq(e.center_mhz)}${e.channel ? ` (${escapeHtml(e.channel)})` : ''}`;
    const src = Object.keys(e.seen_by).map((s) => `<span class="vw-src">${escapeHtml(s)}</span>`).join(' ');
    return `<tr${cls ? ` class="${cls}"` : ''} data-vwfreq="${Number(e.center_mhz)}" data-vwband="${escapeHtml(e.band || '')}">
      <td>${canView ? '▶' : ''}</td>
      <td>${freq}</td>
      <td>${escapeHtml(e.band || '')}</td>
      <td><span class="cls" style="color:${classColor(e.class)}">${escapeHtml(e.class || '')}</span></td>
      <td>${e.snr_db == null ? '—' : escapeHtml(String(e.snr_db))} dB</td>
      <td>${src || '—'}</td>
      <td>${e.live ? 'зараз' : ageLabel(nowS, e.last_seen)}</td></tr>`;
  }).join('');
  return `${hint}<table class="scan-table viewer-table">
    <thead><tr><th></th><th>Частота</th><th>Бенд</th><th>Клас</th><th>SNR</th><th>Джерело</th><th>Коли</th></tr></thead>
    <tbody>${body}</tbody></table>`;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test test/viewer.test.js`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add dashboard/public/viewer.js test/viewer.test.js
git commit -m "feat(dashboard): FPV Viewer list renderer"
```

---

### Task 8: Panel markup, styles, and app wiring (clicks, player, journal seed)

**Files:**
- Modify: `dashboard/public/index.html` (panel above `#spectrum-panel`)
- Modify: `dashboard/public/styles.css` (append)
- Modify: `dashboard/public/app.js` (imports, viewer render, click delegation, player, seed)

**Interfaces:**
- Consumes: everything from Tasks 5–7; `startWhep` (`whep.js`), `nearestRxChannel` (`rx5808-channels.js`), `viewCaption` (`spectrum.js`), `scanClient` (`MqttScanClient`).
- Produces: the working panel. Browser-only code — verified by `node --check` + the live acceptance in Task 9.

- [ ] **Step 1: index.html — static skeleton (the video element must survive re-renders)**

In `dashboard/public/index.html`, directly ABOVE `<section id="spectrum-panel" ...>`:

```html
  <section id="viewer-panel" class="viewer-panel hidden" aria-live="polite">
    <div class="viewer-head">
      <strong>🎯 FPV Viewer</strong>
      <span id="viewer-badge" class="view-badge"></span>
      <span id="viewer-err" class="view-err"></span>
      <button id="viewer-stop" hidden>■ свіп</button>
    </div>
    <div class="viewer-body">
      <div id="viewer-list" class="viewer-list"></div>
      <div class="viewer-player">
        <video id="viewer-video" autoplay playsinline muted></video>
      </div>
    </div>
  </section>
```

- [ ] **Step 2: styles.css — append**

```css
/* FPV Viewer — merged multiband detection list + in-panel SDR view player */
.viewer-panel { margin:.6rem; padding:.7rem; background:var(--panel); border:1px solid var(--line); border-radius:10px; }
.viewer-panel.hidden { display:none; }
.viewer-head { display:flex; align-items:center; gap:.6rem; flex-wrap:wrap; }
.viewer-head .view-badge { color:#39d0ff; font-weight:600; }
.viewer-head .view-err { color:#f87171; font-size:.8rem; }
.viewer-head button { font-size:.75rem; background:#0c1118; color:#cfd6e0; border:1px solid var(--line); border-radius:4px; cursor:pointer; padding:.15rem .5rem; }
.viewer-body { display:flex; gap:.8rem; flex-wrap:wrap; margin-top:.5rem; }
.viewer-list { flex:1 1 340px; min-width:280px; max-height:360px; overflow-y:auto; }
.viewer-table tr[data-vwfreq] { cursor:pointer; }
.viewer-table tr[data-vwfreq]:hover { background:rgba(108,160,255,.10); }
.viewer-table tr.vw-recent { opacity:.55; }
.viewer-table tr.is-viewing { background:rgba(61,220,132,.10); }
.viewer-table tr.is-viewing td:first-child { color:#3ddc84; }
.vw-src { font-size:.7rem; color:#9aa4b2; border:1px solid var(--line); border-radius:3px; padding:0 .25rem; margin-right:.15rem; }
.viewer-player { flex:1 1 320px; min-width:260px; }
.viewer-player video { width:100%; aspect-ratio:4/3; background:#000; border:1px solid var(--line); border-radius:6px; }
```

- [ ] **Step 3: app.js — wire it up**

Add to the imports at the top:

```js
import { startWhep } from '/whep.js';                                        // (already there)
import { splitByKind, renderSpectrum, classColor, fmtFreq, viewCaption } from '/spectrum.js';  // + viewCaption
import {
  emptyViewer, applyDetections, seedFromJournal, viewerRows, viewerListHtml,
  pickViewer, pickRxScanner, viewStream,
} from '/viewer.js';
```

Module state (near `const scanClient = ...`):

```js
const viewerState = emptyViewer();
const viewerPanel = document.getElementById('viewer-panel');
let viewerPlayer = null;      // {player}|null
let viewerStreamKey = '';     // `${stream}|${freq}` of the running/starting player
```

Click delegation (after the `spectrumPanel.addEventListener('click', ...)` block):

```js
viewerPanel.addEventListener('click', (e) => {
  if (e.target.closest('#viewer-stop')) {
    const vid = pickViewer(scanClient.store);
    if (vid) scanClient.publishView(vid, 'stop');
    return;
  }
  const row = e.target.closest('[data-vwfreq]');
  if (!row) return;
  const f = Number(row.dataset.vwfreq);
  const vid = pickViewer(scanClient.store);
  if (!vid || !Number.isFinite(f) || f < 100 || f > 6000) return;
  scanClient.publishView(vid, 'start', f);
  if (row.dataset.vwband === '5.8G') {           // dual action: also tune the RX5808
    const rxId = pickRxScanner(scanClient.store);
    const ch = nearestRxChannel(f);
    if (rxId && ch) scanClient.publishCommand(rxId, { mode: 'manual', channel: ch.name });
  }
});
```

Viewer render + player sync (new functions, after `renderScan`):

```js
function renderViewer() {
  const store = scanClient.store;
  const nowS = Math.floor(Date.now() / 1000);
  for (const [sid, live] of Object.entries(store)) {
    if (live.detection) applyDetections(viewerState, sid, live.detection, nowS);
  }
  const hasScanners = scannersFromRegistry.length > 0;
  viewerPanel.classList.toggle('hidden', !hasScanners);
  if (!hasScanners) return;
  const viewerId = pickViewer(store);
  const view = viewerId ? store[viewerId].view : null;
  document.getElementById('viewer-list').innerHTML = viewerListHtml(
    viewerRows(viewerState, nowS), nowS,
    view && view.active ? view.freq_mhz : null, !!viewerId,
  );
  document.getElementById('viewer-badge').textContent = view ? viewCaption(view) : '';
  document.getElementById('viewer-err').textContent = (view && view.error) || '';
  document.getElementById('viewer-stop').hidden = !(view && view.active);
  syncViewerPlayer(store, viewerId, view);
}

// Keep the in-panel WHEP player in sync with the view state. On retune the RTSP path is
// recreated, so (re)connection attempts retry until the stream is back (or the key changes).
function syncViewerPlayer(store, viewerId, view) {
  const video = document.getElementById('viewer-video');
  const want = view && view.active ? `${viewStream(store, viewerId)}|${view.freq_mhz}` : '';
  if (want === viewerStreamKey) return;
  if (viewerPlayer && viewerPlayer.player) viewerPlayer.player.close();
  viewerPlayer = null;
  viewerStreamKey = want;
  if (!want) { video.srcObject = null; return; }
  startViewerWhep(video, viewStream(store, viewerId), want, 0);
}

async function startViewerWhep(video, stream, key, attempt) {
  if (viewerStreamKey !== key || attempt > 40) return;      // superseded or ~60 s of retries
  try {
    const p = await startWhep(video, `${cfg.webrtcBase}/${stream}/whep`, cfg.readUser, cfg.readPass);
    if (viewerStreamKey !== key) { p.close(); return; }
    viewerPlayer = { player: p };
  } catch {
    setTimeout(() => startViewerWhep(video, stream, key, attempt + 1), 1500);
  }
}
```

Call it from the end of `renderScan()` (both the hidden and the visible paths need it):

```js
function renderScan() {
  const scanners = scannersFromRegistry;
  if (!scanners.length) {
    spectrumPanel.classList.add('hidden');
    spectrumPanel.innerHTML = '';
    renderViewer();
    return;
  }
  ...existing body...
  renderViewer();
}
```

Journal seed in `init()` (before `scanClient.connect`):

```js
  try {
    const res = await fetch('/api/detections?limit=500');
    if (res.ok) seedFromJournal(viewerState, await res.json(), Math.floor(Date.now() / 1000));
  } catch { /* live-only if the journal is unavailable */ }
```

- [ ] **Step 4: Verify**

Run: `node --check dashboard/public/app.js && node --check dashboard/public/viewer.js && npm test`
Expected: both checks silent; full test suite PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/public/index.html dashboard/public/styles.css dashboard/public/app.js
git commit -m "feat(dashboard): FPV Viewer panel — click-to-watch with in-panel player"
```

---

### Task 9: Deploy + live acceptance

**Files:** none (operational task; needs the operator/hardware).

Pre-req: PR merged to `main` (use superpowers:finishing-a-development-branch).

- [ ] **Step 1: Pi (scanner) update** — hackrf unit only, do NOT overwrite the hand-diverged unit file:

```bash
ssh andriy@192.168.1.204 "cd /opt/fpv-video-stream && git pull && systemctl list-units | grep -i fpv"
# restart the unit that runs SCAN_ID=hackrf (VIEW_ENABLED=1); bladerf unit needs a restart
# too (it picks up the run_cycle abort param — harmless, abort stays None without view):
ssh andriy@192.168.1.204 "sudo systemctl restart <hackrf-unit> <bladerf-unit>"
```

Verify: `mosquitto_sub -t 'fpv/hackrf/view' -C 1` (with creds) shows retained `{"active": false, ..., "stream": "hackrf-view"}` right after the restart.

- [ ] **Step 2: Server (dashboard) update** — surgical, WG-only host 193.242.163.139, don't break wg-easy:

```bash
# per the established deploy pattern: git pull on the server checkout + restart the dashboard container/service
ssh <server> "cd /opt/fpv-video-stream && git pull && docker compose up -d --build dashboard"
```

- [ ] **Step 3: Live acceptance checklist**

1. Dashboard shows the «FPV Viewer» panel with detections from BOTH scanners merged (same 5.8 signal seen by both = one row with two source badges).
2. Turn on a test TX in a non-5.8 band (e.g. ~4240, [[agent-video-frame]] setup). The bladeRF detection row appears; click it → the in-panel player shows the demod stream within seconds (view entry ≤ one band sweep, no full-cycle wait).
3. Click another detection while watching → the stream switches in ≤5 s (retune, WHEP auto-reconnect), badge shows the new freq and a fresh «до HH:MM».
4. On a 5.8G row click, the RX5808 also tunes (rxtune badge updates to the nearest channel).
5. ■ свіп (or 10-min timeout) → view ends, hackrf spectrum updates resume, panel badge clears.
6. Reload the page → recently-gone detections still listed (dimmed, journal seed).
7. Stop the hackrf unit → rows lose ▶, hint «SDR view недоступний» appears; bladeRF rows keep updating.

- [ ] **Step 4: Update memory** — refresh `improve-sdr-view-next.md` (sub-feature 1 shipped) and `sdr-view-stream.md` (retune + announce + panel) in auto-memory.

---

## Self-Review (done at plan-writing time)

- **Spec coverage:** merged list (T6), seeding (T6/T8), routing via retained announce (T1/T2/T4, consumed T6/T8), retune-in-place (T3), fast entry (T4), `stream` field (T1/T5), panel+player+dual action+hint (T7/T8), zero server changes (no server task), error handling (T3 error-retune test; T8 badge/err), deploy+acceptance (T9). ✓
- **Type consistency:** `publish_view(..., stream=None)` matches `_pub`/`announce` callers; `run_cycle(..., abort=None)` matches the main-loop call; `viewerListHtml(rows, nowS, activeFreq, canView)` matches the T8 call; `applyDetections(vs, scannerId, det, nowS)` matches the T8 loop; `pickViewer/pickRxScanner/viewStream(store, ...)` all take the raw `scanClient.store`. ✓
- **Placeholder scan:** every code step contains the actual code; no TBD/TODO. ✓
