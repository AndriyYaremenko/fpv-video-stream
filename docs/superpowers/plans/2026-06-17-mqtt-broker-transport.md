# MQTT Broker + Transport (SP-A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a mosquitto broker (in wg-easy's netns) reachable by the Pi over WG (`:1883`) and by the browser over WSS (`wss://rerfpv.ksm.in.ua/mqtt` via traefik), with an auth model and a dashboard endpoint that hands subscribe credentials to the logged-in browser — the foundation the Pi publisher (SP-B) and dashboard subscriber (SP-C) build on.

**Architecture:** A `mosquitto` compose service (`network_mode: container:wg-easy`) with two listeners — `1883` MQTT and `9001` WebSocket — `allow_anonymous false`, a password file (`pub`/`sub`) and ACL. traefik adds a `PathPrefix(/mqtt)` WSS route to the broker's WS port. The dashboard gains `GET /api/mqtt` (login-gated) returning the WSS URL + `sub` creds.

**Tech Stack:** eclipse-mosquitto:2, Docker Compose (`network_mode: container:wg-easy`), traefik file provider, Node/Express dashboard, `node --test`.

Spec: `docs/superpowers/specs/2026-06-17-mqtt-broker-transport-design.md`

---

## File Structure

```
dashboard/server.js                       (change: GET /api/mqtt + config.mqtt from env)
test/server.test.js                       (add: /api/mqtt auth + payload tests)
mosquitto/mosquitto.conf                  (new: listeners 1883+9001, auth, acl, persistence)
mosquitto/acl                             (new: pub→write fpv/#, sub→read fpv/#)
mosquitto/gen-passwd.sh                   (new: build mosquitto/passwd from .env via the mosquitto image)
docker-compose.yml                        (change: add `mosquitto` service)
deploy/traefik/rerfpv-mqtt.yml.example    (new: PathPrefix /mqtt → broker WS, stripPrefix, same cert)
.env.example                              (change: MQTT_* vars)
.gitignore                                (change: ignore mosquitto/passwd + mosquitto/data)
README.md                                 (change: MQTT broker section)
```

Only `/api/mqtt` is unit-tested (`node --test`). The broker/traefik/compose are config artifacts — there is **no docker on the dev box**, so they are created carefully and verified at deploy (commands given). The topic/payload contract is documented for SP-B/SP-C; it is not exercised here.

---

## Task 1: Dashboard `GET /api/mqtt` endpoint

**Files:**
- Modify: `dashboard/server.js` (add the route in `createApp`; add `mqtt` to the `config` in `start()`)
- Test: `test/server.test.js`

- [ ] **Step 1: Write the failing tests**

Append to `test/server.test.js`:

```javascript
test('GET /api/mqtt requires auth', async () => {
  const { server, base } = await startServer();
  const res = await fetch(`${base}/api/mqtt`, { redirect: 'manual' });
  assert.equal(res.status, 401);
  server.close();
});

test('authed /api/mqtt returns the WSS url + sub creds', async () => {
  const { server, base } = await startServer();
  const login = await fetch(`${base}/login`, {
    method: 'POST', redirect: 'manual',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: 'user=op&pass=pw',
  });
  const cookie = login.headers.getSetCookie().map((c) => c.split(';')[0]).join('; ');
  const res = await fetch(`${base}/api/mqtt`, { headers: { cookie } });
  const body = await res.json();
  assert.equal(res.status, 200);
  assert.equal(body.url, 'wss://rerfpv.ksm.in.ua/mqtt');
  assert.equal(body.user, 'sub');
  assert.equal(body.pass, 'subpw');
  server.close();
});
```

- [ ] **Step 2: Add `mqtt` to the test `config`**

In `test/server.test.js`, the module-level `const config = { ... }` is passed to `createApp`. Add an `mqtt` field to it (place it next to `telemetryToken`):

