# RX5808 Dashboard Control (mode + channel from the UI) — Design Spec

**Date:** 2026-06-18
**Author:** andriy@netlife.com.ua
**Status:** Approved (pending written-spec review)

## 1. Context & Goal

The RX5808 controller auto-tunes (carrier-targeting). The operator now wants to **drive it from the
dashboard**: pick an operating mode and, in manual mode, a specific channel — including by **clicking
the 5.8 spectrum** to tune to that frequency.

Today there is no downlink: the Pi only publishes `fpv/<id>/rxtune`; the dashboard browser talks MQTT
directly over WSS as the read-only `sub` user (ACL: `sub` reads `fpv/#`, `pub` writes `fpv/#`).
`server.js` has no MQTT client. This adds a **command path dashboard → Pi** and the controller modes.

## 2. Confirmed decisions

| Decision | Value |
|---|---|
| Downlink | **Browser publishes directly** to a retained `fpv/<id>/rxcmd` over the existing WSS connection. ACL gains `sub` write + `pub` read on `fpv/+/rxcmd`. No server-side MQTT, no new npm dep. |
| Modes | **`auto`** (default — the current carrier-targeting), **`scan`** (all 40 sequential), **`random`** (all 40 random), **`manual`** (a specific channel). |
| Manual input | A **channel dropdown** (A1…R8 + MHz) **and** **click on the 5.8 spectrum** → tune the nearest RX5808 channel at the clicked frequency. |
| `rxtune.mode` | Now reports the **operating mode** (`auto`/`scan`/`random`/`manual`) instead of the old `detected`/`scan`. |
| Persistence | `rxcmd` retained — the last command survives a Pi restart; the Pi applies it on (re)connect. |
| Auth | The dashboard login + WG-only access gate control; no extra command auth. |

## 3. Architecture & data flow

```
Dashboard (browser)                          Broker (mosquitto)                 Pi (scan service)
  click [Auto|Scan|Random|Manual]                                                 MqttPublisher
  or channel <select> or 5.8-chart click   ──WSS publish (sub user)──▶ fpv/<id>/rxcmd (retained)
        scanClient.publishCommand(id, {mode, channel})                              │ subscribed (pub reads)
                                                                                    ▼ _on_message (guarded)
                                                                      controller.set_command(mode, channel)
                                                                                    │ (under lock)
  rxtune marker + mode caption  ◀──── fpv/<id>/rxtune {..., mode} ◀──── Rx5808Controller._next() per mode
        (existing)                                                       auto|scan|random|manual -> tune
```

The browser command-publish reuses the existing `window.mqtt` WSS client; the Pi command-subscribe
reuses the existing `MqttPublisher` paho client/loop. No new connections, no server changes.

## 4. Components / deliverables

```
mosquitto/acl                          (change: sub +write fpv/+/rxcmd; pub +read fpv/+/rxcmd)
agent/scan/rx5808_controller.py        (change: mode + manual_channel; set_command; _next per mode; rng inject)
agent/scan/publisher.py                (change: subscribe fpv/<id>/rxcmd on connect; _on_message -> on_command)
agent/scan/main.py                     (change: wire publisher.on_command = controller.set_command)
agent/scan/tests/test_rx5808_controller.py (change: per-mode _next + set_command)
agent/scan/tests/test_publisher.py     (change: command subscribe + dispatch)
dashboard/public/rx5808-channels.js    (new: RX5808_CHANNELS + nearestRxChannel — JS mirror of rx5808.py)
dashboard/public/mqtt-scan.js          (change: buildCommand + MqttScanClient.publishCommand)
dashboard/public/spectrum.js           (change: control row (mode buttons + channel select) in scannerBlock; mark the 5.8 chart canvas tunable)
dashboard/public/app.js                (change: delegated clicks -> publishCommand; 5.8-chart click -> nearest channel)
dashboard/public/styles.css            (change: control-row styles)
test/rx5808-channels.test.js           (new: nearestRxChannel)
test/mqtt-scan.test.js                 (change: buildCommand)
```

### 4.1 ACL (`mosquitto/acl`)
```
user pub
topic write fpv/#
topic read fpv/+/rxcmd          # Pi receives commands

user sub
topic read fpv/#
topic write fpv/+/rxcmd         # dashboard sends commands
```

### 4.2 `Rx5808Controller`
- New: `self._mode = "auto"`, `self._manual = None` (a `(name, freq)`), `self._rng` (injectable
  `random.Random`, default `random.Random()`).
- `set_command(self, mode, channel=None)` — under lock: validate `mode in {auto,scan,random,manual}`
  (ignore unknown); for `manual`, resolve `channel` (name) → `(name, freq)` via `RX5808_CHANNELS`
  (ignore unknown channel, keep previous). Log the applied command.
