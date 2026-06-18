# RX5808 Auto-Tune Controller (detection-driven channel hopping) — Design Spec

**Date:** 2026-06-18
**Author:** andriy@netlife.com.ua
**Status:** Approved (pending written-spec review)

## 1. Context & Goal

The HackRF scan service detects analog FPV activity but can't recover usable video from IQ (the
reconstruction returns `not_video` on real signals). Instead, the Pi will drive a real **RX5808**
analog 5.8 GHz receiver over 3-wire bit-bang SPI; its composite-video output already goes to a USB
grabber that streams to the server (MediaMTX). This feature makes the Pi **tune the RX5808 channel
automatically**:

- **When analog signals are detected on 5.8** (by the HackRF scan): cycle (round-robin) through the
  detected channels, dwelling a few seconds on each, and publish the currently-tuned frequency so
  the dashboard highlights it.
- **When nothing is detected**: scan **all** standard 5.8 channels (A/B/E/F/R), dwelling a few
  seconds each, hunting for video.

The RX5808 protocol is already implemented for ESP (`esp_ino/rx5808_esp8266_scanner/…ino`): write to
synthesizer register `0x1`, a 25-bit LSB-first word, frequency encoded as
`tf=(f−479)/2; N=tf/32; A=tf%32; reg=(N<<7)|(A&0x7F)`. This spec ports that to the Pi.

**Verified on the Pi (`fvp-01`, Raspberry Pi 3B):** SPI/I2C disabled, header GPIO free; no Python
GPIO lib in the scan venv but `python3-lgpio` is in apt; `/dev/gpiochip0` present. Backend = **lgpio**.

## 2. Confirmed decisions

| Decision | Value |
|---|---|
| Wiring | Bit-bang 3 lines: **CLK=GPIO5 (pin29), DATA=GPIO6 (pin31), LE=GPIO13 (pin33)**, GND, RX5808 powered at 5V (ideally off a powered hub — see §6). 3.3V logic, no level shifter. RSSI not wired (HackRF senses; Pi has no ADC). |
| Detected mode | >0 analog 5.8 detections → **round-robin** through the detected channels, `dwell_s` each. |
| Scan mode | 0 detections → round-robin through **all 40** standard 5.8 channels, `dwell_s` each. |
| Hop driver | A **dedicated daemon thread** with its own timer (NOT the scan-cycle tick) — keeps hopping even when the HackRF scan loop is stalled in its flaky-USB recovery. |
| Publish | Current tune → new retained topic **`fpv/<id>/rxtune`** via the live `MqttPublisher`. |
| Dashboard | Consume `rxtune`; show the tuned frequency/channel + a marker on the 5.8 chart. |
| IQ-frame emitter | **Kept** (coexists; unchanged). |
| Backend | **lgpio** (`/dev/gpiochip0`); GPIO abstracted behind an interface so the protocol is unit-tested with a fake backend (no hardware). |
| Bands | **5.8 only** (RX5808 covers ~5.6–5.9 GHz). 1.2/2.4 out of scope. |

## 3. Architecture & data flow

```
HackRF scan loop (run_cycle)                     Rx5808Controller daemon thread
  detect analog 5.8 candidates                     loop every dwell_s:
        │                                             targets = snapshot()         (thread-safe)
        ▼                                             list = targets or ALL_CHANNELS
  controller.update_targets(                          ch = list[(i := (i+1) % len(list))]
     [d.center_mhz for d in dets                       rx5808.set_frequency(gpio, ch.freq, settle_ms)  ─SPI→ RX5808
      if d.band=="5.8G" and                            publisher.publish_rxtune(ts, ch.freq, ch.name,
         d.signal_class=="analog"])                       mode="detected" if targets else "scan", targets)
        │  (maps each to nearest RX5808 ch)                    │
        ▼  stores under lock                                   ▼  fpv/<id>/rxtune (QoS1, retained)
  (continues its normal cycle)                          dashboard: reduce rxtune → highlight tuned freq
                                                                                   │
  RX5808 composite video ──(already)──▶ USB grabber ──ffmpeg(running)──▶ MediaMTX ─┘ (live stream tile)
```

The controller and the scan loop share only `targets` (a small frequency list) behind a lock. The
RX5808 video path (grabber → ffmpeg → MediaMTX) already exists and is **out of scope**; this feature
only changes which channel the RX5808 is on.

## 4. Components / deliverables

