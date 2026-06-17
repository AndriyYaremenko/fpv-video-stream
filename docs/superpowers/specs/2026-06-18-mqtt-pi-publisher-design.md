# Pi Scan Service → MQTT Publisher (SP-B) — Design Spec

**Date:** 2026-06-18
**Author:** andriy@netlife.com.ua
**Status:** Approved (pending written-spec review)

## 1. Context & Goal

SP-A stood up the broker (mosquitto in wg-easy's netns), the topic/payload contract, and the
dashboard's `GET /api/mqtt` creds endpoint. SP-B makes the **Pi scan service publish scan data to
the broker** over WireGuard, replacing its HTTP telemetry POST to the dashboard. This is the
producer half of the cutover; the dashboard subscriber + waterfall (SP-C) is the consumer half.

The scan service (`agent/scan/`, Python, runs on a Pi with a HackRF) currently, each cycle:
1. builds one combined payload `{scanner_id, ts, detections[], occupancy{}, spectrum{band:[pts]}}`,
2. writes it to a local state file (`/run/fpv-scan/scan.json`),
3. **POSTs it to the dashboard** `POST /api/telemetry/<id>` (the path SP-B replaces),
4. serves the latest payload from a local debug HTTP server (`127.0.0.1:8077`).

## 2. Confirmed decisions

| Decision | Value |
|---|---|
| Transport out | Add **MQTT publish** (paho-mqtt) over WG to `10.8.0.1:1883`; **remove** the dashboard HTTP POST (`post_telemetry`). |
| Local debug | **Keep** the on-disk state file (`write_state`) and the local HTTP server (`:8077`). MQTT is added alongside. |
| Spectrum cadence | **Per-band, published immediately after that band's sweep** (3 frames/cycle), each `bands:[<one band>]`. |
| Detection cadence | **Once per cycle**, at the end (all detections + the per-band occupancy map). |
| Migration | **Hard cutover** — no dual HTTP+MQTT publish. A dark window on the dashboard scan panel between SP-B and SP-C is acceptable (operator confirmed). |
| Library | `paho-mqtt` (background `loop_start`, auto-reconnect, LWT for presence). |
| psd resolution | MQTT spectrum frame downsampled to **~128 pts/band** (the local state file stays at 64). |

## 3. Architecture & data flow

```
                          ┌─ per band, right after sweep ─▶ fpv/<id>/spectrum  (QoS0, retained)
scan cycle (run_cycle) ───┤
                          └─ end of cycle ───────────────▶ fpv/<id>/detection (QoS1, retained)
   │  build_payload + write_state  → /run/fpv-scan/scan.json     (kept, local)
   │  local HTTP :8077              → latest payload             (kept, local)
   ▼
MqttPublisher (paho) ──MQTT :1883 (WG 10.8.0.1)──▶ mosquitto (SP-A)
   on connect → fpv/<id>/status {online:true} (QoS1, retained)
   LWT (set at connect) → fpv/<id>/status {online:false} (QoS1, retained) — delivered on ungraceful disconnect
```

- `<id>` = `cfg.scanner_id` (e.g. `hackrf`). Topics are built from it: `fpv/<id>/{spectrum,detection,status}`.
- The publisher runs a background network loop; publishing is decoupled from the scan loop. If the
  broker is unreachable the scan loop keeps running and writing the state file — publish failures
  never crash the cycle (QoS1 detection is queued by paho; QoS0 spectrum may be dropped — acceptable).

## 4. Components / deliverables

```
agent/scan/publisher.py        (new: MqttPublisher + pure payload builders)
agent/scan/config.py           (change: MQTT config fields + env; remove server_url/server_token)
agent/scan/main.py             (change: run_cycle publishes per-band spectrum + end-of-cycle detection; main() wires the publisher; remove post_telemetry)
agent/scan/reporter.py         (change: remove post_telemetry + `import requests`; keep build_payload/write_state/local HTTP)
agent/scan/requirements.txt    (change: +paho-mqtt>=2.0, -requests)
agent/scan/tests/test_publisher.py   (new: payload-builder contract + publish topic/qos/retain via fake client + no-op when down)
agent/scan/tests/test_run_cycle.py   (change: inject a fake publisher; assert per-band spectrum + one detection; drop server_url)
agent/scan/tests/test_reporter.py    (change: remove test_post_telemetry_swallows_errors)
agent/scan/tests/test_config.py      (change: drop SCAN_SERVER_URL assertions; add MQTT env assertions)
```

### 4.1 `publisher.py`

- **Pure builders (no network — unit-testable):**
  - `build_spectrum_frame(scanner_id, ts, band_id, low_mhz, high_mhz, psd) -> dict`
    → `{"scanner_id", "ts", "bands":[{"id":band_id, "low_mhz", "high_mhz", "psd":[...]}]}`.
  - `build_detection_payload(scanner_id, ts, detections, occupancy) -> dict`
    → `{"scanner_id", "ts", "detections":[d.to_dict()…], "occupancy":{…}}` (`to_dict()` already emits `"class"`).
- **`MqttPublisher`:**
  - `__init__(host, port, user, pass, scanner_id, keepalive)` — builds topic names; sets up the paho `Client`.
  - `connect()` — sets credentials, sets **LWT** on `fpv/<id>/status` = `{"online":false,"ts":<connect_ts>}` (QoS1, retained), connects, `loop_start()`. On the connect callback, publishes `fpv/<id>/status` = `{"online":true,"ts"}` (QoS1, retained).
  - `publish_spectrum(ts, band_id, low_mhz, high_mhz, psd)` — publish to `fpv/<id>/spectrum`, QoS0, retain=True.
  - `publish_detection(ts, detections, occupancy)` — publish to `fpv/<id>/detection`, QoS1, retain=True.
  - All publishes are guarded: wrap in try/except and log; never raise into the caller.
  - `close()` — publish offline status (best-effort), `loop_stop()`, `disconnect()` (used on graceful shutdown; the loop otherwise runs forever and LWT covers crashes).

### 4.2 `config.py`

Add (env in parentheses): `mqtt_host="10.8.0.1"` (`SCAN_MQTT_HOST`), `mqtt_port=1883` (`SCAN_MQTT_PORT`),
`mqtt_user="pub"` (`MQTT_PUB_USER`), `mqtt_pass=""` (`MQTT_PUB_PASS`), `mqtt_keepalive=60`
(`SCAN_MQTT_KEEPALIVE`), `mqtt_enabled=True` (`SCAN_MQTT_ENABLED`, falsey = "0"/"false"). **Remove**
`server_url`, `server_token`, and their `SCAN_SERVER_URL`/`SCAN_SERVER_TOKEN` reads.

### 4.3 `main.py`

- `run_cycle(cfg, now_ts, publisher=None)`:
  - per band: after `spec = _get_spectrum(...)` and computing `occupancy`/downsample, call
    `publisher.publish_spectrum(now_ts, band, brange[0], brange[1], _downsample(spec, 128))`
    if `publisher` is not None — **before** the dwell loop for that band.
  - after all bands: `publisher.publish_detection(now_ts, detections, occupancy)` if `publisher`.
  - keep `build_payload` + `write_state(cfg.state_path, payload)`; keep returning `payload`.
  - **remove** the `post_telemetry(...)` call and its import.
- `main()`: if `cfg.mqtt_enabled`, construct `MqttPublisher(...)`, `connect()`, pass it into
  `run_cycle`. Keep the existing local HTTP server + the failure/backoff/`reset_hackrf` loop. A
  publisher construction/connect failure logs and continues with `publisher=None` (scanning + state
  file still work).

## 5. Failure & resilience

- Broker down at startup: `connect()` failure is caught; service runs with `publisher=None` (no
  MQTT) and keeps scanning + writing state. (paho can also be configured to retry connect; simplest
  is to attempt connect once and let the loop's reconnect handle later availability — if connect
  raises, fall back to None for this run.)
- Broker drops mid-run: paho's background loop auto-reconnects; on reconnect the connect callback
  republishes online status. QoS1 detection is queued; QoS0 spectrum frames during the outage are lost.
- A publish error never propagates into `run_cycle` (guarded), so the scan/`reset_hackrf` recovery
  loop is unaffected.

## 6. Security

- MQTT `:1883` is reached only over WireGuard (`10.8.0.1`), not host-published (SP-A). Credentials
  (`MQTT_PUB_USER`/`MQTT_PUB_PASS`) come from the scan service's environment (systemd unit / env
  file on the Pi), not committed. The `pub` user is publish-only on `fpv/#` (SP-A ACL).
