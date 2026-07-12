# Manual freq + bandwidth (BW) at SDR view — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the operator set a demod **bandwidth** (video lowpass width) per view session, live, from each viewer card — alongside the frequency — to tune picture detail vs noise.

**Architecture:** Thread an optional `bandwidth_mhz` through the EXISTING view command → `ViewController` → `run_stream_source`'s demod lowpass. BW = demod `lpf_cutoff_hz` (MHz), clamped `[0.5, fs/2]`; RF `sample_rate` stays fixed. No new MQTT topics/ACL. Same path for HackRF and bladeRF (both already use `run_stream_source`).

**Tech Stack:** Python 3 + numpy (agent), vanilla ES modules (dashboard), node:test + pytest, dev-preview harness.

## Global Constraints

- BW semantics: `bandwidth_mhz` = demod lowpass cutoff (MHz). Agent clamps to `[0.5, fs/2]` where `fs = viewcfg.view_sample_rate_hz`. Absent/invalid → agent default `vcfg.lpf_cutoff_hz` (unchanged behavior). `sample_rate` is NEVER changed.
- No new MQTT topics or ACL changes — reuse `fpv/<id>/rxcmd` (command) and `fpv/<id>/view` (retained state, now carries `bandwidth_mhz`).
- The `run_stream(freq, bw, stop, max_s)` contract is set by `ViewController` and consumed by `main.py`'s `run` closure; keep them in sync.
- Reconcile-safety (dashboard): the BW `<input>` is user input — the card is built once and NEVER re-innerHTML'd; `updateCard` may set the BW input's PLACEHOLDER (active bw) but must NOT overwrite its `value`. (See [[fpv-viewer-multi-player]] / [[tactical-ui-redesign]].)
- `npm test` and the pytest suites green each task (run per-package: `python -m pytest agent/scan/tests` etc — NOT `pytest agent` which mis-collects).
- Deploy: Pi (both `fpv-scan` + `fpv-scan-hackrf`) + dashboard rebuild.
- Commit footer:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01JLJdXSvLMBojY7w2dcUwuN
  ```
- Branch: `feat/view-bandwidth-control` (spec committed there).

## File structure

- `agent/video/stream_demod.py` — `run_stream_source` gains `lpf_cutoff_hz=None` override.
- `agent/scan/view_controller.py` — pending = `(freq, bw)`; `run_view` threads bw; `_pub`/`announce` carry `bandwidth_mhz`.
- `agent/scan/main.py` — `run(freq, bw, stop, max_s)` closures (both branches) clamp bw→lpf, pass to `run_stream_source`.
- `agent/scan/publisher.py` — `publish_view(..., bandwidth_mhz=None)`.
- `dashboard/public/mqtt-scan.js` — `buildViewCommand(action, freqMhz, bwMhz)`, `publishView(..., bwMhz)`, `reduce` view carries `bandwidth_mhz`.
- `dashboard/public/app.js` — `onViewStart(id, freq, bw)` → `publishView(id,'start',freq,bw)`; `viewerRowClick(freq,band,viewerId,bw)`.
- `dashboard/public/views/viewer.js` — BW `<input>` per card; starts (play/stepper/row-button) carry the target card's BW.
- Tests: `agent/scan/tests/test_view_controller.py`, `agent/video/tests/test_stream_demod.py`, `agent/scan/tests/test_run_cycle.py`, `agent/scan/tests/test_publisher.py`, `test/mqtt-scan.test.js`.

---

### Task 1: `run_stream_source` — optional demod `lpf_cutoff_hz` override

**Files:**
- Modify: `agent/video/stream_demod.py` (`run_stream_source`, ~439-505)
- Test: `agent/video/tests/test_stream_demod.py`

**Interfaces:**
- Produces: `run_stream_source(vcfg, source, freq_mhz, stop_event, max_s, encoder, clock=None, channel_of=None, lpf_cutoff_hz=None)` — when `lpf_cutoff_hz is None`, uses `vcfg.lpf_cutoff_hz` (unchanged); otherwise uses the passed value in BOTH `pick_standard` and `chunk_to_frames`.

- [ ] **Step 1: Write the failing test**

Add to `agent/video/tests/test_stream_demod.py` (near the other `run_stream_source` tests; reuse `_FakeSource`, `_FakeEncoder`, `_vcfg`, `_chunk_bytes`, `CHUNK_S`):

```python
def test_run_stream_source_uses_lpf_override_when_given(monkeypatch):
    import stream_demod as sd
    seen = []
    real_ctf = sd.chunk_to_frames
    def spy_ctf(iq, fs, standard, width, height, lpf_cutoff_hz=5e6, **kw):
        seen.append(lpf_cutoff_hz)
        return real_ctf(iq, fs, standard, width, height, lpf_cutoff_hz, **kw)
    monkeypatch.setattr(sd, "chunk_to_frames", spy_ctf)
    fs = 4e6
    stop = threading.Event()
    src = _FakeSource([bytes(_chunk_bytes(fs))], stop=stop, stop_after=1)
    sd.run_stream_source(_vcfg(), src, 947.0, stop, max_s=60.0, encoder=_FakeEncoder(),
                         clock=lambda: 0.0, lpf_cutoff_hz=1.5e6)
    assert seen and all(c == 1.5e6 for c in seen)     # override reached the demod