```
agent/scan/rx5808.py             (new: protocol port + lgpio backend + RX5808_CHANNELS)
agent/scan/rx5808_controller.py  (new: Rx5808Controller hopping thread)
agent/scan/publisher.py          (change: + _t_rxtune + publish_rxtune)
agent/scan/config.py             (change: + rx5808_* fields/env)
agent/scan/main.py               (change: build+start controller; run_cycle feeds update_targets)
agent/scan/requirements.txt      (change: + lgpio)
agent/scan/tests/test_rx5808.py            (new: protocol encode + fake-GPIO bit sequence)
agent/scan/tests/test_rx5808_controller.py (new: hopping logic with fakes)
agent/scan/tests/test_publisher.py         (change: + publish_rxtune)
agent/scan/tests/test_run_cycle.py         (change: controller.update_targets called with analog-5.8 freqs)
dashboard/public/mqtt-scan.js    (change: reduce 'rxtune' + subscribe fpv/+/rxtune)
dashboard/public/spectrum.js     (change: render rxtune marker + caption in the scanner block)
test/mqtt-scan.test.js           (change: + 'reduce stores rxtune')
test/spectrum.test.js            (change: + rxtune caption helper)
```

### 4.1 `rx5808.py`
- `RX5808_CHANNELS`: list of `(name, freq_mhz)` for the 40 standard 5.8 channels (A/B/E/F/R, from the
  ESP table). `CHANNEL_BY_FREQ = {freq: name}` and `nearest_rx_channel(center_mhz, tol=10)` → `(name, freq)` or `None`.
- `freq_to_register(mhz) -> int` — `tf=(int(mhz)-479)//2; N=tf//32; A=tf%32; return (N<<7)|(A&0x7F)`.
- `encode_word(mhz) -> list[int]` — the 25 bits in send order (LSB-first): address `0x1` → `[1,0,0,0]`,
  R/W `[1]`, then 20 data bits `[(reg>>i)&1 for i in range(20)]`. Pure, unit-tested.
- `class LgpioBackend`: `__init__(chip=0, clk=5, data=6, le=13)` claims the 3 lines as outputs (LE
  idle high); `write(pin, level)`, `close()`. Lazy `import lgpio` so non-Pi hosts don't import it.
- `set_frequency(backend, mhz, settle_ms=35)` — LE low; for each bit of `encode_word(mhz)`: data then
  CLK pulse (rising-edge latch, ~1 µs phases); LE high; sleep `settle_ms` for PLL lock.
- A `FakeBackend` (in the test) records `(pin, level)` writes so the test reconstructs the emitted word.

### 4.2 `rx5808_controller.py` — `Rx5808Controller`
- `__init__(self, backend, publisher, scanner_id, channels, dwell_s, settle_ms, clock=time.monotonic, sleep=time.sleep)` — `clock`/`sleep` injectable for tests; `self._targets=[]`, `self._lock`, `self._idx=-1`, `self._stop=Event()`.
- `update_targets(self, center_mhzs)` — map each to `nearest_rx_channel`, dedup, store the
  `[(name, freq)]` list under the lock (empty list = scan mode).
- `_next(self)` — under the lock: `lst = self._targets or self._channels`; `self._idx=(self._idx+1)%len(lst)`; return `(name, freq, mode, target_freqs)` where `mode="detected" if self._targets else "scan"` and `target_freqs=[f for _,f in self._targets]`.
- `tune(self, name, freq, mode, target_freqs, ts)` — `set_frequency(self.backend, freq, self.settle_ms)`; if `publisher`: `publisher.publish_rxtune(ts, freq, name, mode, target_freqs)`. Guarded.
- `run(self)` — loop until `_stop`: `name, freq, mode, tf = self._next(); self.tune(name, freq, mode, tf, ts=int(self._clock()))`; `self._sleep(self.dwell_s)`. Fully guarded so a tune/publish error never kills the thread (logs, continues).
- `start()` spawns a daemon thread on `run`; `stop()` sets the event.

### 4.3 `publisher.py`
- `__init__`: `self._t_rxtune = f"fpv/{scanner_id}/rxtune"`.
- `publish_rxtune(self, ts, freq_mhz, channel, mode, targets)` → `_publish(self._t_rxtune, {"scanner_id", "ts", "freq_mhz", "channel", "mode", "targets"}, self.QOS_DETECTION)` (QoS1, retained, guarded).

### 4.4 `config.py`
Add (env): `rx5808_enabled=True` (`RX5808_ENABLED`, falsey=0/false/no), `rx5808_clk=5`/`rx5808_data=6`/`rx5808_le=13` (`RX5808_CLK`/`_DATA`/`_LE`), `rx5808_dwell_s=4.0` (`RX5808_DWELL_S`), `rx5808_settle_ms=35` (`RX5808_SETTLE_MS`).

### 4.5 `main.py`
- After the publisher is built, if `cfg.rx5808_enabled`, **lazily** build the controller (guarded so a
  missing lgpio/hardware leaves `controller=None` and the scan service runs normally):
  `from rx5808 import LgpioBackend, RX5808_CHANNELS; from rx5808_controller import Rx5808Controller`;
  construct with the configured pins/dwell; `controller.start()`.
