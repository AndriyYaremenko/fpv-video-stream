# Public HTTPS for the Dashboard (rerfpv.ksm.in.ua) — Design Spec

**Date:** 2026-06-17
**Author:** andriy@netlife.com.ua
**Status:** Approved (pending written-spec review)

## 1. Context & Goal

The dashboard currently serves plain HTTP on `10.8.0.1:8080`, reachable only over WireGuard
(WG-only by design). The operator wants it on `https://rerfpv.ksm.in.ua` with a valid certificate
(a secure context — useful for `navigator.clipboard`, autoplay/secure-context hygiene; the Web Audio
beep itself works on HTTP, but HTTPS is wanted regardless).

The server already runs **traefik** (`hopeful_black`) owning host `:80`/`:443`, with: a static
`/root/traefik.yml` (web → 301 → websecure; ACME `letsEncrypt` resolver via http-01 on `:80`,
storage `/etc/traefik/acme.json`), a **file provider** watching `/root/custom/`, the docker provider,
and `/etc/letsencrypt` mounted. Adding a domain follows the existing `/root/custom/zab.yml` pattern.

DNS `rerfpv.ksm.in.ua` already resolves to the public IP **193.242.163.139**.

## 2. Confirmed decisions

| Decision | Value |
|---|---|
| Access model | **Public**, behind the existing dashboard login (`DASH_USER`/`DASH_PASS`). No extra traefik Basic-Auth / IP-allowlist (operator's choice). |
| TLS termination | Existing **traefik**, cert auto-issued by its `letsEncrypt` resolver (http-01). |
| Routing | traefik **file provider** — new `/root/custom/rerfpv.yml` (consistent with `zab.yml`). |
| Reaching the dashboard | Dashboard binds **0.0.0.0:8080** inside wg-easy's netns; traefik proxies to wg-easy's bridge IP **172.17.0.3:8080**. |
| wg-easy / mediamtx | **Untouched.** No restart of traefik (file-watch hot-reload). |

## 3. Architecture & data flow

```
Browser → https://rerfpv.ksm.in.ua
   DNS → 193.242.163.139  →  traefik :443 (LE cert for rerfpv.ksm.in.ua, auto-issued)
        │  http :80 → 301 → https  (existing global redirect)
        ▼  file-provider route: Host(rerfpv.ksm.in.ua) → service
   http://172.17.0.3:8080  (wg-easy container's bridge IP)
        ▼
   dashboard (listening 0.0.0.0:8080 inside wg-easy netns)

WG clients (unchanged):  http://10.8.0.1:8080
```

- The dashboard listening on `0.0.0.0` adds bridge reachability (for traefik) while keeping
  `10.8.0.1` for WG clients. Port 8080 is **not** published to the host (wg-easy publishes only
  51820/51821), so the only public path is traefik :443 (TLS + login).
- traefik (bridge `172.17.0.2`) and wg-easy (bridge `172.17.0.3`) are on the same default bridge, so
  traefik can reach `172.17.0.3:8080` by IP.

## 4. Changes

### 4.1 Dashboard listen address (server `.env` for the fpv compose)
- Add `DASH_HOST=0.0.0.0` to the server's `~/fpv-video-stream/.env` (keep `WG_IP=10.8.0.1`).
  `server.js` already reads `DASH_HOST` (default `10.8.0.1`) for `app.listen(port, host)`.
- Recreate only the dashboard: `docker compose up -d --no-deps dashboard`.

### 4.2 traefik route (`/root/custom/rerfpv.yml` on the server)
```yaml
http:
  routers:
    rerfpv:
      entryPoints:
        - websecure
      service: service-rerfpv
      rule: "Host(`rerfpv.ksm.in.ua`)"
      tls:
        certResolver: letsEncrypt
  services:
    service-rerfpv:
      loadBalancer:
        servers:
          - url: "http://172.17.0.3:8080"
        passHostHeader: true
```
traefik watches `/root/custom` → hot-reload; the `letsEncrypt` resolver issues the cert on first
request (http-01 via `:80`, which DNS + the open port satisfy). **No traefik restart.**

### 4.3 Repo documentation (reproducibility)
The server's `.env` and `/root/custom/` are not in the repo, so document the setup:
- `deploy/traefik/rerfpv.yml.example` — the file above (sample for the operator).
- `README.md` — a "Public HTTPS (optional)" section: set `DASH_HOST=0.0.0.0`, drop the traefik
  file, the caveats below.

## 5. Caveats / limitations

- **Camera video does not play over the public HTTPS dashboard.** The WHEP player pulls video from
  `http://10.8.0.1:8889` (WG-only WebRTC + mixed-content on an HTTPS page → browser blocks it). Over
  `https://rerfpv.ksm.in.ua` the **UI, login, status, Spectrum panel, and the audio alert** work;
  **camera tiles stay black**. For live video, use WG (`http://10.8.0.1:8080`). Public WHEP is a
  separate, larger task (out of scope).
- The session cookie is **not** flagged `Secure` (so it works over both WG-HTTP and public-HTTPS).
- The traefik upstream uses wg-easy's bridge IP `172.17.0.3`. If wg-easy is ever recreated and its
  bridge IP changes, update `/root/custom/rerfpv.yml` (same fragility class already noted for the
  netns coupling).
- Public exposure is gated only by the dashboard login — ensure `DASH_PASS` is strong.

## 6. Verification

On the server after deploy:
- `curl -sI https://rerfpv.ksm.in.ua/login.html` → `HTTP/2 200`, valid cert (chain for
  rerfpv.ksm.in.ua), and `curl -sI http://rerfpv.ksm.in.ua/` → `301` to https.
- Cert present: `rerfpv.ksm.in.ua` appears in `/etc/traefik/acme.json`.
- WG path intact: `docker exec fpv-video-stream-dashboard-1 wget -qO- http://10.8.0.1:8080/login.html`
  still returns the login page.
- `wg-easy` and `mediamtx` containers untouched (same uptime/status).

This is an ops/config change — no automated tests; verification is the curl/exec checks above.

## 7. Out of scope (YAGNI)

- Public WHEP/WebRTC video (needs HTTPS/WSS MediaMTX + reachable host + non-WG ICE).
- Extra auth layers (Basic-Auth / IP-allowlist) — declined.
- DNS-01 / WG-only HTTPS variant — declined (DNS already points public).
- Migrating the dashboard out of wg-easy's netns.