def test_run_stream_source_defaults_lpf_to_vcfg(monkeypatch):
    import stream_demod as sd
    seen = []
    real_ctf = sd.chunk_to_frames
    def spy_ctf(iq, fs, standard, width, height, lpf_cutoff_hz=5e6, **kw):
        seen.append(lpf_cutoff_hz)
        return real_ctf(iq, fs, standard, width, height, lpf_cutoff_hz, **kw)
    monkeypatch.setattr(sd, "chunk_to_frames", spy_ctf)
    fs = 4e6
    stop = threading.Event()
    src = _FakeSource([bytes(_chunk_bytes(fs))], stop=stop, stop_after=1)
    cfg = _vcfg()                                       # _vcfg sets lpf_cutoff_hz = 2.5e6
    sd.run_stream_source(cfg, src, 947.0, stop, max_s=60.0, encoder=_FakeEncoder(),
                         clock=lambda: 0.0)             # no override
    assert seen and all(c == cfg.lpf_cutoff_hz for c in seen)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest agent/video/tests/test_stream_demod.py::test_run_stream_source_uses_lpf_override_when_given -v`
Expected: FAIL — `run_stream_source() got an unexpected keyword argument 'lpf_cutoff_hz'`.

- [ ] **Step 3: Implement**

In `agent/video/stream_demod.py`, change the `run_stream_source` signature and its two `vcfg.lpf_cutoff_hz` uses:

```python
def run_stream_source(vcfg, source, freq_mhz, stop_event, max_s, encoder, clock=None,
                      channel_of=None, lpf_cutoff_hz=None):
```

At the top of the body (after `fs = vcfg.view_sample_rate_hz`), add:

```python
    lpf = vcfg.lpf_cutoff_hz if lpf_cutoff_hz is None else lpf_cutoff_hz
```

Then replace the two `vcfg.lpf_cutoff_hz` occurrences inside the loop:
- in `pick_standard(bb, fs, vcfg.view_standard, vcfg.line_snr_db, vcfg.harm_snr_db)` — leave as-is (pick_standard takes no lpf); the lpf is used in `chunk_to_frames`.
- in `chunk_to_frames(iq, fs, standard, vcfg.view_width, VIEW_CANVAS_HEIGHT, vcfg.lpf_cutoff_hz, vcfg.blank_frac, budget=..., tracker=...)` → replace `vcfg.lpf_cutoff_hz` with `lpf`.

(Note: `pick_standard` uses `vcfg.lpf_cutoff_hz` internally? No — it calls `detect_standard`; the demod lowpass for detection is inside `pick_standard`→`bb` which is computed as `lowpass(fm_demod(iq), fs, vcfg.lpf_cutoff_hz)` in the `if standard is None:` block. Replace THAT `vcfg.lpf_cutoff_hz` with `lpf` too, so detection and frames use the same cutoff.)

Concretely, the `if standard is None:` block's first line becomes:
```python
            bb = lowpass(fm_demod(iq), fs, lpf)
