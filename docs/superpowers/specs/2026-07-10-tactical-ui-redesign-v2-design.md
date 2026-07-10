# Tactical UI Redesign v2 (on current main) — Design

**Date:** 2026-07-10
**Status:** Approved design, ready to plan. Branch `feat/tactical-ui-redesign-v2` off `origin/main`.

## Why v2
The first redesign (`feat/tactical-ui-redesign`, PR #31) was built on a **stale base**
(`feat/hackrf-carrier-demod`, off old `main`) and did not include ~21 dashboard commits that landed on
`main`: the **FPV Viewer panel**, the **frames gallery (🖼️)**, **SDR view controls**, and the
**generation-token WHEP** lifecycle. PR #31 conflicts and would regress live features. v2 re-applies the
tactical theme + sidebar/screens structure **on top of current `main`**, preserving every existing feature.

## Goal
Re-skin + restructure `dashboard/public/` into the tactical "AERO-SHIELD" look (design-system
"Tactical Spectrum Command"): fixed left **sidebar + 5 screens**, waterfalls removed, node TEMP telemetry
slots for the future, **no loss of any current feature**. Front-end only — no server/`lib`/Pi/MQTT/API
changes.

## Reuse from the v1 branch (`feat/tactical-ui-redesign`)
Retrieve verbatim (via `git show feat/tactical-ui-redesign:<path>`) and adapt as noted:
- `dashboard/public/styles.css` — tactical tokens + components (extend for viewer/gallery/view-controls).
- `dashboard/public/vendor/fonts/*.woff2` — Geist + JetBrains Mono (self-hosted).
- `dashboard/dev-serve.mjs` — dev static server; `dashboard/public/dev-preview.html` — styleguide.
- `dashboard/public/views/components.js` — atoms (`el, pip, cornerCard, occupancyStrip, detectionCard, tempSlot, fmtBitrate, fmtUptime, escapeHtml`).
- `dashboard/public/router.js` — hash router (extend to 5 routes).
- `dashboard/public/modals.js` — viewer/image modal + add/edit/creds/scanner-info (journal+frames become screens, not modals).
- `dashboard/public/fixtures.js` + the `?preview=1` seam in `app.js` — extend fixtures for viewer/view-state/frames.

## Keep unchanged from `main` (import only — DO NOT modify)
`whep.js`, `alert.js`, `mqtt-scan.js`, `rx5808-channels.js`, `viewer.js`, and the server/`lib`.
`viewer.js` (FPV Viewer state machine) and `frames-gallery.js` (pure query/HTML builders) are reused;
`frames-gallery.js`'s HTML builder is re-themed but its `buildFramesQuery`/`BAND_PRESETS`/`scannerOptions`
logic is kept.

## Screens & feature mapping

**Sidebar** (brand `FPV · SDR MONITOR`, status pill `ОПЕРАЦІЙНИЙ · N/N`, operator + Вийти) → 5 nav items:

### 1. Панель (`#/dashboard`)
Camera feeds grid (WHEP tiles, `players` Map lifecycle) + node telemetry strip (all devices: name,
ONLINE/OFFLINE, **TEMP `—°C`**, uptime; scanners also occupancy) + a compact active-detections count.
Tile actions (restart/creds/edit/del), tile-size slider, fullscreen video modal.

### 2. FPV Viewer (`#/viewer`, 🎯)
The primary watch surface, consolidating the current `#viewer-panel` + the useful parts of the per-scanner
spectrum block:
- **Merged detection list** (`viewer.js`: `viewerRows`/`viewerListHtml` re-themed) — live rows first
  (by power), then recent (dimmed, by recency); source-scanner badges, SNR, age; `is-viewing` highlight;
  click a row → start SDR view at that freq (via the routing scanner) **and** RX5808 nearest-tune on 5.8G
  (dual action preserved).
- **Shared in-panel WHEP player** (`#viewer-video`) keyed by **stream name** with the
  **generation-token retry chain** (`syncViewerPlayer`/`startViewerWhep`/`whepRetryDelay`, `playerKey`) —
  reused verbatim in behavior; `■ свіп` stop-sweep button; badge + error text.
- **Small live spectrum** of the active scanner+band (mini PSD line, **no waterfall**) with **canvas
  freq-pick** (click fills freq; on 5.8G also RX5808 nearest-tune) + a manual freq input + ▶/■.
- Latest recovered frame thumbnail(s) → open in the image modal.

### 3. Вузли (`#/nodes`)
Device management cards for all devices: id/kind, ONLINE/OFFLINE, **TEMP `—°C`**, uptime/bitrate,
scanners' occupancy + bands, RX5808 mode buttons + channel `<select>`, per-scanner SDR view controls
(freq input + ▶/■), and actions Edit / Creds-or-Info / Restart / Delete + ➕ Add. Absorbs the current
add/edit/creds/scanner-info modals + the scanner-block management row.

### 4. Детекції (`#/detections`, 📜)
Detection journal history table from `GET /api/detections?limit=` (time/scanner/band/freq/class/SNR/event),
with a refresh button. (Promoted from the current 📜 modal to a screen.)

### 5. Кадри (`#/frames`, 🖼️)
Frames gallery from `GET /api/frames` with the full filter toolbar (scanner / band-preset / standard /
SNR-min / time-range presets + custom datetime) and «Показати ще» pagination (cursor = oldest-shown `ts`),
tiles → fullscreen image modal. Reuse `frames-gallery.js` query/filter logic, re-theme its HTML to the
tactical gallery.

## Removed / dropped
- **Scrolling waterfalls** (`bandCell` waterfall canvas) — removed; keep the mini PSD line.
- The full-width `#spectrum-panel` block layout — dissolved into the Viewer + Nodes screens (its detection
  table is superseded by the merged Viewer list).
- The dead `telemetry` device field (never populated server-side) — dropped from the UI.

## Contracts to preserve (from the current code — do not break)
- **Store shape** `store[id]` incl. the `view` field `{ts, active, freq_mhz, until_ts, error, stream}`
  and `waterfalls` buffer (still populated by the reducer even though not rendered).
- **`whep.js`** `startWhep(video, url, user, pass, onDead?) → {close()}`; `close()` only blanks
  `video.srcObject` if the video still owns its stream (stale-close guard).
- **Viewer generation-token discipline** — every retry callback re-checks `viewerRetry !== retry` at
  entry, after await, and after success/failure; `playerKey` intentionally ignores freq/until_ts (smooth
  retune); `pickViewer` (route new starts to an idle scanner) ≠ `activeViewer` (whose session displays/stops).
- **View commands NOT retained** (`publishView` retain:false); RX5808 mode/channel retained.
- **ts-idempotent detection folding** (`applyDetections` guards on `seenTs[id]===ts`); **30 s ticker**
  re-renders the viewer so age labels/TTL advance with no MQTT traffic; **seed from `/api/detections`** on load.
- **Save/restore typed `.view-freq` input** across any full re-render (or use incremental updates).
- **Dual-action 5.8G**: a 5.8G canvas/row click both starts the view and RX5808 nearest-tunes.
- **Sound-alert arm needs a user gesture**; **clipboard copy has an execCommand fallback** (plain-HTTP/WG).

## Theme & fonts
Tactical Spectrum Command tokens (base `#121212`, panel `#1A1A1A`+1px, primary `#00FF41`, warn `#FF8C00`,
threat `#FF3131`, aux `#00E5FF`), sharp 0px corners, no shadow/gradient, corner-marks, pip badges,
data tables, `label-caps`. Self-hosted Geist (UI) + JetBrains Mono (all dynamic data). Login re-themed.

## Files
| File | Change |
|---|---|
| `dashboard/public/index.html` | New shell: sidebar + topbar + 5 `<section>` screens + modals. |
| `dashboard/public/styles.css` | v1 tactical theme + viewer/gallery/view-control components. |
| `dashboard/public/app.js` | Thin bootstrap: ctx + data stores (SSE, MqttScanClient, SoundAlerter, viewerState), players Map + viewer gen-token player, router + views + modals, 30 s viewer ticker, `?preview` seam. |
| `dashboard/public/router.js` | 5 routes (reused from v1). |
| `dashboard/public/modals.js` | viewer/image modal + device CRUD (reused from v1). |
| `dashboard/public/views/components.js` | atoms (reused from v1). |
| `dashboard/public/views/dashboard.js` | feeds + node strip + detections count. |
| `dashboard/public/views/viewer.js` | merged list + shared player + mini-spectrum + view controls. |
| `dashboard/public/views/nodes.js` | device cards + RX5808 + per-scanner view controls + CRUD. |
| `dashboard/public/views/detections.js` | journal history table. |
| `dashboard/public/views/frames.js` | gallery + filter toolbar + pagination (uses `frames-gallery.js`). |
| `dashboard/public/spectrum.js` | keep pure helpers (unit-tested) + `renderMiniSpectrum` (no waterfall); drop the old DOM block renderer. |
| `dashboard/public/login.html` | re-themed. |
| `dashboard/public/fixtures.js`, `dev-preview.html`, `dashboard/dev-serve.mjs` | dev harness (reused/extended). |
| unchanged | `whep.js`, `alert.js`, `mqtt-scan.js`, `rx5808-channels.js`, `viewer.js`, `frames-gallery.js` (logic). |

## Testing / acceptance
- `npm test` stays green (spectrum/viewer/frames-gallery/mqtt-scan/alert pure-helper suites — do not break exports).
- Dev-preview (`dashboard/dev-serve.mjs` → `index.html?preview=1` + `fixtures.js`) renders all 5 screens with
  sample data (incl. an active view-state fixture so the Viewer player slot + merged list show); controller
  verifies each screen + modals in Chrome, plus a `window.__rerender` reuse check (no WHEP/player leak).
- Live acceptance over WireGuard after deploy: real camera WHEP, FPV Viewer scan→click→watch, RX5808,
  journal, frames gallery.

## Out of scope
- Real per-node temperature wiring (UI slot only).
- Any server/`lib`/Pi changes.
- Public-HTTPS/traefik deploy config (unchanged; front-end assets ship the same way).