- No TLS on `:1883` (WG already private — same decision as SP-A §9).

## 7. Testing / verification

- **Automated (pytest, `agent/scan/tests/`):**
  - `test_publisher.py`: `build_spectrum_frame`/`build_detection_payload` produce the contract shape
    (self-describing `bands[]` with `low_mhz`/`high_mhz`/`psd`; detection `class` key, `occupancy`
    map). With a **fake paho client** (injected/monkeypatched), assert `publish_spectrum`/
    `publish_detection`/status use the right topic, QoS, and `retain`, and the LWT is set on connect.
    Assert publishes are no-ops (no raise) when the client reports not connected.
  - `test_run_cycle.py`: inject a fake publisher (records calls); assert one `publish_spectrum` per
    band and exactly one `publish_detection` per cycle; the returned payload + state file are
    unchanged. Drop the `server_url` line.
  - `test_reporter.py`: remove `test_post_telemetry_swallows_errors`; keep build_payload/write_state.
  - `test_config.py`: drop `SCAN_SERVER_URL` assertions; add MQTT env → field assertions.
  - Run: `cd agent/scan && python -m pytest -q`.
- **Ops verification (live, after deploy):** with the broker up, run the scan service on the Pi; a
  `mosquitto_sub -t 'fpv/#' -v` (as `sub`, over WG) shows retained `fpv/<id>/status {online:true}`,
  per-band `fpv/<id>/spectrum` frames, and `fpv/<id>/detection`; stopping the service flips
  `status` to `{online:false}` (LWT). wg-easy/mediamtx/dashboard untouched.