```
and the `chunk_to_frames(...)` call uses `lpf`.

- [ ] **Step 4: Run tests**

Run: `python -m pytest agent/video/tests/test_stream_demod.py -v`
Expected: PASS (both new tests + all existing).

- [ ] **Step 5: Commit**

```bash
git add agent/video/stream_demod.py agent/video/tests/test_stream_demod.py
git commit -m "feat(view): run_stream_source accepts a demod lpf_cutoff_hz override

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JLJdXSvLMBojY7w2dcUwuN"
```

---

### Task 2: `ViewController` — carry `(freq, bw)` through the session

**Files:**
- Modify: `agent/scan/view_controller.py`
- Test: `agent/scan/tests/test_view_controller.py`

**Interfaces:**
- Consumes: `run_stream(freq, bw, stop, max_s)` (the run closure — Task 3 supplies it).
- Produces: `set_command` reads `bandwidth_mhz`; `pending()`/`run_view(req)` use `req = (freq_mhz, bw_mhz)`; `_pub`/`publish_view`/`announce` carry `bandwidth_mhz` (None when inactive).

- [ ] **Step 1: Update the tests to the new contract (write them first)**

In `agent/scan/tests/test_view_controller.py`, apply these mechanical changes (they encode the new contract):
- `_Pub.publish_view` signature → add `bandwidth_mhz=None`; append it to the recorded dict:
  ```python
      def publish_view(self, ts, active, freq_mhz=None, until_ts=None, error=None, stream=None, bandwidth_mhz=None):
          self.calls.append({"ts": ts, "active": active, "freq_mhz": freq_mhz,
                             "until_ts": until_ts, "error": error, "stream": stream,
                             "bandwidth_mhz": bandwidth_mhz})
  ```
- Every `run_stream`/`stream` stub gains a `bw` param after `freq`: `def stream(freq, stop, max_s)` → `def stream(freq, bw, stop, max_s)`; lambdas `lambda f, s, m:` → `lambda f, bw, s, m:`; `lambda *a:` stays.
- Every `vc.run_view(<freq>)` → `vc.run_view((<freq>, None))` (e.g. `vc.run_view(5865.0)` → `vc.run_view((5865.0, None))`).
- Every `vc.pending() == <freq>` → `vc.pending() == (<freq>, None)` (e.g. `test_set_command_validates...`, `test_has_pending...`).
- In `test_run_view_lifecycle_publishes_and_resets`, the stream stub asserts `freq == 5865.0 and max_s == 60.0` — keep, now `def stream(freq, bw, stop, max_s)`.
- `test_announce_publishes_retained_inactive_state_with_stream`: the expected dict gains `"bandwidth_mhz": None`.

Add two NEW tests:
```python
def test_set_command_parses_bandwidth():
    vc = ViewController(None, run_stream=lambda *a: None)
    vc.set_command({"view": "start", "freq_mhz": 5865, "bandwidth_mhz": 3.5})
    assert vc.pending() == (5865.0, 3.5)
    vc.set_command({"view": "start", "freq_mhz": 5865})              # no bw -> None
    assert vc.pending() == (5865.0, None)
    vc.set_command({"view": "start", "freq_mhz": 5865, "bandwidth_mhz": "x"})  # bad bw -> None
    assert vc.pending() == (5865.0, None)