```javascript
  mqtt: { url: 'wss://rerfpv.ksm.in.ua/mqtt', user: 'sub', pass: 'subpw' },
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `npm test`
Expected: the two new tests FAIL — `/api/mqtt` 404s (no route) so the auth test gets 404 not 401, and the payload test fails.

- [ ] **Step 4: Implement the route + config**

In `dashboard/server.js`, inside `createApp`, add the route right after the existing `GET /api/config` handler:

```javascript
  app.get('/api/mqtt', requireAuth, (req, res) => {
    res.json({ url: config.mqtt?.url || '', user: config.mqtt?.user || 'sub', pass: config.mqtt?.pass || '' });
  });
```

In `start()`, add `mqtt` to the `config` object (next to `telemetryToken`):

```javascript
    mqtt: {
      url: env.MQTT_WSS_URL || '',
      user: env.MQTT_SUB_USER || 'sub',
      pass: env.MQTT_SUB_PASS || '',
    },
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `npm test`
Expected: PASS — both new tests green, rest of the suite unaffected.

- [ ] **Step 6: Commit**

```bash
git add dashboard/server.js test/server.test.js
git commit -m "feat(dashboard): GET /api/mqtt serves WSS url + sub creds to the logged-in browser" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: mosquitto config + ACL

**Files:**
- Create: `mosquitto/mosquitto.conf`
- Create: `mosquitto/acl`

- [ ] **Step 1: Create `mosquitto/mosquitto.conf`**

```
# mosquitto/mosquitto.conf — FPV scan broker (runs in wg-easy's netns).
persistence true
persistence_location /mosquitto/data/

allow_anonymous false
password_file /mosquitto/config/passwd
acl_file /mosquitto/config/acl

# MQTT for the Pi over WireGuard (10.8.0.1:1883); not published to the host.
listener 1883
protocol mqtt

# MQTT-over-WebSocket for the browser, fronted by traefik (WSS).
listener 9001
protocol websockets
```

- [ ] **Step 2: Create `mosquitto/acl`**

```
# mosquitto/acl — pub publishes scan data, sub reads it.
user pub
topic write fpv/#

user sub
topic read fpv/#
```

- [ ] **Step 3: Verify**

These are config files (no unit test; no docker on the dev box). Confirm they exist and the listener/auth/acl directives match the spec §6.

Run: `ls mosquitto/mosquitto.conf mosquitto/acl`
Expected: both listed.

- [ ] **Step 4: Commit**

```bash
git add mosquitto/mosquitto.conf mosquitto/acl
git commit -m "feat(mqtt): mosquitto config (mqtt+ws listeners, auth, acl, persistence)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: passwd generator + gitignore

**Files:**
- Create: `mosquitto/gen-passwd.sh`
- Modify: `.gitignore`

- [ ] **Step 1: Create `mosquitto/gen-passwd.sh`**

```bash
#!/usr/bin/env bash
# Generate mosquitto/passwd from the MQTT_*_USER/PASS in ../.env, using the mosquitto image.
# Run on the server (has docker) from the repo root: bash mosquitto/gen-passwd.sh
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
: "${MQTT_PUB_USER:?}" "${MQTT_PUB_PASS:?}" "${MQTT_SUB_USER:?}" "${MQTT_SUB_PASS:?}"
docker run --rm -v "$(pwd)/mosquitto:/m" eclipse-mosquitto:2 sh -c "
  mosquitto_passwd -b -c /m/passwd '$MQTT_PUB_USER' '$MQTT_PUB_PASS' &&
  mosquitto_passwd -b /m/passwd '$MQTT_SUB_USER' '$MQTT_SUB_PASS'
"
echo "wrote mosquitto/passwd (users: $MQTT_PUB_USER, $MQTT_SUB_USER)"
```

- [ ] **Step 2: Ignore the generated passwd + data dir**

Append to `.gitignore`:

```
mosquitto/passwd
mosquitto/data/
```

- [ ] **Step 3: Verify**

