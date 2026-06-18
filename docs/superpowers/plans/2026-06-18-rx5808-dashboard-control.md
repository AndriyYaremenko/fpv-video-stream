# RX5808 Dashboard Control — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the operator drive the RX5808 from the dashboard — pick a mode (auto/scan/random/manual) and, in manual, a channel via dropdown or by clicking the 5.8 spectrum — by publishing a retained `fpv/<id>/rxcmd` the Pi controller applies.

**Architecture:** The browser publishes `rxcmd` over its existing WSS MQTT connection (ACL widened); the Pi `MqttPublisher` subscribes and dispatches to `Rx5808Controller.set_command`, which adds modes. The dashboard gets a control row + a JS channel table for the dropdown and the spectrum-click mapping.

**Tech Stack:** Python (numpy/pytest), plain ES modules + `node --test`. Pi tests: `agent/scan/.venv/Scripts/python.exe` from `agent/scan`; JS tests: `npm test` from repo root.

**Reference spec:** `docs/superpowers/specs/2026-06-18-rx5808-dashboard-control-design.md`

---

### Task 1: ACL — allow the command topic

**Files:**
- Modify: `mosquitto/acl`

- [ ] **Step 1: Edit `mosquitto/acl`** to its full new content (adds command read/write):

```
# mosquitto/acl — pub publishes scan data, sub reads it.
# The usernames below are LITERAL: they must match MQTT_PUB_USER/MQTT_SUB_USER in .env
# (default pub/sub). Renaming a user in .env without editing here = authenticated but all
# topics denied. Edit both together.
user pub
topic write fpv/#
topic read fpv/+/rxcmd

user sub
topic read fpv/#
topic write fpv/+/rxcmd
```

- [ ] **Step 2: Sanity check** (no test harness for the ACL — verify the literal content):

Run: `grep -c rxcmd mosquitto/acl`
Expected: `2`.

- [ ] **Step 3: Commit**

```bash
git add mosquitto/acl
git commit -m "feat(rx5808): ACL — pub reads + sub writes fpv/+/rxcmd"
```

---

### Task 2: Controller modes + `set_command`

**Files:**
- Modify: `agent/scan/rx5808_controller.py`
- Modify: `agent/scan/tests/test_rx5808_controller.py`

- [ ] **Step 1: Update + add tests** in `agent/scan/tests/test_rx5808_controller.py`.

Replace the bodies of the three mode-asserting tests and add new ones. First, change `_ctrl` to allow
an injected rng (replace the existing `_ctrl` helper):

```python
import random


def _ctrl(pub, backend=None, rng=None):
    return RC.Rx5808Controller(
        backend or FakeBackend(), pub, "hackrf", RX5808_CHANNELS,
        dwell_s=0, settle_ms=0, clock=lambda: 1000, sleep=lambda s: None, rng=rng,
    )
```

Replace `test_scan_mode_cycles_all_channels_in_order`, `test_detected_mode_round_robins_targets`,
and `test_out_of_range_targets_fall_back_to_scan` with these (auto is now the default mode and its
`rxtune.mode` is `"auto"`):

```python
def test_auto_mode_cycles_all_channels(tmp_path=None):
    pub = FakePub(); c = _ctrl(pub)
    for _ in range(3):
        c.run_once()
    assert [t[2] for t in pub.tunes] == ["auto", "auto", "auto"]
    assert [t[0] for t in pub.tunes] == [f for _, f in RX5808_CHANNELS[:3]]


def test_auto_mode_round_robins_targets():
    pub = FakePub(); c = _ctrl(pub)
    c.update_targets([5865.3, 5800.0])
    for _ in range(4):
        c.run_once()
    assert [t[2] for t in pub.tunes] == ["auto"] * 4
    assert [t[0] for t in pub.tunes] == [5865, 5800, 5865, 5800]


def test_auto_mode_out_of_range_targets_cycle_all():
    pub = FakePub(); c = _ctrl(pub)
    c.update_targets([5500.0])           # no channels -> auto cycles all 40
    c.run_once()
    assert pub.tunes[0][2] == "auto"
```