def test_run_view_threads_bandwidth_to_stream_and_publish():
    pub = _Pub()
    seen = []
    def stream(freq, bw, stop, max_s):
        seen.append((freq, bw))
        return None
    vc = ViewController(pub, stream, max_s=60.0, reset=lambda: None, clock=lambda: 1000.0)
    vc.run_view((5865.0, 2.0))
    assert seen == [(5865.0, 2.0)]
    start = [c for c in pub.calls if c["active"]][0]
    assert start["bandwidth_mhz"] == 2.0
    assert pub.calls[-1]["bandwidth_mhz"] is None      # final inactive publish clears bw
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest agent/scan/tests/test_view_controller.py -v`
Expected: FAIL (pending returns a float not a tuple; stream stubs get wrong arity; new tests error).

- [ ] **Step 3: Implement**

In `agent/scan/view_controller.py`:

- `__init__`: change `self._last = (False, None, None, None)` → `self._last = (False, None, None, None, None)` (adds bw slot).

- `set_command`: after the freq validation block (which `return`s on bad freq), replace the pending-store with:
  ```python
          bw = data.get("bandwidth_mhz")
          bw = float(bw) if isinstance(bw, (int, float)) else None
          with self._lock:
              self._pending = (float(freq), bw)
          self._stop.set()
  ```

- `run_view`: change signature and threading:
  ```python
      def run_view(self, req):
          freq, bw = req
          error = None
          try:
              while True:
                  self._stop.clear()
                  ts = int(self._clock())
                  self._pub(ts, True, freq, ts + int(self._max_s), bandwidth_mhz=bw)
                  try:
                      error = self._run_stream(freq, bw, self._stop, self._max_s)
                  except Exception as e:
                      LOG.exception("view stream crashed")
                      error = str(e)
                  nxt = self.pending()
                  if nxt is None:
                      break
                  if error is not None:
                      try:
                          self._reset()
                      except Exception:
                          LOG.exception("view: device reset failed")
                      error = None
                  freq, bw = nxt
                  LOG.info("view retune -> %.1f MHz (bw=%s)", freq, bw)
          finally:
              try:
                  self._on_idle()
              except Exception:
                  LOG.exception("view: on_idle failed")
              self._pub(int(self._clock()), False, None, None, error, bandwidth_mhz=None)
              try:
                  self._reset()
              except Exception:
                  LOG.exception("view: device reset failed")
              self._stop.clear()
          return error
  ```

- `_pub`: add `bandwidth_mhz=None` param, store in `_last`, forward to `publish_view`:
  ```python
      def _pub(self, ts, active, freq_mhz, until_ts, error=None, bandwidth_mhz=None):
          self._last = (active, freq_mhz, until_ts, error, bandwidth_mhz)
          if self._publisher is None:
              return
          try:
              self._publisher.publish_view(ts, active, freq_mhz=freq_mhz,
                                           until_ts=until_ts, error=error,
                                           stream=self._stream, bandwidth_mhz=bandwidth_mhz)
          except Exception:
              LOG.exception("view state publish failed")
  ```

- `announce`: unpack the 5-tuple:
  ```python
      def announce(self):
          active, freq_mhz, until_ts, error, bandwidth_mhz = self._last
          self._pub(int(self._clock()), active, freq_mhz, until_ts, error, bandwidth_mhz)
  ```

- [ ] **Step 4: Run tests**

Run: `python -m pytest agent/scan/tests/test_view_controller.py -v`
Expected: PASS (all updated + 2 new).

- [ ] **Step 5: Commit**

```bash
git add agent/scan/view_controller.py agent/scan/tests/test_view_controller.py
git commit -m "feat(view): ViewController threads (freq, bandwidth) through the session

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JLJdXSvLMBojY7w2dcUwuN"
```

---

### Task 3: `main.py` run closures (bw→lpf clamp) + `publisher.publish_view` bw

**Files:**
- Modify: `agent/scan/main.py` (view wiring ~269-306; main-loop `run_view` call ~324-327)
- Modify: `agent/scan/publisher.py` (`publish_view`, ~145-149)
- Test: `agent/scan/tests/test_publisher.py`, `agent/scan/tests/test_run_cycle.py`

**Interfaces:**
- Consumes: `run_stream_source(..., lpf_cutoff_hz=)` (Task 1); `ViewController.run_view((freq,bw))` (Task 2).
- Produces: `publish_view(..., bandwidth_mhz=None)` payload key; the `run` closures apply the clamp.

- [ ] **Step 1: Write the failing tests**

In `agent/scan/tests/test_publisher.py`, add a `publish_view` test using the file's existing `FakeClient` + `_pub(fake)` helpers (same pattern as `test_publish_rxtune_topic_qos_retain`):
```python
def test_publish_view_includes_bandwidth():
    fake = FakeClient()
    p = _pub(fake); p.connect(ts=1)
    p.publish_view(500, True, freq_mhz=5865, until_ts=1600, stream="hackrf-view", bandwidth_mhz=2.5)
    msg = [m for m in fake.published if m[0] == "fpv/hackrf/view"][-1]
    topic, payload, qos, retain = msg
    assert qos == 1 and retain is True
    body = json.loads(payload)
    assert body["bandwidth_mhz"] == 2.5
    assert body["freq_mhz"] == 5865 and body["stream"] == "hackrf-view" and body["active"] is True