Run: `ls mosquitto/gen-passwd.sh && grep -q 'mosquitto/passwd' .gitignore && echo OK`
Expected: prints the path and `OK`.

- [ ] **Step 4: Commit**

```bash
git add mosquitto/gen-passwd.sh .gitignore
git commit -m "feat(mqtt): passwd generator + gitignore secrets/data" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: docker-compose mosquitto service

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add the `mosquitto` service**

Append this service under `services:` in `docker-compose.yml` (sibling of `mediamtx`/`dashboard`):

```yaml
  mosquitto:
    image: eclipse-mosquitto:2
    network_mode: "container:wg-easy"
    volumes:
      - ./mosquitto/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro
      - ./mosquitto/passwd:/mosquitto/config/passwd:ro
      - ./mosquitto/acl:/mosquitto/config/acl:ro
      - ./mosquitto/data:/mosquitto/data
    restart: unless-stopped
```

(Like `mediamtx`/`dashboard`, it joins wg-easy's netns; `1883`/`9001` bind there and are not published to the host. `mosquitto/passwd` must exist first — generated by `gen-passwd.sh` at deploy.)

- [ ] **Step 2: Verify YAML shape**

No docker on the dev box. Sanity-check indentation/structure (2-space, sibling service).

Run: `grep -nA8 'mosquitto:' docker-compose.yml`
Expected: the new service block, correctly indented under `services:`.

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(mqtt): add mosquitto service (wg-easy netns)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: traefik route sample + .env.example + README

**Files:**
- Create: `deploy/traefik/rerfpv-mqtt.yml.example`
- Modify: `.env.example`
- Modify: `README.md`

- [ ] **Step 1: Create `deploy/traefik/rerfpv-mqtt.yml.example`**

```yaml
# Sample traefik file-provider config to expose the mosquitto WebSocket as WSS.
# Deploy: copy to traefik's watched dir (e.g. /root/custom/rerfpv-mqtt.yml). traefik hot-reloads.
# The cert for rerfpv.ksm.in.ua (issued for the dashboard route) covers this same host.
# Verify/refresh the wg-easy bridge IP (172.17.0.3) with:
#   docker inspect wg-easy --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'

http:
  middlewares:
    mqtt-strip:
      stripPrefix:
        prefixes: ["/mqtt"]

  routers:
    rerfpv-mqtt:
      entryPoints:
        - websecure
      rule: "Host(`rerfpv.ksm.in.ua`) && PathPrefix(`/mqtt`)"
      priority: 100          # higher than the catch-all dashboard router
      middlewares:
        - mqtt-strip
      service: service-rerfpv-mqtt
      tls:
        certResolver: letsEncrypt

  services:
    service-rerfpv-mqtt:
      loadBalancer:
        servers:
          - url: "http://172.17.0.3:9001"   # wg-easy bridge IP : mosquitto WS port
        passHostHeader: true
```

(traefik upgrades the WebSocket automatically; `stripPrefix /mqtt` maps the public `/mqtt` path onto the broker's root WS listener. The browser connects to `wss://rerfpv.ksm.in.ua/mqtt`.)

- [ ] **Step 2: Add MQTT vars to `.env.example`**

Append to `.env.example`:

```
# MQTT broker (scan data pub/sub). Generate mosquitto/passwd from these via mosquitto/gen-passwd.sh.
MQTT_WSS_URL=wss://rerfpv.ksm.in.ua/mqtt
MQTT_PUB_USER=pub
MQTT_PUB_PASS=change-me-pub
MQTT_SUB_USER=sub
MQTT_SUB_PASS=change-me-sub
```

- [ ] **Step 3: Add a README section**

Append a `## MQTT broker (scan data)` section to `README.md`:

````markdown
## MQTT broker (scan data)

Scan data (detections + spectrum) flows over MQTT instead of HTTP. A `mosquitto` container runs in
wg-easy's netns: the Pi publishes over WireGuard (`10.8.0.1:1883`); the browser subscribes over WSS
(`wss://rerfpv.ksm.in.ua/mqtt`, fronted by traefik). Topics (namespaced per scanner `<id>`):