Add new mode tests at the end of the file:

```python
def test_set_command_scan_cycles_all():
    pub = FakePub(); c = _ctrl(pub)
    c.update_targets([5865.0])           # has a target, but scan ignores it
    c.set_command("scan")
    for _ in range(2):
        c.run_once()
    assert [t[2] for t in pub.tunes] == ["scan", "scan"]
    assert [t[0] for t in pub.tunes] == [f for _, f in RX5808_CHANNELS[:2]]


def test_set_command_random_uses_injected_rng():
    pub = FakePub(); c = _ctrl(pub, rng=random.Random(0))
    c.set_command("random")
    c.run_once()
    expected = random.Random(0).choice(RX5808_CHANNELS)
    assert pub.tunes[0][0] == expected[1]
    assert pub.tunes[0][2] == "random"


def test_set_command_manual_holds_channel():
    pub = FakePub(); c = _ctrl(pub)
    c.set_command("manual", "A1")
    for _ in range(2):
        c.run_once()
    assert [t[0] for t in pub.tunes] == [5865, 5865]    # A1, held
    assert [t[1] for t in pub.tunes] == ["A1", "A1"]
    assert [t[2] for t in pub.tunes] == ["manual", "manual"]


def test_set_command_unknown_mode_ignored():
    pub = FakePub(); c = _ctrl(pub)
    c.set_command("bogus")
    c.run_once()
    assert pub.tunes[0][2] == "auto"     # unchanged
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `cd agent/scan && ./.venv/Scripts/python.exe -m pytest tests/test_rx5808_controller.py -q`
Expected: FAIL (`Rx5808Controller` has no `rng` kwarg / no `set_command`; auto mode label differs).

- [ ] **Step 3: Implement** — in `agent/scan/rx5808_controller.py`.

Add `import random` at the top (with the other imports). Change `__init__` to accept `rng` and add the
mode/manual state — replace the `__init__` signature line and the state init:

```python
    def __init__(self, backend, publisher, scanner_id, channels, dwell_s,
                 settle_ms=35, clock=None, sleep=None, rng=None):
        self.backend = backend
        self.publisher = publisher
        self.scanner_id = scanner_id
        self._channels = list(channels)
        self.dwell_s = dwell_s
        self.settle_ms = settle_ms
        self._clock = clock or time.time
        self._sleep = sleep or time.sleep
        self._rng = rng or random.Random()
        self._targets = []                       # [(name, freq)]
        self._mode = "auto"                      # auto | scan | random | manual
        self._manual = None                      # (name, freq) for manual mode
        self._lock = threading.Lock()
        self._idx = -1
        self._stop = threading.Event()
        self._thread = None
```

Add the `set_command` method (after `update_targets`):

```python
    def set_command(self, mode, channel=None):
        if mode not in ("auto", "scan", "random", "manual"):
            LOG.warning("rx5808 ignoring unknown mode %r", mode)
            return
        resolved = None
        if mode == "manual":
            resolved = next(((n, f) for n, f in self._channels if n == channel), None)
            if resolved is None:
                LOG.warning("rx5808 manual: unknown channel %r, keeping previous", channel)
        with self._lock:
            self._mode = mode
            if resolved is not None:
                self._manual = resolved
        LOG.info("rx5808 command applied: mode=%s channel=%s", mode, channel)
```

Replace the whole `_next` method:

```python
    def _next(self):
        with self._lock:
            mode = self._mode
            if mode == "manual":
                name, freq = self._manual or self._channels[0]
                target_freqs = []
            elif mode == "random":
                name, freq = self._rng.choice(self._channels)
                target_freqs = []
            elif mode == "scan":
                self._idx = (self._idx + 1) % len(self._channels)
                name, freq = self._channels[self._idx]
                target_freqs = []
            else:                                # auto
                lst = self._targets or self._channels
                self._idx = (self._idx + 1) % len(lst)
                name, freq = lst[self._idx]
                target_freqs = [f for _, f in self._targets]
                mode = "auto"
        return name, freq, mode, target_freqs
