# FPV Video Stream — Server

Server side for multi-node FPV video surveillance: MediaMTX ingests H.264 from Raspberry Pi 5
nodes over WireGuard; a Node/Express dashboard shows all streams in a live WebRTC grid with
online/offline status. Everything is reachable only over WireGuard.

## Architecture

```
Pi (10.8.0.x) --H.264--> MediaMTX (10.8.0.1)  --WHEP--> Browser (wg-easy client)
   ffmpeg x264                RTSP :8554 / SRT :8890 ingest
                              WebRTC :8889 + ICE :8189
                              Control API 127.0.0.1:9997  <-- dashboard polls (read-only)
                          Dashboard :8080 (10.8.0.1)
```

`devices.yml` is the single source of truth. `mediamtx.yml` is generated from it
(`node bin/gen-mediamtx.js`); each device gets its own publish user, so a compromised node
cannot read or publish other streams.

## Install (Ubuntu 22.04/24.04, root)

```bash
git clone https://github.com/AndriyYaremenko/fpv-video-stream.git
cd fpv-video-stream
sudo WG_IP=10.8.0.1 WG_IFACE=wg0 ./install.sh
```

The installer is idempotent and **does not touch wg-easy/WireGuard**. It installs MediaMTX +
Node, generates `.env` and `devices.yml` (from examples) on first run, renders the config, and
starts both systemd services. Review `.env` afterwards (set `DASH_USER` / `DASH_PASS`).

## Install (Docker — when WireGuard runs as a container, e.g. wg-easy)

If WireGuard runs as a Docker container (so `wg0`/`10.8.0.1` live inside that container's
network namespace, not on the host), use the Compose deployment instead of `install.sh`.
MediaMTX and the dashboard join the wg-easy container's network namespace
(`network_mode: container:wg-easy`), bind to `10.8.0.1` (wg0), and become reachable by
WireGuard clients — without modifying wg-easy and without publishing any host ports.

```bash
git clone https://github.com/AndriyYaremenko/fpv-video-stream.git
cd fpv-video-stream
cp .env.example .env        # set WG_IP=10.8.0.1, a strong DASH_PASS and SESSION_SECRET
cp devices.example.yml devices.yml   # or build it with ./compose-add-device.sh
docker compose up -d --build
```

Prereqs: the WireGuard container must be named `wg-easy`. The dashboard is then reachable at
`http://10.8.0.1:8080` from any WireGuard client. Note: if the `wg-easy` container is recreated,
restart these services (`docker compose restart mediamtx dashboard`) so they re-attach to the
new namespace.

**Manage nodes from the dashboard:** the web UI has **➕ Додати вузол** to create a node
(device id optional — auto-generated as `pi-NN` if blank); it shows the publish password and the
ready-to-paste RTSP/SRT push command. Each tile has 🔄 (restart the live view), 🔑 (re-show
creds/push command), ✏️ (edit name/location) and 🗑 (delete). MediaMTX hot-reloads its config on
add/delete — no restart needed; edits to name/location only touch `devices.yml`. WireGuard on the
Pi is still set up manually (e.g. via the wg-easy UI). The top bar also has **🔄 Усі** (restart all
views) and a **▭ slider** to resize the video tiles (persisted per browser). The
`./compose-add-device.sh` CLI remains available as an alternative to the web UI.

## Add a new node

```bash
sudo ./add-device.sh pi-03 "Garage" "Compound — South"
```

This generates credentials, updates `devices.yml`, regenerates `mediamtx.yml`, reloads MediaMTX,
and prints the exact Pi push command. No main-config editing required.

## Pi 5 push command (software x264 — Pi 5 has no hardware H.264 encoder)

RTSP (printed by add-device.sh, with real credentials filled in):

```bash
ffmpeg -f v4l2 -framerate 30 -video_size 720x576 -i /dev/video0 \
  -c:v libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p \
  -g 30 -b:v 2M -maxrate 2M -bufsize 2M \
  -f rtsp -rtsp_transport tcp \
  "rtsp://<device-id>:<publish_pass>@10.8.0.1:8554/<device-id>"
```

SRT alternative (lower jitter on lossy links):

```bash
ffmpeg -f v4l2 -framerate 30 -video_size 720x576 -i /dev/video0 \
  -c:v libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p \
  -g 30 -b:v 2M -maxrate 2M -bufsize 2M -f mpegts \
  "srt://10.8.0.1:8890?streamid=#!::m=publish,r=<device-id>,u=<device-id>,s=<publish_pass>&latency=200000&pkt_size=1316"
```

