# FPV Video Stream — Server-Side Design Spec

**Date:** 2026-06-13
**Author:** andriy@netlife.com.ua
**Status:** Approved (pending written-spec review)

## 1. Context & Goal

Several remote nodes (Raspberry Pi 5) capture analog video via USB grabber and push an
H.264 stream over an existing WireGuard tunnel to a central Ubuntu server. We must deploy
the **server side**:

1. A **media server** that ingests multiple simultaneous streams.
2. A **web dashboard** showing all streams in a responsive grid, with live WebRTC preview,
   per-device online/offline status, and low latency.

Adding a new node must be simple — no hand-editing of the main config.

## 2. Environment (confirmed parameters)

| Parameter | Value |
|---|---|
| Server OS | Ubuntu 22.04 / 24.04, sudo/root available |
| WireGuard | wg-easy already running — **do not modify**, only add |
| WG server tunnel IP | `10.8.0.1` |
| WG interface | `wg0` |
| Pi nodes | WireGuard clients `10.8.0.x`, outbound-only (Starlink/CGNAT) |
| Push target | `rtsp://10.8.0.1:8554/<device-id>` (SRT alternative on 8890/udp) |
| Dashboard access | **WG-only** (operators connect as wg-easy clients). Public TLS+domain deferred. |
| Web stack | Node + Express + vanilla JS |
| Device registry seed | 2 placeholder devices + `add-device` script |
| MediaMTX auth model | **Config generated from registry** (per-device `authInternalUsers`), hot-reloaded |
| Telemetry | Stubbed hook only (ready for MQTT/POST, no live source) |

The public IP is used **only** for the WireGuard handshake; all media travels inside the tunnel.

## 3. Architecture & Data Flow

```
Pi nodes (10.8.0.x)        MediaMTX (binds 10.8.0.1 / 127.0.0.1)
  ffmpeg x264 (soft) ──┐    ├─ RTSP ingest   10.8.0.1:8554
  rtsp://10.8.0.1:8554/<id> ├─ SRT  ingest   10.8.0.1:8890/udp  (alt)
                       └──▶ ├─ WebRTC/WHEP    10.8.0.1:8889
                            ├─ ICE UDP        :8189 (advertises host 10.8.0.1)
                            └─ Control API    127.0.0.1:9997
                                      ▲ read-only polling
Operator (wg-easy client)             │
  browser ──▶ Dashboard 10.8.0.1:8080 ┘
    WHEP player pulls video directly from 10.8.0.1:8889
```

- **Ingest path** (Pi → MediaMTX) is independent of the dashboard. If the dashboard is
  down, cameras keep publishing — auth is in MediaMTX, generated from the registry.
- **Playback path** (browser → MediaMTX WHEP) is direct WebRTC; the dashboard only serves
  the page and proxies/polls the control API for status. Video bytes do not pass through Node.

## 4. Components

### 4.1 MediaMTX (media server)
- Latest release installed under `/opt/mediamtx` (or `/usr/local/bin` + config dir), run as a
  systemd service `mediamtx.service` with `Restart=always`, logs to journald.
- **Config is generated** from the device registry (`mediamtx.yml` rendered by the generator).
- Bindings (interface-scoped):
  - `rtspAddress: 10.8.0.1:8554`
  - `srtAddress: 10.8.0.1:8890`
  - `webrtcAddress: 10.8.0.1:8889`
  - `webrtcLocalUDPAddress: :8189`, `webrtcIPsFromInterfaces: false`,
    `webrtcAdditionalHosts: [10.8.0.1]` (advertise only the WG IP; no STUN/TURN needed —
    readers share the tunnel subnet)
  - `apiAddress: 127.0.0.1:9997` (most restrictive — dashboard runs on the same host)
- Auth: `authMethod: internal`, `authInternalUsers`:
  - One entry per device: `user: <device-id>`, `pass: <generated>`, `permissions:
    [{action: publish, path: <device-id>}]` → a compromised node can only publish to its own path.
  - One reader: `user: <READ_USER>`, `pass: <READ_PASS>`, `permissions: [{action: read}]`
    (read on all paths) — used by the browser WHEP players.
  - One api/metrics user limited to `ips: [127.0.0.1]` (or anonymous `any` scoped to localhost IP)
    so the dashboard can poll the control API locally.
- Paths: single catch-all `paths: { all_others: { source: publisher } }` with low-latency
  defaults. No per-device path entries — isolation comes from `authInternalUsers` path scoping.
- Low latency: WebRTC playback (inherently low-latency); `rtspTransports` kept permissive
  (tcp/udp); SRT latency tuned on the publisher side.

### 4.2 Device registry (`devices.yml`)
Single source of truth. Schema per device:
```yaml
devices:
  - id: pi-01                 # used as RTSP path and publish username
    name: "Front Gate"        # friendly name shown on the tile
    location: "Perimeter — North"
    publish_pass: "<generated>"  # per-device publish password
read_user: viewer             # single reader login for all streams (browser playback)
read_pass: "<generated>"
```
Secrets live here (file mode `600`, git-ignored), **not** in code. `.env` holds dashboard
login + paths to registry/config + MediaMTX API address.