```

- [ ] **Step 4: Run them to confirm they pass**

Run: `cd agent/scan && ./.venv/Scripts/python.exe -m pytest tests/test_rx5808_controller.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/scan/rx5808_controller.py agent/scan/tests/test_rx5808_controller.py
git commit -m "feat(rx5808): controller modes (auto/scan/random/manual) + set_command"
```

---

### Task 3: Publisher command subscription + `main()` wiring

**Files:**
- Modify: `agent/scan/publisher.py`
- Modify: `agent/scan/main.py`
- Modify: `agent/scan/tests/test_publisher.py`

- [ ] **Step 1: Write the failing test** — append to `agent/scan/tests/test_publisher.py`.

First, add a `subscribe` recorder + `on_message` slot to the existing `FakeClient` class (add these
two methods inside `FakeClient`):

```python
    def subscribe(self, topic, *a, **k):
        self.subscribed = getattr(self, "subscribed", [])
        self.subscribed.append(topic)
```

Then append the tests:

```python
class _Msg:
    def __init__(self, payload):
        self.payload = payload


def test_connect_subscribes_to_command_topic():
    fake = FakeClient()
    p = _pub(fake); p.connect(ts=1)
    p._on_connect(fake, None, None, 0)
    assert "fpv/hackrf/rxcmd" in getattr(fake, "subscribed", [])


def test_on_message_dispatches_command():
    p = publisher.MqttPublisher("h", 1, "u", "p", "hackrf")
    got = []
    p.on_command = lambda mode, channel: got.append((mode, channel))
    p._on_message(None, None, _Msg(json.dumps({"mode": "manual", "channel": "A1"})))
    assert got == [("manual", "A1")]