- `_next(self)` — under lock, branch on `self._mode`:
  - `auto`: existing behavior (`self._targets or self._channels`, sequential), `mode="auto"`.
  - `scan`: `self._channels` sequential, `mode="scan"`.
  - `random`: `self._rng.choice(self._channels)`, `mode="random"`.
  - `manual`: the fixed `self._manual` (fallback to `_channels[0]` if unset), `mode="manual"`.
  Returns `(name, freq, mode, target_freqs)`; `rxtune.mode` is now this operating mode.
- `update_targets` unchanged (still feeds `auto`).

### 4.3 `MqttPublisher`
- `__init__`: `self._t_rxcmd = f"fpv/{scanner_id}/rxcmd"`, `self.on_command = None`.
- `connect`: after `loop_start`, the connect callback (`_on_connect`) also `client.subscribe(self._t_rxcmd)`.
- `client.on_message = self._on_message`; `_on_message(client, userdata, msg)` parses JSON (guarded),
  and if `self.on_command`: `self.on_command(data.get("mode"), data.get("channel"))`. Never raises.

### 4.4 `main()`
After building both: `if controller is not None and publisher is not None: publisher.on_command =
controller.set_command`.

### 4.5 Dashboard
- `rx5808-channels.js`: `export const RX5808_CHANNELS = [{name, freq}, …]` (the 40 A/B/E/F/R) +
  `export function nearestRxChannel(mhz, tol=10)` → `{name, freq}` or null. Mirrors `rx5808.py`.
- `mqtt-scan.js`: `export function buildCommand(mode, channel)` → `{mode, channel: channel || null}`
  (pure); `MqttScanClient.publishCommand(id, cmd)` → `this.client.publish('fpv/'+id+'/rxcmd',
  JSON.stringify(buildCommand(cmd.mode, cmd.channel)), {qos: 1, retain: true})`.
- `spectrum.js` `scannerBlock`: a `div.rx5808-ctl` row with four mode buttons
  (`data-rxmode=auto|scan|random|manual`, the active one from `live.rxtune?.mode` highlighted) and a
  `<select.rx5808-ch>` of `RX5808_CHANNELS`. The 5.8 `chart-line` canvas gets
  `class="chart-line tunable"` + `dataset.lowMhz/highMhz` so a click maps to a frequency.
- `app.js` `spectrumPanel` delegated click handler (and the select `change`): a mode button →
  `publishCommand(id, {mode})`; the select → `publishCommand(id, {mode:'manual', channel})`; a click on
  a `.tunable` canvas → `freq = low + (offsetX/width)*(high-low)`, `ch = nearestRxChannel(freq)`, then
  `publishCommand(id, {mode:'manual', channel: ch.name})`. `id` from the enclosing `[data-scanner-id]`.

## 5. MQTT contract

- **Command** `fpv/<id>/rxcmd` (QoS1, retained): `{ "mode": "auto|scan|random|manual", "channel": "A1"|null }`.
- **State** `fpv/<id>/rxtune` (unchanged shape) — `mode` now carries the operating mode
  (`auto`/`scan`/`random`/`manual`).

## 6. Error handling & resilience

- `_on_message` is fully guarded (bad JSON / missing fields / unknown mode or channel → ignored); a
  command error never disturbs the publish loop. `set_command` validates and keeps prior state on bad
  input. The controller thread keeps hopping regardless.
- The browser publish is best-effort; a missing `window.mqtt`/disconnected client is a no-op. The
  retained command means a Pi reconnect re-applies the last mode.
- Unknown manual channel → controller keeps the previous channel (no crash, no silent jump).

## 7. Testing / verification

- **`test_rx5808_controller.py`:** `set_command("scan")` → `_next` cycles all 40 with `mode=scan`;
  `"random"` with an injected `rng` → deterministic pick, `mode=random`; `"manual","A1"` → holds A1,
  `mode=manual`; `"auto"` → existing targets/scan behavior, `mode=auto`; unknown mode/channel ignored.
- **`test_publisher.py`:** on connect the client subscribes to `fpv/<id>/rxcmd`; `_on_message` with a
  JSON command calls `on_command(mode, channel)`; malformed payload is swallowed.
- **`test/rx5808-channels.test.js`:** `nearestRxChannel(5865.3)` → A1; out-of-tol → null; table length 40.
- **`test/mqtt-scan.test.js`:** `buildCommand("manual","A1")` and `buildCommand("scan")` shapes.
- **Browser:** `node --check` on the changed JS.
- Run: `cd agent/scan && python -m pytest tests ../video/tests -q` and `npm test`.
- **Live (after deploy):** dashboard mode buttons / channel select / 5.8-chart click publish
  `fpv/<id>/rxcmd`; the Pi log shows the applied command; `fpv/<id>/rxtune.mode` flips accordingly;
  the RX5808 (grabber) follows; the marker + mode caption update.

## 8. Out of scope

- Server-side MQTT / a REST command endpoint (browser-direct chosen).
- Command auth beyond the dashboard login + WG.
- Controlling non-RX5808 hardware; changing the main detection or the carrier-targeting feed.
