# Node Telemetry (CPU temp / RAM / host health) — Design

**Date:** 2026-07-11
**Status:** Approved design, ready to plan. Branch `feat/node-telemetry` off `main`.

## Goal
Show real host health for the physical Raspberry Pi that carries the SDRs — CPU temperature,
RAM, throttle/undervoltage, CPU load, uptime, disk — filling the node **TEMP `—°C`** placeholder slots
built during the tactical UI redesign. One physical Pi hosts HackRF + bladeRF + RX5808 (device-ids
`hackrf`, `hackrf-view`, `bladerf`); the user's rule is **that counts as ONE node**, so telemetry is a
host property published once per node and the dashboard groups the radios under that node.

## Decisions (locked in brainstorming)
1. **Node model:** an optional `node:` key on each device in `devices.yml` groups radios under one
   physical host. The **node-id = `bladerf`** (reuse an existing scanner id — no new MQTT identity).
2. **Metrics:** CPU temp, RAM (used/total/%), throttle+undervoltage flags, CPU load, host uptime, disk.
3. **Publisher:** a new standalone `fpv-telemetry` systemd service on the Pi that **reuses the scan
   agent's MQTT publish credentials** (broker user `pub` — `bladerf` is the node-id/topic segment, not
   a username) and publishes `fpv/bladerf/telemetry` retained every ~15 s. Standalone (not
   folded into the scan agent) so host health is reported even when a scan agent has crashed — which is
   exactly when it matters. **No mosquitto ACL / broker restart** needed.
4. **Display:** the **Вузли** screen groups by node — a node header with the health readout, its radio
   cards beneath; ungrouped devices (phone `pi-03`, `pi-01`) render standalone. The **Панель** node-strip
   shows each node once with CPU temp + RAM + throttle badge.

## Architecture / data flow
```
Pi 5 host ── fpv-telemetry.service (NEW) ──▶ MQTT fpv/bladerf/telemetry  (retained, ~15 s)
                                                     │
                                        mosquitto broker (unchanged)
                                                     │
                        dashboard MqttScanClient ─▶ store["bladerf"].telemetry
                                                     │
                    views/nodes.js (grouped) + views/dashboard.js (node-strip) ─▶ host health UI
```

## Component 1 — Pi telemetry agent (`agent/telemetry/`)
A small, dependency-light Python package mirroring `agent/scan/`'s layout:

- **`collector.py`** — PURE metric readers, each independent and fail-soft (a failed read → `None`, others
  still publish). Sources (no heavy deps; `psutil` NOT required):
  - CPU temp → `/sys/class/thermal/thermal_zone0/temp` (millidegrees → °C), fallback `vcgencmd measure_temp`.
  - RAM → `/proc/meminfo` (`MemTotal`, `MemAvailable`) → used_mb, total_mb, used_pct.
  - CPU load → `/proc/loadavg` (1-min) normalized by `os.cpu_count()` → `cpu_load_pct`.
  - Uptime → `/proc/uptime` → `uptime_s`.
  - Disk → `os.statvfs('/')` → `disk_used_pct`.
  - Throttle → `vcgencmd get_throttled` → parse the hex bitmask (`parse_throttled()`), yielding
    `throttled` (bit 0 undervolt-now OR bit 2 throttled-now), `throttled_ever` (bit 16 OR bit 18), and the
    raw `throttle_flags`. Absent `vcgencmd` (non-Pi / dev) → all three `None`.
- **`main.py`** — the loop: build the payload from `collector`, publish, `sleep(interval)`. Reads config
  from env (`TELEM_NODE_ID`, `TELEM_INTERVAL_S=15`, `TELEM_MQTT_HOST=10.8.0.1`, `TELEM_MQTT_PORT=1883`,
  `MQTT_PUB_USER`, `MQTT_PUB_PASS`). Graceful on MQTT errors (log + retry next tick; paho auto-reconnect).
- **MQTT publish** — a minimal client that **only** writes `fpv/<node>/telemetry` (QoS 1, retained).
  **CRITICAL: it sets NO will/LWT and NEVER publishes `fpv/<node>/status`** — otherwise it would clobber
  the scan agent's retained presence on the shared `bladerf` id (same rule as `publish_video_once`).