def test_on_message_swallows_bad_payload():
    p = publisher.MqttPublisher("h", 1, "u", "p", "hackrf")
    p.on_command = lambda mode, channel: (_ for _ in ()).throw(AssertionError("should not be called"))
    p._on_message(None, None, _Msg(b"{not json"))      # must not raise / not dispatch
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `cd agent/scan && ./.venv/Scripts/python.exe -m pytest tests/test_publisher.py -q`
Expected: FAIL (no `_on_message`; `_on_connect` doesn't subscribe).

- [ ] **Step 3: Implement** — in `agent/scan/publisher.py`.

In `__init__`, add the command topic + handler slot (next to `self._t_rxtune`):

```python
        self._t_rxcmd = f"fpv/{scanner_id}/rxcmd"
        self.on_command = None
```

In `connect`, set the message callback (add after `client.on_connect = self._on_connect`):

```python
        client.on_message = self._on_message
```

In `_on_connect`, subscribe to the command topic (add after the status publish, inside the `try`,
so a reconnect re-subscribes):

```python
            client.subscribe(self._t_rxcmd)
```

Add the message handler (after `_on_connect`):

```python
    def _on_message(self, client, userdata, msg, *args):
        try:
            data = json.loads(msg.payload)
        except Exception:
            return
        if not isinstance(data, dict) or self.on_command is None:
            return
        try:
            self.on_command(data.get("mode"), data.get("channel"))
        except Exception:
            LOG.exception("on_command handler failed")
```

- [ ] **Step 4: Wire `main()`** — in `agent/scan/main.py`, right after the `controller` build block
(after its `except Exception: LOG.exception("rx5808 controller init failed; continuing without it")`),
add:

```python
    if controller is not None and publisher is not None:
        # Apply dashboard commands (fpv/<id>/rxcmd) to the controller. Set after connect; the
        # retained command's async delivery arrives after this synchronous assignment.
        publisher.on_command = controller.set_command
```

- [ ] **Step 5: Run the publisher suite**

Run: `cd agent/scan && ./.venv/Scripts/python.exe -m pytest tests/test_publisher.py -q`
Expected: PASS.

- [ ] **Step 6: Full scan + video suite (no regressions)**

Run: `cd agent/scan && ./.venv/Scripts/python.exe -m pytest tests ../video/tests -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add agent/scan/publisher.py agent/scan/main.py agent/scan/tests/test_publisher.py
git commit -m "feat(rx5808): MqttPublisher subscribes fpv/<id>/rxcmd -> controller.set_command"
```

---

### Task 4: `rx5808-channels.js` — JS channel table

**Files:**
- Create: `dashboard/public/rx5808-channels.js`
- Test: `test/rx5808-channels.test.js`

- [ ] **Step 1: Write the failing test** (`test/rx5808-channels.test.js`)

```javascript
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { RX5808_CHANNELS, nearestRxChannel } from '../dashboard/public/rx5808-channels.js';

test('channel table has 40 entries including A1 and R8', () => {
  assert.equal(RX5808_CHANNELS.length, 40);
  assert.deepEqual(RX5808_CHANNELS[0], { name: 'A1', freq: 5865 });
  assert.ok(RX5808_CHANNELS.some((c) => c.name === 'R8' && c.freq === 5917));
});

test('nearestRxChannel snaps to the closest channel within tolerance', () => {
  assert.deepEqual(nearestRxChannel(5865.3), { name: 'A1', freq: 5865 });
  assert.deepEqual(nearestRxChannel(5800.0), { name: 'F4', freq: 5800 });
  assert.equal(nearestRxChannel(5500.0), null);
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `npm test`
Expected: FAIL (cannot find `rx5808-channels.js`).

- [ ] **Step 3: Implement** (`dashboard/public/rx5808-channels.js`)

```javascript
// 40 standard 5.8 GHz FPV channels (Band A/B/E/F/R) — JS mirror of agent/scan/rx5808.py.
const _BANDS = {
  A: [5865, 5845, 5825, 5805, 5785, 5765, 5745, 5725],
  B: [5733, 5752, 5771, 5790, 5809, 5828, 5847, 5866],
  E: [5705, 5685, 5665, 5645, 5885, 5905, 5925, 5945],
  F: [5740, 5760, 5780, 5800, 5820, 5840, 5860, 5880],
  R: [5658, 5695, 5732, 5769, 5806, 5843, 5880, 5917],
};

export const RX5808_CHANNELS = Object.entries(_BANDS).flatMap(
  ([b, fs]) => fs.map((f, i) => ({ name: `${b}${i + 1}`, freq: f })),
);

// Nearest channel to a frequency (MHz) within tol; null if none. First wins on ties.
export function nearestRxChannel(mhz, tol = 10) {
  let best = null;
  let bestD = tol + 1e-9;
  for (const ch of RX5808_CHANNELS) {
    const d = Math.abs(ch.freq - mhz);
    if (d < bestD) { bestD = d; best = ch; }
  }
  return best;
}
```

- [ ] **Step 4: Run it to confirm it passes**

Run: `npm test` && `node --check dashboard/public/rx5808-channels.js`
Expected: PASS, then no output.

- [ ] **Step 5: Commit**

```bash
git add dashboard/public/rx5808-channels.js test/rx5808-channels.test.js
git commit -m "feat(dashboard): RX5808 channel table + nearestRxChannel (JS mirror)"
```

---

### Task 5: `mqtt-scan.js` — `buildCommand` + `publishCommand`

**Files:**
- Modify: `dashboard/public/mqtt-scan.js`
- Test: `test/mqtt-scan.test.js`

- [ ] **Step 1: Write the failing test** — append to `test/mqtt-scan.test.js`:

```javascript
import { buildCommand } from '../dashboard/public/mqtt-scan.js';

test('buildCommand shapes mode + channel', () => {
  assert.deepEqual(buildCommand('manual', 'A1'), { mode: 'manual', channel: 'A1' });
  assert.deepEqual(buildCommand('scan'), { mode: 'scan', channel: null });
});
```

(If `mqtt-scan.test.js` already imports from `mqtt-scan.js`, add `buildCommand` to that import line.)

- [ ] **Step 2: Run it to confirm it fails**

Run: `npm test`
Expected: FAIL (`buildCommand` is not exported).

- [ ] **Step 3: Implement** — in `dashboard/public/mqtt-scan.js`.

Add the pure builder (near the top, after `emptyStore`):

```javascript
// Build an RX5808 command payload for fpv/<id>/rxcmd.
export function buildCommand(mode, channel) {
  return { mode, channel: channel || null };
}
```

Add a publish method to `MqttScanClient` (after `connect`):

```javascript
  publishCommand(id, cmd) {
    if (!this.client || !id || !cmd || !cmd.mode) return;
    this.client.publish(
      `fpv/${id}/rxcmd`, JSON.stringify(buildCommand(cmd.mode, cmd.channel)),
      { qos: 1, retain: true },
    );
  }
```

- [ ] **Step 4: Run it to confirm it passes**

Run: `npm test` && `node --check dashboard/public/mqtt-scan.js`
Expected: PASS, then no output.

- [ ] **Step 5: Commit**

```bash
git add dashboard/public/mqtt-scan.js test/mqtt-scan.test.js
git commit -m "feat(dashboard): buildCommand + MqttScanClient.publishCommand"
```

---

### Task 6: `spectrum.js` — control row + tunable 5.8 chart

**Files:**
- Modify: `dashboard/public/spectrum.js`
- Modify: `dashboard/public/styles.css`

- [ ] **Step 1: Implement the control row** — in `dashboard/public/spectrum.js`.

Add the import at the top (after the existing `import { detectionKey } ...`):

```javascript
import { RX5808_CHANNELS } from './rx5808-channels.js';
```

Add a control-row builder (near `rxtuneCaption`):

```javascript
// RX5808 control row: mode buttons + channel <select>. activeMode highlights the live mode.
function rx5808Controls(activeMode) {
  const row = el('div', 'rx5808-ctl');
  for (const m of ['auto', 'scan', 'random', 'manual']) {
    const b = el('button', `rx-mode${m === activeMode ? ' active' : ''}`, m);
    b.dataset.rxmode = m;
    row.appendChild(b);
  }
  const sel = el('select', 'rx5808-ch');
  for (const ch of RX5808_CHANNELS) {
    const o = document.createElement('option');
    o.value = ch.name; o.textContent = `${ch.name} · ${ch.freq}`;
    sel.appendChild(o);
  }
  row.appendChild(sel);
  return row;
}
```

In `scannerBlock`, append the control row right after the rxtune caption block (after the
`block.appendChild(el('div', 'scan-rxtune', ...))` `if`):

```javascript
  block.appendChild(rx5808Controls(live && live.rxtune ? live.rxtune.mode : null));
```

- [ ] **Step 2: Mark the 5.8 chart tunable** — in `dashboard/public/spectrum.js`.

Change the `bandCell` call inside `scannerBlock` to pass a `tunable` flag for the 5.8 band:

```javascript
    charts.appendChild(bandCell(band, range, psd, frames, dets, rxFreq, band === '5.8G'));
```

Change `bandCell`'s signature and, after `const lc = line.getContext('2d');`, tag the canvas:

```javascript
function bandCell(band, range, psd, frames, dets, rxFreq, tunable) {
```

```javascript
  if (tunable && range.low_mhz != null) {
    line.classList.add('tunable');
    line.dataset.lowMhz = range.low_mhz;
    line.dataset.highMhz = range.high_mhz;
  }
```

(Place that block right after `wrap.appendChild(el('div', 'band-label', escapeHtml(band)));` and the
`line` canvas is created — i.e., after `line.className = 'chart-line';` and before drawing. Use
`line.classList.add` so the existing class stays.)

- [ ] **Step 3: Add styles** — append to `dashboard/public/styles.css`:

```css
.rx5808-ctl { display:flex; gap:.3rem; align-items:center; margin:.35rem 0; flex-wrap:wrap; }
.rx5808-ctl .rx-mode { font-size:.75rem; padding:.15rem .5rem; background:#0c1118; color:#9aa4b2; border:1px solid var(--line); border-radius:4px; cursor:pointer; }
.rx5808-ctl .rx-mode.active { color:#39d0ff; border-color:#39d0ff; }
.rx5808-ctl .rx5808-ch { font-size:.75rem; background:#0c1118; color:#cfd6e0; border:1px solid var(--line); border-radius:4px; }
.band-cell .chart-line.tunable { cursor:crosshair; }
```

- [ ] **Step 4: Validate it parses**

Run: `node --check dashboard/public/spectrum.js && npm test`
Expected: no output from `node --check`, then JS suite green (spectrum pure-helper tests unaffected).

- [ ] **Step 5: Commit**

```bash
git add dashboard/public/spectrum.js dashboard/public/styles.css
git commit -m "feat(dashboard): RX5808 control row + tunable 5.8 chart"
```

---

### Task 7: `app.js` — wire control clicks

**Files:**
- Modify: `dashboard/public/app.js`

- [ ] **Step 1: Import the channel helper** — in `dashboard/public/app.js`, add to the imports:

```javascript
import { nearestRxChannel } from '/rx5808-channels.js';
```

- [ ] **Step 2: Handle mode buttons + spectrum click** — in `app.js`, at the **start** of the
`spectrumPanel` click listener (before the existing `.scan-frame` / `button[data-act]` handling), add:

```javascript
  const block = e.target.closest('[data-scanner-id]');
  const sid = block ? block.dataset.scannerId : null;

  const modeBtn = e.target.closest('[data-rxmode]');
  if (modeBtn && sid) {
    scanClient.publishCommand(sid, { mode: modeBtn.dataset.rxmode });
    return;
  }

  const canvas = e.target.closest('canvas.tunable');
  if (canvas && sid) {
    const rect = canvas.getBoundingClientRect();
    const lo = Number(canvas.dataset.lowMhz);
    const hi = Number(canvas.dataset.highMhz);
    const freq = lo + (Math.max(0, e.clientX - rect.left) / rect.width) * (hi - lo);
    const ch = nearestRxChannel(freq);
    if (ch) scanClient.publishCommand(sid, { mode: 'manual', channel: ch.name });
    return;
  }
```

- [ ] **Step 3: Handle the channel select** — in `app.js`, add a `change` listener next to the
`spectrumPanel` click listener (e.g., right after it):

```javascript
spectrumPanel.addEventListener('change', (e) => {
  const sel = e.target.closest('select.rx5808-ch');
  if (!sel) return;
  const block = sel.closest('[data-scanner-id]');
  if (block) scanClient.publishCommand(block.dataset.scannerId, { mode: 'manual', channel: sel.value });
});
```

- [ ] **Step 4: Validate it parses**

Run: `node --check dashboard/public/app.js && npm test`
Expected: no output from `node --check`, then JS suite green.

- [ ] **Step 5: Commit**

```bash
git add dashboard/public/app.js
git commit -m "feat(dashboard): wire RX5808 mode buttons, channel select, spectrum-click"
```

---

## Self-Review notes (for the implementer)

- **Spec coverage:** Task 1 = ACL (§4.1); Task 2 = controller modes + set_command (§4.2); Task 3 =
  publisher subscribe/dispatch + main wiring (§4.3, §4.4); Task 4 = JS channel table (§4.5); Task 5 =
  buildCommand/publishCommand (§4.5); Task 6 = control row + tunable chart (§4.5); Task 7 = click
  wiring incl. spectrum-click (§4.5). MQTT contract (§5) by Tasks 2/3/5; resilience (§6) by the guards
  in Tasks 2/3.
- **Commit trailers:** append to every commit:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01Fr3LCjweDyLf1WRPz9PNUX`.
- **Not unit-tested (browser DOM):** `rx5808Controls`, the click/`change` wiring, the tunable canvas —
  `node --check` only; validated live on the dashboard after deploy.
- **Deploy:** ACL → re-publish the mosquitto `acl` file into the broker container + reload (or restart
  the mosquitto container); Pi `git pull` + restart fpv-scan; dashboard surgical Docker rebuild.
```
