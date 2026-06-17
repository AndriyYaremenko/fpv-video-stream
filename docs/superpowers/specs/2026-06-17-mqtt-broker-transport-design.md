# MQTT Broker + Transport + Topic Contract (SP-A) — Design Spec

**Date:** 2026-06-17
**Author:** andriy@netlife.com.ua
**Status:** Approved (pending written-spec review)

## 1. Context & Goal

Replace the scan data path (Pi `POST /api/telemetry/<id>` → in-memory store → SSE → dashboard
panel) with **MQTT pub/sub**: the Pi publishes to a broker; the browser subscribes to the **same
broker directly over WebSocket** (no separate WS server). This is the foundation sub-project (A) of
a 3-part effort:

- **A (this spec):** broker + transport (TLS WebSocket via the existing traefik) + the topic/payload
  **contract** that B and C implement against.
- **B (later):** the Pi scan service publishes to the broker (replacing its HTTP reporter).
- **C (later):** the dashboard subscribes over WSS and renders detections + a **waterfall** (bands
  data-driven from the spectrum frames; custom bands later).

Existing constraints carried in: the dashboard is public HTTPS at `rerfpv.ksm.in.ua` via traefik
(so browser MQTT-over-WS **must be WSS** — `ws://` is mixed-content-blocked); the Pi is a WG client;
the dashboard + mediamtx run in **wg-easy's netns**; **wg-easy must not be touched**; camera status
stays on the existing SSE path — only **scan data** moves to MQTT.

## 2. Confirmed decisions

| Decision | Value |
|---|---|
| Broker | **mosquitto** container in the fpv compose, `network_mode: container:wg-easy`. |
| Pi → broker | MQTT `:1883` over WG (`10.8.0.1:1883`); 1883 **not** host-published. |
| Browser → broker | MQTT-over-WebSocket `:9001`, reached by traefik on wg-easy's bridge IP, exposed as **`wss://rerfpv.ksm.in.ua/mqtt`** (PathPrefix route, same cert). |
| Topics | Namespaced per scanner: `fpv/<id>/detection`, `fpv/<id>/spectrum`, `fpv/<id>/status`. Dashboard subscribes `fpv/+/...`. |
| Migration | **Fully replace** the HTTP scan path (Pi reporter + SSE scan render removed in B/C); cameras stay on SSE. |
| Auth | Password file + ACL: `pub` (Pi, publish `fpv/<id>/#` only), `sub` (browser, subscribe `fpv/#` only); `allow_anonymous false`. |
| Spectrum frame | **Self-describing** (each band carries `low_mhz`/`high_mhz`) → dashboard renders any bands → custom bands free. |
| Presence | Retained `fpv/<id>/status` + **LWT** (`{online:false}`) so disconnect flips offline. |

## 3. Architecture & data flow

```
Pi scan service ──MQTT :1883 (WG 10.8.0.1)──▶  mosquitto  ──WS :9001──▶ traefik ──WSS──▶ browser
  pub fpv/hackrf/{detection,spectrum,status}    (wg-easy netns)   (bridge 172.17.0.3)  wss://rerfpv.ksm.in.ua/mqtt
                                                persistence ON (retained)
  dashboard server ──GET /api/mqtt (login-gated) → { url, user:"sub", pass } to the browser
```

- mosquitto runs in wg-easy's netns binding `0.0.0.0`: reachable at `10.8.0.1:1883` for the Pi (WG)
  and at the wg-easy **bridge IP `172.17.0.3:9001`** for traefik. Neither 1883 nor 9001 is published
  to the host; the only public path is traefik's WSS.
- The browser learns the broker URL + `sub` credentials from the dashboard (an authenticated
  endpoint), mirroring how `/api/config` hands out the WHEP read creds today.

## 4. Topic / payload contract

`<id>` = scanner device id (e.g. `hackrf`). All payloads are JSON; `ts` is epoch seconds.

| Topic | QoS | Retain | Payload |
|---|---|---|---|
| `fpv/<id>/detection` | 1 | yes | `{ "scanner_id", "ts", "detections":[{ "band","center_mhz","bandwidth_mhz","power_dbm","snr_db","class","confidence","channel" }], "occupancy":{ "<band>": <0..1> } }` |
| `fpv/<id>/spectrum` | 0 | yes (last) | `{ "scanner_id", "ts", "bands":[{ "id","low_mhz","high_mhz","psd":[<dBm>…] }] }` — `psd` downsampled (~128 pts/band); index→freq via `low_mhz..high_mhz`. |
| `fpv/<id>/status` | 1 | yes | `{ "online": true, "ts" }` published on connect; **LWT** = retained `{ "online": false, "ts" }` set at connect, delivered by the broker on ungraceful disconnect. |