- `run_cycle(cfg, now_ts, publisher=None, emitter=None, controller=None)`: after the band loop, call
  `controller.update_targets([d.center_mhz for d in detections if d.band=="5.8G" and d.signal_class=="analog"])` if `controller` (guarded).

### 4.6 Dashboard
- `mqtt-scan.js`: topic regex adds `rxtune`; `ensure()` adds `rxtune: null`; branch stores
  `s.rxtune = {ts, freq_mhz, channel, mode, targets}`; subscribe `fpv/+/rxtune`.
- `spectrum.js`: `rxtuneCaption(rx)` (pure) → e.g. `"RX5808 → 5865 МГц (A1) · scan"`; in `scannerBlock`,
  if `live.rxtune`, append a caption line; in the **5.8 band cell**, draw a vertical marker at
  `rxtune.freq_mhz` (reuse `detectionX` with the 5.8 range) in a distinct colour.

## 5. MQTT contract

Topic `fpv/<scanner_id>/rxtune`, QoS1, retained:
```json
{ "scanner_id": "hackrf", "ts": 1718700000.0, "freq_mhz": 5865, "channel": "A1",
  "mode": "detected", "targets": [5865, 5800] }
```
`mode` = `"detected"` (cycling detected channels) or `"scan"` (sweeping all channels).

## 6. Hardware & power

- 3 GPIO outputs (CLK/DATA/LE) at 3.3V — directly drive the RTC6715 SPI; no level shifter. RX5808
  module powered at 5V.
- **Power caution:** the Pi 3B already powers the HackRF over USB; the observed HackRF flaky-USB
  wedging is consistent with marginal 5V. **Power the RX5808 (and grabber) from a powered USB hub or a
  separate 5V supply with common ground** — do not add their draw to the Pi's rail. (Operational note,
  not enforced in code.)
- After a tune, wait `settle_ms` (~35 ms) for PLL lock before the video is stable.

## 7. Failure & resilience

- The controller thread is fully guarded: a tune/publish/GPIO error is logged and the loop continues
  (it never dies). `publish_rxtune` is a no-op when the publisher isn't connected.
- If lgpio / the hardware is unavailable (e.g. dev machine, or `RX5808_ENABLED=0`), `main()` logs once
  and runs with `controller=None`; the scan service (sweep/detect/publish/IQ-frame) is unaffected.
- The controller hops independently of HackRF health, so RX5808 scanning continues during sweep
  timeouts / USB resets. `update_targets` only ever swaps a small list behind a lock.

## 8. Testing / verification

- **`test_rx5808.py`:** `freq_to_register(5865)` matches the ESP formula; `encode_word(5865)` is the
  expected 25-bit LSB-first sequence; driving `set_frequency` with a `FakeBackend` reproduces that
  word on CLK rising edges with correct LE framing; `nearest_rx_channel(5865.3)` → `("A1", 5865)`.
- **`test_rx5808_controller.py`:** with a fake backend + fake publisher + injected clock/sleep:
  `update_targets([])` → scan mode cycles all channels round-robin; `update_targets([5865.3, 5800])`
  → detected mode cycles `[5865, 5800]`; `publish_rxtune` called with the right freq/channel/mode each
  hop; a tune that raises is swallowed (loop survives).
- **`test_publisher.py`:** `publish_rxtune` → topic `fpv/<id>/rxtune`, QoS1, retain, payload shape; no-op when not connected.
- **`test_run_cycle.py`:** a fake controller's `update_targets` is called with exactly the analog-5.8
  detection centers (monkeypatch `classify`/fixtures), and not for non-5.8 / non-analog.
- **Dashboard (`node --test`):** `reduce` stores `rxtune`; `rxtuneCaption(...)` contains the freq,
  channel, mode. `node --check` on changed browser files.
- Run: `cd agent/scan && python -m pytest tests ../video/tests -q` and `npm test`.
- **Live (after deploy):** with an analog 5.8 TX on a known channel, `mosquitto_sub -t 'fpv/#'` shows
  `fpv/hackrf/rxtune` hopping; with the TX present the controller settles onto the detected channel(s)
  and the grabber stream shows that channel; the dashboard highlights the tuned frequency.

## 9. Deployment notes (later step)

On the Pi: `pip install -r agent/scan/requirements.txt` (adds `lgpio`) into the scan venv; ensure the
service user (root) can access `/dev/gpiochip0` (it can). Wire CLK/DATA/LE per §2. `RX5808_*` env in
the unit/env file if non-default. No change to the IQ-frame path. Dashboard: rebuild the
`fpv-dashboard` container (the documented surgical `docker compose build dashboard && up -d --no-deps dashboard`).

## 10. Out of scope

- The grabber → ffmpeg → MediaMTX video path (already running) and ffmpeg autostart.
- Manual channel control from the dashboard (auto only for v1).
- 1.2/2.4 GHz receivers; per-channel RSSI on the Pi (no ADC).
- Changing the HackRF scan/detection or the IQ-frame emitter.