- `fpv/<id>/detection` — QoS 1, retained — structured events `{detections[], occupancy}`.
- `fpv/<id>/spectrum` — QoS 0, retained — self-describing spectrum frames `{bands:[{id,low_mhz,high_mhz,psd}]}`.
- `fpv/<id>/status` — retained + LWT — presence `{online}`.

Deploy on the server:
```bash
cd /home/andriy/fpv-video-stream
# set MQTT_* in .env, then build the password file and start the broker:
bash mosquitto/gen-passwd.sh
docker compose up -d --no-deps mosquitto
# expose the WS as WSS via traefik (one-time): copy the sample, adjust the wg-easy bridge IP if needed
sudo cp deploy/traefik/rerfpv-mqtt.yml.example /root/custom/rerfpv-mqtt.yml   # traefik hot-reloads
```
The dashboard serves the browser's subscribe creds at `GET /api/mqtt` (login-gated). The Pi uses
`MQTT_PUB_USER`/`MQTT_PUB_PASS` (configured Pi-side in SP-B).
````

- [ ] **Step 4: Verify + full suite**

Run: `ls deploy/traefik/rerfpv-mqtt.yml.example && grep -q MQTT_WSS_URL .env.example && echo OK`
Expected: prints the path and `OK`.

Run: `npm test`
Expected: PASS (the `/api/mqtt` tests from Task 1 + the rest).

- [ ] **Step 5: Commit**

```bash
git add deploy/traefik/rerfpv-mqtt.yml.example .env.example README.md
git commit -m "docs(mqtt): traefik WSS route sample + .env + README" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage** (spec § → task):
- §2/§3 broker in wg-easy netns, 1883 + 9001 → Task 2 (conf) + Task 4 (compose). Browser WSS via traefik PathPrefix `/mqtt` → Task 5 (traefik sample). Pi over WG → covered by the 1883 listener (Task 2); 1883 not host-published → Task 4 (network_mode, no ports).
- §4 contract (detection/spectrum/status, QoS, retain, self-describing) → documented in the spec + README (Task 5); implemented by SP-B/SP-C (out of scope here, per §9).
- §5 deliverables → Tasks 1–5 map 1:1 (server.js+test, mosquitto.conf+acl, gen-passwd+gitignore, compose, traefik+env+README).
- §6 auth (pub/sub, ACL, allow_anonymous false, /api/mqtt creds) → Task 2 (acl + conf auth) + Task 3 (passwd) + Task 1 (/api/mqtt).
- §7 security (WG-only 1883, WSS-only public, secrets git-ignored) → Task 3 (gitignore) + Task 4 (no host ports) + Task 5 (traefik).
- §8 testing: automated /api/mqtt → Task 1; ops verification → README/deploy (Task 5) + the post-merge deploy.

**Placeholder scan:** no TBD/TODO; config files have full content; the only non-code-test tasks (config) are explicitly verified by inspection + deploy, with exact deploy commands.

**Type/name consistency:** `config.mqtt = {url,user,pass}`; `GET /api/mqtt` returns `{url,user,pass}`; env `MQTT_WSS_URL`/`MQTT_SUB_USER`/`MQTT_SUB_PASS` (and `MQTT_PUB_USER`/`MQTT_PUB_PASS` for the Pi/passwd); mosquitto users `pub`/`sub`; topics `fpv/<id>/{detection,spectrum,status}` — used identically across the plan and the spec.

**Note (deploy, not a plan task):** after merge, on the server — set `MQTT_*` in `.env`, `bash mosquitto/gen-passwd.sh`, `docker compose up -d --no-deps mosquitto`, copy the traefik sample to `/root/custom/`, then verify (`mosquitto_pub`/`sub` over WG, a WSS `mqtt.js` connect). wg-easy untouched.