- `detection` retained → a fresh dashboard immediately shows current targets; QoS 1 → no missed
  events. `spectrum` QoS 0 (high-rate, lossy OK) but retained so a fresh dashboard draws the latest
  frame at once. `status` retained + LWT → online/offline without polling.
- The exact publish cadence is B's concern; the contract supports any rate (the waterfall in C
  appends whatever frames arrive).

## 5. Components / deliverables (SP-A only)

```
docker-compose.yml            (add `mosquitto` service: network_mode container:wg-easy, config/data volumes, depends on wg-easy)
mosquitto/mosquitto.conf      (listeners 1883 mqtt + 9001 websockets; allow_anonymous false; password_file; acl_file; persistence on)
mosquitto/acl                 (pub → write fpv/#; sub → read fpv/#)   (see §6)
mosquitto/passwd              (git-ignored; generated with `mosquitto_passwd` from the .env creds — a documented deploy step / small gen script)
dashboard/server.js           (add GET /api/mqtt — login-gated → { url, user, pass }; config reads MQTT_* env)
deploy/traefik/rerfpv-mqtt.yml.example   (traefik PathPrefix `/mqtt` → http://172.17.0.3:9001, stripPrefix, same host/cert)
.env.example                  (MQTT_PUB_USER/PASS, MQTT_SUB_USER/PASS, MQTT_WSS_URL)
test/server.test.js           (add: /api/mqtt requires auth; returns url+sub creds when authed)
README.md                     (MQTT broker + transport section)
```

`runtime`/secrets stay git-ignored. The broker data dir (retained-message persistence) is a named
volume or `./mosquitto/data` (git-ignored).

## 6. Auth model

- **mosquitto.conf:** `allow_anonymous false`, `password_file /mosquitto/config/passwd`,
  `acl_file /mosquitto/config/acl`, `persistence true`, `persistence_location /mosquitto/data/`.
- **Users (passwd):** `pub` and `sub`, passwords from `.env` (`MQTT_PUB_PASS`, `MQTT_SUB_PASS`).
- **ACL:** `pub` → `topic write fpv/#` (publish); `sub` → `topic read fpv/#` (subscribe). (Per-scanner
  write scoping is a future tightening; one shared `pub` user is acceptable for now since the Pi is
  trusted and reaches the broker only over WG.)
- **Browser creds:** `GET /api/mqtt` (behind the dashboard login) returns
  `{ url: MQTT_WSS_URL, user: "sub", pass: MQTT_SUB_PASS }`. The `sub` user is subscribe-only, so
  exposing it to authenticated operators is low-risk.
- **Pi creds:** `MQTT_PUB_USER`/`MQTT_PUB_PASS` configured on the Pi (B's concern).

## 7. Security

- Broker WS is public (via traefik WSS) → gated by `sub` auth (subscribe-only). MQTT `:1883` is
  WG-only (not host-published). No new public ports beyond traefik :443.
- Credentials live in `.env` and `mosquitto/passwd` (mode-restricted, git-ignored), not in code.
- traefik terminates TLS for the WS; the broker speaks plain WS internally on the bridge.

## 8. Testing / verification

- **Automated (node --test):** `/api/mqtt` returns `401` unauthenticated; returns
  `{url,user,pass}` for an authed session (server-side, like the existing `/api/config` test).
- **Ops verification (live):** `mosquitto_pub` (as `pub`, over WG `10.8.0.1:1883`) → `mosquitto_sub`
  (as `sub`) receives it; ACL denies `sub` publishing; a `mqtt.js` WSS client to
  `wss://rerfpv.ksm.in.ua/mqtt` connects and receives a retained test message; wg-easy/mediamtx/the
  dashboard remain untouched.

## 9. Out of scope (this sub-project)

- The Pi publisher (SP-B) and the dashboard subscriber + waterfall (SP-C).
- Removing the HTTP reporter / SSE scan render (done in B/C as the cutover lands).
- Per-scanner ACL write-scoping, MQTT-over-TLS on :1883 (WG already private), broker clustering.
- A custom-bands configuration UI (the self-describing spectrum frame enables it; UI is later).

## 10. Assumptions

- traefik's existing `letsEncrypt` resolver + the `rerfpv.ksm.in.ua` cert already cover the WSS route
  (same host); the PathPrefix `/mqtt` router gets higher priority than the catch-all dashboard route.
- mosquitto's websockets listener serves at the root path; traefik `stripPrefix /mqtt` maps the
  public `/mqtt` path onto it.
- The wg-easy bridge IP `172.17.0.3` is stable (same caveat as the dashboard route; update the
  traefik file if wg-easy is recreated with a new IP).