## 8. Deployment

On the Pi (scan service host): `pip install -r agent/scan/requirements.txt` (adds `paho-mqtt`), set
`MQTT_PUB_USER`/`MQTT_PUB_PASS`/`SCAN_MQTT_HOST` (=`10.8.0.1`) in the scan service's env / systemd
unit, restart the unit. The broker (SP-A) must be deployed first. **Sequencing:** the dashboard scan
panel (Spectrum / scanner-online) reads the old telemetry path and goes **stale/empty between SP-B
and SP-C** — acceptable per the operator; the live spectrum is still visible on the Pi via the local
state file / `:8077`. Deploy SP-C soon after to restore the dashboard view.

## 9. Out of scope

- The dashboard MQTT-WS subscriber + waterfall + custom-bands UI (SP-C), and removal of the
  dashboard's `/api/telemetry/:id` + SSE scan render / Spectrum-from-telemetry (done in SP-C as the
  consumer cutover lands).
- Per-scanner ACL write-scoping, MQTT-over-TLS on `:1883` (deferred in SP-A §9).
- Changing the local state-file payload shape or psd size (stays 64; MQTT frame uses 128).

## 10. Assumptions

- The broker (SP-A) is reachable at `10.8.0.1:1883` over WG with the `pub` credentials.
- `paho-mqtt>=2.0` installs on the Pi's Python (the scan venv). The Pi reaches the broker outbound
  over WG (same path the HTTP POST used).
- `cfg.scanner_id` on the unit is `hackrf` (per the deployed unit), so topics are `fpv/hackrf/...`.