On the Pi, wrap this command in a `systemd` service with `Restart=always` so it auto-reconnects after
reboots or link drops (Pi-side setup is out of scope for this server repo).

## Access the dashboard

Operators connect as a wg-easy WireGuard client, then browse to `http://10.8.0.1:8080` and log in
with `DASH_USER` / `DASH_PASS` from `.env`.

## Ports & interfaces

| Port | Proto | Service | Bind |
|---|---|---|---|
| 8554 | tcp | RTSP ingest | 10.8.0.1 (WG) |
| 8890 | udp | SRT ingest | 10.8.0.1 (WG) |
| 8889 | tcp | WebRTC/WHEP | 10.8.0.1 (WG) |
| 8189 | udp | WebRTC ICE | advertises 10.8.0.1 |
| 9997 | tcp | Control API | 127.0.0.1 only |
| 8080 | tcp | Dashboard | 10.8.0.1 (WG) |

WireGuard handshake (wg-easy) is the only public-facing port. Optional `ufw` rules are printed at
the end of `install.sh` — allow the above only on `wg0`.

## Telemetry hook (optional, stubbed)

Pi (or any WG client) can POST JSON to `http://10.8.0.1:8080/api/telemetry/<device-id>`:

```bash
curl -X POST http://10.8.0.1:8080/api/telemetry/pi-01 \
  -H 'Content-Type: application/json' -d '{"rssi":-58,"freq":"5.8G","alarm":false}'
```

The latest payload shows on the device tile. Set `TELEMETRY_TOKEN` in `.env` to require
`Authorization: Bearer <token>`. No live source is wired yet — this is a ready hook.

## Scan service (HackRF)

A Pi-side daemon (`agent/scan/`) sweeps 1.2/2.4/5.8 GHz with a HackRF One, detects active video
carriers, classifies analog vs digital, and POSTs detections to the dashboard telemetry hook
(`/api/telemetry/<scanner-id>`) plus a local state file (`/run/fpv-scan/scan.json`). Analog
detections are receivable on rx5808 (later sub-project); digital ones are flagged only.

### Install on the Pi
```bash
sudo apt-get install -y hackrf
cd /opt/fpv-video-stream/agent/scan
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
hackrf_info                       # confirm the HackRF is detected
sudo cp /opt/fpv-video-stream/systemd/fpv-scan.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fpv-scan
journalctl -u fpv-scan -f
```

### Develop without a HackRF (replay mode)
Synthetic fixtures for all three bands are committed under `tests/fixtures/`, so replay mode runs
out of the box. POST failures to an unreachable dashboard are non-fatal (the local state file is
still written); point `SCAN_SERVER_URL` at a dummy to keep the logs quiet:
```bash
SCAN_SOURCE=replay SCAN_FIXTURES_DIR=./tests/fixtures \
  SCAN_SERVER_URL=http://127.0.0.1:1 SCAN_STATE_PATH=./scan.json python main.py
```
Regenerate the synthetic fixtures any time with `python tests/fixtures/generate_fixtures.py`.

### Record real fixtures on the Pi (for threshold tuning)
Replace the synthetic fixtures with real captures for each band, then tune `Thresholds` in
`config.py` and re-run `pytest`:
```bash
# 5.8 GHz
hackrf_sweep -f 5645:5945 -w 100000 -1 > tests/fixtures/sweep_5.8G.csv
hackrf_transfer -r tests/fixtures/iq_5.8G.bin -f 5800000000 -s 20000000 -n 2000000 -a 1
# 1.2 GHz
hackrf_sweep -f 1080:1360 -w 100000 -1 > tests/fixtures/sweep_1.2G.csv
hackrf_transfer -r tests/fixtures/iq_1.2G.bin -f 1200000000 -s 20000000 -n 2000000 -a 1
# 2.4 GHz
hackrf_sweep -f 2370:2510 -w 100000 -1 > tests/fixtures/sweep_2.4G.csv
hackrf_transfer -r tests/fixtures/iq_2.4G.bin -f 2440000000 -s 20000000 -n 2000000 -a 1
```

## Public TLS access (later, optional)

Not enabled in this iteration. To expose the dashboard publicly, put Caddy (automatic TLS) in front
of `127.0.0.1:8080` with a login, point a domain at the server's public IP, open 80/443, and keep
the MediaMTX control API internal. The dashboard already runs behind a login.

## Operations

```bash
systemctl status mediamtx fpv-dashboard
journalctl -u mediamtx -f
journalctl -u fpv-dashboard -f
node bin/gen-mediamtx.js && systemctl reload mediamtx   # after manual devices.yml edits
```

## Development / tests

```bash
npm install
npm test          # node --test over lib/ and dashboard/
```