- **`requirements.txt`** — `paho-mqtt` (already the scan agent's dep).

### Payload contract — `fpv/<node>/telemetry` (retained, QoS 1)
```json
{ "node_id": "bladerf", "ts": 1752200000,
  "cpu_temp_c": 62.4,
  "cpu_load_pct": 38,
  "mem_used_mb": 1200, "mem_total_mb": 4096, "mem_used_pct": 29,
  "disk_used_pct": 47,
  "uptime_s": 123456,
  "throttled": false, "throttled_ever": true, "throttle_flags": "0x50000" }
```
Any field whose source failed is `null` (not omitted) so the shape is stable. `ts` is epoch seconds.

## Component 2 — deployment unit (`systemd/fpv-telemetry.service`)
A repo template like `systemd/fpv-scan.service`: runs `agent/telemetry` via its venv, `Restart=always`,
`Environment=` for node-id + MQTT creds. Installed on the Pi as `/etc/systemd/system/fpv-telemetry.service`.
**Must not touch** the hand-diverged `fpv-scan.service` / `fpv-scan-hackrf.service` units.

## Component 3 — dashboard consumption
- **`dashboard/public/mqtt-scan.js`** (intentional, in-scope change — this is the MQTT subscriber's job;
  the "do-not-modify" note was scoped to the UI redesign): add `telemetry` to the topic regex, subscribe
  to `fpv/+/telemetry`, and reduce it to `store[id].telemetry = { ts, cpu_temp_c, cpu_load_pct,
  mem_used_mb, mem_total_mb, mem_used_pct, disk_used_pct, uptime_s, throttled, throttled_ever,
  throttle_flags }`. Add `telemetry: null` to `ensure()`/`emptyStore()`. Pure + fail-soft on bad input,
  matching the existing reducer.
- **`lib/status.js`** — `mergeStatus` passes the registry's `node` through: add `node: d.node || null` to
  the returned device object (so the dashboard's device list carries the grouping key).
- **`dashboard/public/views/components.js`** — health atoms: reuse `tempSlot`; add small builders for a
  RAM used/total+% cell, a throttle badge (🔥 warn when `throttled`/`throttled_ever`), and a staleness-
  aware value wrapper. Pure, unit-testable where non-DOM.
- **`dashboard/public/views/nodes.js`** — restructure to **group by `node`** (reconcile-safe, per the v2
  pattern — build node/card skeletons once, update live fields in place, never wipe inputs):
  - Devices sharing a `node` id render inside one node group: a **node header** with the health readout
    from `store[nodeId].telemetry` (CPU temp, RAM, CPU load, uptime, disk%, throttle badge; each cell
    shows `—` when the field is null or the telemetry is stale), and the member radio cards (existing
    RX5808 / view / CRUD controls unchanged) beneath.
  - Devices without a `node` render as standalone cards (today's behavior).
  - The per-card TEMP slot is superseded by the node-header temp; radio cards drop the redundant `—°C`.
- **`dashboard/public/views/dashboard.js`** — the node-strip shows each **node** once (node header health:
  CPU temp + RAM + throttle badge), not each radio device.
- **Staleness:** telemetry is considered fresh only if `now - ts < 45 s` (≈3× the 15 s interval); otherwise
  the health cells show `—`. Retained delivery means a freshly-loaded dashboard gets the last value at once.

## Contracts to preserve
- **Reconcile-safe rendering** (v2): node grouping in `nodes.js` must build skeletons once and update live
  fields in place — never `innerHTML=''` a container holding a typed `.view-freq` input or an open RX5808
  `<select>`. Route stays `live:true`.
- **Do NOT clobber scan presence:** the telemetry publisher writes only `fpv/<node>/telemetry`, never
  `.../status`, and sets no LWT.
- **MQTT reducer stays pure + fail-soft**; `npm test` stays green (extend, don't break, exports).
- **Do NOT modify** the Pi's `fpv-scan` / `fpv-scan-hackrf` systemd units or the scan agent's behavior.

## Testing
- **Python (agent/telemetry/tests):** `parse_throttled()` bitmask cases (undervolt-now, throttled-now,
  ever-flags, `0x0`, malformed/absent); `collector` readers against synthetic `/proc` + `/sys` sample
  strings (temp, meminfo, loadavg, uptime) and a fail-soft path (missing file → `None`); payload builder
  shape (all keys present, nulls stable).
- **JS (node:test):** `mqtt-scan` reduce for a `telemetry` message (store shape, bad-input guard);
  any pure health formatter / staleness helper added to `components.js`/`spectrum.js`.
- **Dashboard visual (dev-preview):** extend `fixtures.js` with a `telemetry` block on the `bladerf` node
  and a `node:` mapping so the Вузли grouping + node-strip render with sample health; controller verifies
  in Chrome (grouped header, health cells, throttle badge, stale `—` case).

## Deployment (two-sided)
- **Pi** (`andriy@` over WG): `git pull`, create the telemetry venv + `pip install -r agent/telemetry/
  requirements.txt`, install `/etc/systemd/system/fpv-telemetry.service`, `systemctl enable --now
  fpv-telemetry`. Verify `fpv/bladerf/telemetry` appears (retained) and `fpv-scan*` units untouched.
- **Dashboard** (server over WG): add `node:` keys to `devices.yml` (hackrf, hackrf-view, bladerf →
  `node: bladerf`), `git pull`, `docker compose build dashboard` + `up -d --no-deps dashboard`
  (mediamtx / mosquitto / wg-easy untouched). Hard-refresh the browser.

## Out of scope
- Telemetry for the dashboard host (traefik box) or the camera nodes (`pi-03`, `pi-01`) — the schema
  supports them (any host can publish `fpv/<node>/telemetry`), but only the Pi 5 SDR node is wired now.
- Historical telemetry charts / alerting thresholds (only the live readout).
- A `nodes:` registry with per-node display names — the node header derives its label from the node-id and
  the grouped scanner's `location`; a named node registry can be added later if needed.
- Any change to scan/video/rxtune/view behavior or the MediaMTX/traefik stack.