def test_publish_view_bandwidth_defaults_null():
    fake = FakeClient()
    p = _pub(fake); p.connect(ts=1)
    p.publish_view(500, False)                          # no bw -> present as null
    body = json.loads([m for m in fake.published if m[0] == "fpv/hackrf/view"][-1][1])
    assert body["bandwidth_mhz"] is None
```

In `agent/scan/tests/test_run_cycle.py`, the existing `test_main_enters_pending_view_without_the_idle_sleep` fake view has `run_view(self, freq)` and `pending()` returning a float. Update its `_FakeView`: `pending()` returns `(5865.0, None)`, and `run_view(self, req)` appends `req` (so `entered == [(5865.0, None)]`). Keep the assertion shape but for the tuple.

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest agent/scan/tests/test_publisher.py -k bandwidth -v`
Expected: FAIL — `publish_view() got an unexpected keyword argument 'bandwidth_mhz'`.

- [ ] **Step 3: Implement**

**publisher.py** — `publish_view` (keep the existing `self._publish(topic, payload, self.QOS_DETECTION)` form; only ADD the `bandwidth_mhz` param + payload key):
```python
    def publish_view(self, ts, active, freq_mhz=None, until_ts=None, error=None, stream=None,
                     bandwidth_mhz=None):
        self._publish(
            self._t_view,
            {"scanner_id": self.scanner_id, "ts": ts, "active": bool(active),
             "freq_mhz": freq_mhz, "until_ts": until_ts, "error": error, "stream": stream,
             "bandwidth_mhz": bandwidth_mhz},
            self.QOS_DETECTION,
        )
```