### 4.3 Dashboard (Node + Express + vanilla JS)
- **Backend** (`server.js`):
  - Serves static frontend + login (single user/pass from `.env`, signed-cookie session).
  - `GET /api/devices` — merges the registry with a poll of MediaMTX
    `GET /v3/paths/list` (control API on 127.0.0.1:9997) → returns each expected device with
    `online|offline`, plus `bitrate`/`readers`/`uptime` where the API provides them.
  - `GET /api/stream` (SSE) — pushes status diffs so tiles appear/disappear gracefully without
    a page reload. (Fallback: client polls `/api/devices` every ~2s.)
  - `POST /api/telemetry/:id` — **stubbed** telemetry hook (accepts JSON, stores last value in
    memory, exposes it on the device payload). Ready to wire to MQTT or Pi POSTs later; no live
    source now.
  - Generates a per-load WHEP config so the frontend knows the WebRTC base URL
    (`http://10.8.0.1:8889`) and the read credentials (delivered to authenticated sessions only).
- **Frontend** (`public/`):
  - Responsive grid of tiles. Each tile: friendly name + location, WHEP `<video>` player,
    online/offline badge, bitrate/uptime when available, telemetry sub-panel (hidden until data).
  - Click a tile → enlarged/fullscreen view.
  - Reconnect/teardown logic for streams coming and going.
  - WHEP client: standard `WHEP` POST/PATCH against `http://10.8.0.1:8889/<device-id>/whep`.

### 4.4 Operations tooling
- `install.sh` — idempotent, parameterized (WG IP/iface, dashboard host/port, device seed list).
  Installs MediaMTX, renders config from registry, installs systemd units, installs Node deps,
  generates `.env` if missing, prints firewall guidance. **Never touches wg-easy config.**
- `add-device.sh <device-id> "<name>" "<location>"` — generates publish creds, appends to
  `devices.yml`, regenerates `mediamtx.yml`, triggers MediaMTX reload, prints the exact Pi 5
  push command (software x264 via ffmpeg, since Pi 5 has no hardware H.264 encoder).
- `gen-mediamtx.(js|sh)` — renders `mediamtx.yml` from `devices.yml` + template.
- systemd units: `mediamtx.service`, `fpv-dashboard.service`.

## 5. Ports & Interface Bindings (exact)

| Port | Proto | Service | Bind | Exposure |
|---|---|---|---|---|
| 8554 | TCP | RTSP ingest | `10.8.0.1` | WG only |
| 8890 | UDP | SRT ingest | `10.8.0.1` | WG only |
| 8889 | TCP | WebRTC/WHEP | `10.8.0.1` | WG only |
| 8189 | UDP | WebRTC ICE | all ifaces; advertises `10.8.0.1` | WG only (reachable via tunnel) |
| 9997 | TCP | Control API | `127.0.0.1` | localhost only |
| 8080 | TCP | Dashboard | `10.8.0.1` | WG only |
| (WG) | UDP | wg-easy handshake | public IP | the **only** public-facing port |

Firewall (README guidance, optional `ufw`): allow the above inbound only on `wg0`; deny on the
public interface except the existing WireGuard UDP port. The public IP serves the WG handshake only.

## 6. Security

- No secrets in code — all in `.env` and `devices.yml` (mode `600`, git-ignored).
- Per-device publish credentials → a compromised node cannot read or publish other streams.
- Control API and ingest bound to tunnel/localhost, never the public interface.
- Dashboard behind a simple login; reachable only over WG.
- `install.sh` is additive to networking; it does not modify the WireGuard/wg-easy setup.

## 7. Operations / README contents

- How to add a node: run `add-device.sh` (or edit `devices.yml`), copy the printed Pi push command.
- Exact Pi 5 push command (software x264 ffmpeg) for both RTSP and SRT.
- How operators connect (import a wg-easy client config, browse to `http://10.8.0.1:8080`).
- Firewall notes; how to enable optional public TLS later (Caddy) without changing the API exposure.

## 8. Deliverables (file list)

```
install.sh
add-device.sh
gen-mediamtx.js                 # registry → mediamtx.yml generator
mediamtx.template.yml           # base config (low-latency, bindings, catch-all path)
devices.example.yml             # 2 placeholder devices (real devices.yml git-ignored)
.env.example
dashboard/
  server.js
  package.json
  public/ (index.html, app.js, whep.js, styles.css, login.html)
systemd/
  mediamtx.service
  fpv-dashboard.service
README.md
.gitignore
# Caddyfile — only if public access is later requested (not in this iteration)
```

## 9. Out of Scope (YAGNI for this iteration)

- Public TLS + domain via Caddy (documented as a future toggle; not built now).
- Live telemetry source (MQTT/POST wiring) — only a clean stub hook is provided.
- Recording to disk, multi-server clustering, transcoding.

## 10. Assumptions / Open Items

- wg-easy uses `wg0` / `10.8.0.1` (confirmed). If real values differ, `install.sh` is
  parameterized to override.
- Dashboard port defaults to `8080` (changeable via `.env`); no conflict assumed on the WG IP.
- MediaMTX auto-reloads on config-file change; `add-device.sh` falls back to
  `systemctl reload/restart mediamtx` if needed.