**main.py** — the two view `run` closures (hackrf branch and bladerf branch) gain a `bw` param and compute the clamped lpf. Add a small module helper near the top:
```python
def _view_lpf(bw_mhz, fs_hz):
    """BW (MHz) -> demod lowpass cutoff (Hz), clamped to [0.5 MHz, fs/2]. None -> None (caller defaults)."""
    if not isinstance(bw_mhz, (int, float)):
        return None
    return max(0.5e6, min(float(bw_mhz) * 1e6, fs_hz / 2.0))
```
In the hackrf branch, replace the `run = lambda freq, stop, max_s: stream_demod.run_stream_source(...)` with:
```python
                    def _run_hackrf_view(freq, bw, stop, max_s):
                        return stream_demod.run_stream_source(
                            viewcfg, source, freq, stop, max_s, encoder,
                            channel_of=nearest_channel,
                            lpf_cutoff_hz=_view_lpf(bw, viewcfg.view_sample_rate_hz))
                    run = _run_hackrf_view
```
In the bladerf branch, update `_run_blade_view` to take `bw`:
```python
                    def _run_blade_view(freq, bw, stop, max_s):
                        _reset_bladerf_backend()
                        return stream_demod.run_stream_source(
                            viewcfg, source, freq, stop, max_s, encoder,
                            channel_of=nearest_channel,
                            lpf_cutoff_hz=_view_lpf(bw, viewcfg.view_sample_rate_hz))
                    run = _run_blade_view
```
The main loop already does `req = view.pending()` then `view.run_view(req)` — `req` is now `(freq, bw)`; `run_view` unpacks it (Task 2). No other main-loop change needed. (The persistent `else` branch that shells `run_stream_persistent` is dead for live SDRs — leave it; if a reviewer wants, it can gain a `bw` param too, but it's unreachable for hackrf/bladerf live.)

- [ ] **Step 4: Run tests**

Run: `python -m pytest agent/scan/tests/test_publisher.py agent/scan/tests/test_run_cycle.py -v`
Expected: PASS. Then `python -m pytest agent/scan/tests -q` (full scan suite) green.

- [ ] **Step 5: Commit**

```bash
git add agent/scan/main.py agent/scan/publisher.py agent/scan/tests/test_publisher.py agent/scan/tests/test_run_cycle.py
git commit -m "feat(view): main run closures clamp bw->lpf; publish_view carries bandwidth_mhz

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JLJdXSvLMBojY7w2dcUwuN"
```

---

### Task 4: Dashboard — command + reduce + card BW field

**Files:**
- Modify: `dashboard/public/mqtt-scan.js` (`buildViewCommand`, `publishView`, `reduce` view branch)
- Modify: `dashboard/public/app.js` (`onViewStart`, `viewerRowClick`)
- Modify: `dashboard/public/views/viewer.js` (BW input + starts carry bw)
- Test: `test/mqtt-scan.test.js`

**Interfaces:**
- Produces: `{view:'start', freq_mhz, bandwidth_mhz?}` command; `store[id].view.bandwidth_mhz`.

- [ ] **Step 1: Write the failing node tests**

In `test/mqtt-scan.test.js` add (mirror existing style; import `buildViewCommand`, `reduce` as already imported):
```javascript
test('buildViewCommand carries bandwidth_mhz when given, omits when not', () => {
  assert.deepEqual(buildViewCommand('start', 5865, 3), { view: 'start', freq_mhz: 5865, bandwidth_mhz: 3 });
  assert.deepEqual(buildViewCommand('start', 5865), { view: 'start', freq_mhz: 5865 });
  assert.deepEqual(buildViewCommand('start', 5865, ''), { view: 'start', freq_mhz: 5865 }); // empty -> omit
  assert.deepEqual(buildViewCommand('stop'), { view: 'stop' });
});

test('reduce view carries bandwidth_mhz (null when absent)', () => {
  const store = {};
  reduce(store, 'fpv/hackrf/view', JSON.stringify({ ts: 1, active: true, freq_mhz: 5865, stream: 'hackrf-view', bandwidth_mhz: 2.5 }));
  assert.equal(store.hackrf.view.bandwidth_mhz, 2.5);
  reduce(store, 'fpv/bladerf/view', JSON.stringify({ ts: 1, active: false, stream: 'bladerf-view' }));
  assert.equal(store.bladerf.view.bandwidth_mhz, null);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `node --test test/mqtt-scan.test.js`
Expected: FAIL (bandwidth not in cmd / view).

- [ ] **Step 3: Implement**

**mqtt-scan.js** — `buildViewCommand`:
```javascript
export function buildViewCommand(action, freqMhz, bwMhz) {
  const cmd = { view: action === 'stop' ? 'stop' : 'start' };
  if (cmd.view === 'start') {
    cmd.freq_mhz = Number(freqMhz);
    if (bwMhz != null && bwMhz !== '' && Number.isFinite(Number(bwMhz))) cmd.bandwidth_mhz = Number(bwMhz);
  }
  return cmd;
}
```
`publishView`:
```javascript
  publishView(id, action, freqMhz, bwMhz) {
    if (!this.client || !id) return;
    if (action === 'start' && !Number.isFinite(Number(freqMhz))) return;
    this.client.publish(
      `fpv/${id}/rxcmd`, JSON.stringify(buildViewCommand(action, freqMhz, bwMhz)),
      { qos: 1, retain: false },
    );
  }
```
`reduce` view branch — add `bandwidth_mhz`:
```javascript
    s.view = {
      ts: data.ts || 0,
      active: !!data.active,
      freq_mhz: data.freq_mhz == null ? null : Number(data.freq_mhz),
      until_ts: data.until_ts == null ? null : Number(data.until_ts),
      error: data.error || null,
      stream: data.stream || null,
      bandwidth_mhz: data.bandwidth_mhz == null ? null : Number(data.bandwidth_mhz),
    };
```

**app.js** — `onViewStart` + `viewerRowClick`:
```javascript
  onViewStart: (id, freq, bw) => { if (!PREVIEW) scanClient.publishView(id, 'start', freq, bw); },
```
```javascript
function viewerRowClick(freq, band, viewerId, bw) {
  if (PREVIEW) return;
  const store = scanClient.store;
  const chosen = (viewerId && store[viewerId] && store[viewerId].online && store[viewerId].view) ? viewerId : null;
  const vid = chosen || pickViewer(store);
  if (!vid || !Number.isFinite(freq) || freq < 100 || freq > 6000) return;
  scanClient.publishView(vid, 'start', freq, bw);
  if (band === '5.8G') {
    const rxId = pickRxScanner(store);
    const ch = nearestRxChannel(freq);
    if (rxId && ch) scanClient.publishCommand(rxId, { mode: 'manual', channel: ch.name });
  }
}
```

**views/viewer.js** — add the BW input + thread bw:
- In `buildCard`'s `.view-controls` markup, add after the `.vc-freq` input's `▶` stepper (before `.vc-play`):
  ```html
      <input class="vc-bw" type="number" min="0.5" max="20" step="0.5" placeholder="BW" title="смуга відео, МГц" />
  ```
- Add a `curBw()` reader in `buildCard`:
  ```javascript
  const bwInput = card.querySelector('.vc-bw');
  const curBw = () => { const v = Number(bwInput.value); return Number.isFinite(v) && v > 0 ? v : undefined; };
  ```
- Update the three start call-sites in `buildCard` to pass `curBw()`:
  - stepper: `ctx.onViewStart(c.id, f, curBw());`
  - play: `ctx.onViewStart(c.id, f, curBw());`
- In `updateCard`, reflect the active bw as the BW input's PLACEHOLDER (never its value):
  ```javascript
  const bwInput = card.querySelector('.vc-bw');
  bwInput.placeholder = (view && view.bandwidth_mhz != null) ? `BW ${view.bandwidth_mhz}` : 'BW';
  ```
- Row-button click: in `render()`'s delegated list listener, read the TARGET card's BW and pass it:
  ```javascript
      const bwEl = document.querySelector(`#viewer-card-${btn.dataset.vid} .vc-bw`);
      const bw = bwEl && bwEl.value !== '' ? Number(bwEl.value) : undefined;
      ctx.handlers.viewerRowClick(freq, btn.dataset.vwband || '', btn.dataset.vid || '', bw);
  ```

- [ ] **Step 4: Run tests + syntax check**

Run: `node --check dashboard/public/mqtt-scan.js && node --check dashboard/public/app.js && node --check dashboard/public/views/viewer.js && npm test`
Expected: no `--check` output; `npm test` PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/public/mqtt-scan.js dashboard/public/app.js dashboard/public/views/viewer.js test/mqtt-scan.test.js
git commit -m "feat(view): dashboard BW field + bandwidth_mhz in view command/state

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JLJdXSvLMBojY7w2dcUwuN"
```

---

### Task 5: Fixtures + full regression + visual gate

**Files:**
- Modify: `dashboard/public/fixtures.js` (bladerf view fixture gains `bandwidth_mhz`)
- Modify: `dashboard/public/styles.css` (BW input width, if needed)

- [ ] **Step 1: Fixtures**

In `dashboard/public/fixtures.js`, the `bladerf` `view` fixture: add `bandwidth_mhz: 3` so the active card's BW placeholder shows it:
```javascript
    view: { ts: NOW, active: true, freq_mhz: 5800, until_ts: NOW+600, error: null, stream: 'bladerf-view', bandwidth_mhz: 3 },
```

- [ ] **Step 2: CSS (optional)**

If the BW input crowds the controls, append to `styles.css`:
```css
.viewer-card .vc-bw { width: 68px; }
```

- [ ] **Step 3: Syntax + full regression**

Run: `node --check dashboard/public/fixtures.js && npm test && python -m pytest agent/scan/tests -q && python -m pytest agent/video/tests -q`
Expected: all green.

- [ ] **Step 4: Visual gate (dev-preview) — controller-run**

Start a FRESH dev-serve on a new port (avoids browser module cache): `DEV_PORT=8093 node dashboard/dev-serve.mjs &`. Load `http://127.0.0.1:8093/index.html?preview=1#/viewer` and verify:
1. Each card shows a **BW** input next to the freq controls; the bladeRF (active) card's BW input placeholder reads `BW 3`.
2. Reconcile-safety: type `2` into a card's BW input, `window.__rerender()` twice → the typed `2` survives and the `<video>` node is unchanged.
3. Command shape: stub `window.__store`/intercept — or in preview, confirm the play button calls `onViewStart(id, freq, bw)` with the BW (preview no-ops the publish; verify via a temporary console.log or by reading that `curBw()` returns the typed value). At minimum confirm no console errors and the BW field is wired into the same control row.
Record results in the report.

- [ ] **Step 5: Commit**

```bash
git add dashboard/public/fixtures.js dashboard/public/styles.css
git commit -m "feat(view): preview fixture bandwidth + BW input styling

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JLJdXSvLMBojY7w2dcUwuN"
```

---

## Deploy (after merge)
- Pi (`rpi-4` @192.168.1.204, key fpv_deploy): `cd /opt/fpv-video-stream && sudo git pull && sudo systemctl restart fpv-scan fpv-scan-hackrf`.
- Server `traefik`: `cd ~/fpv-video-stream && git pull && sudo docker compose build dashboard && sudo docker compose up -d --no-deps dashboard` (wg-easy/mediamtx/mosquitto untouched).
- Over-WG acceptance: start a view, change BW (e.g. 4 → 1.5) → picture detail/noise changes live on both SDRs.

## Self-review (spec coverage)
- ✅ BW = demod lpf, clamp [0.5, fs/2] (Task 3 `_view_lpf` + Task 1 override).
- ✅ Threaded through view command → ViewController → run_stream_source (Tasks 2/3/1), both SDRs.
- ✅ `publish_view` echoes bandwidth_mhz; reduce → store; UI shows it (Tasks 3/4).
- ✅ Per-card BW input; starts (play/stepper/row-button) carry the card's BW (Task 4).
- ✅ Reconcile-safety: BW input placeholder only, never value (Task 4 + Task 5 gate).
- ✅ No new topics/ACL; sample_rate fixed (v1 boundary).
- ✅ pytest + npm green each task; visual gate (Task 5).
