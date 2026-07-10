# Tactical UI Redesign (AERO-SHIELD theme) — Design

**Date:** 2026-07-10
**Status:** Approved design, ready to plan (own branch off current work).

## Goal
Re-skin and restructure the dashboard front-end (`dashboard/public/`) to match the Stitch template
**"Multi-Node SDR Drone Monitor" / AERO-SHIELD Tactical** and its design system
**"Tactical Spectrum Command"**. Move from the current single-page layout (top bar + grid +
spectrum panel + modals) to a **sidebar + multi-screen** layout, drop the scrolling waterfalls, and
keep spectrum/node **telemetry** — building explicit UI slots for future **per-node temperature**.

This is a front-end-only change. All server APIs, MQTT/SSE/WHEP data plumbing, and device semantics
stay exactly as they are. No build system is introduced — the site remains zero-build static assets.

## Decisions (from the operator)
- **Structure: sidebar + separate screens** (faithful to the template), not a pure re-skin.
- **Waterfalls removed.** Keep one small live PSD line + occupancy bars as telemetry.
- **RX5808 controls: keep full functionality** (mode buttons + channel select + click-to-tune).
- **Login page** (`login.html`) is re-themed to match.
- **Fonts self-hosted** (WireGuard-only access; no external CDN).
- **Node temperature**: build the UI slot now (placeholder `—°C`); wiring real temps is out of scope.

## Architecture
Hash-router single-page shell that shows/hides three screen sections. Existing data sources are
reused unchanged:
- `GET /api/config`, `/api/devices`, `/api/mqtt`, `/api/detections`
- SSE `/api/stream` → device list (cameras + scanners: online/stats/telemetry)
- MQTT scan store via `mqtt-scan.js` → per-scanner `spectrum` / `detection` / `status` / `video`
- WHEP live video via `whep.js`

Routing: `#/dashboard` (default), `#/nodes`, `#/logs`. The router toggles section visibility and
lets each view subscribe to the shared data stores; no data re-fetch on navigation.

## Layout shell
- **Left sidebar (fixed):**
  - Brand: `FPV · SDR MONITOR`.
  - Status pill: `ОПЕРАЦІЙНИЙ · N/N онлайн` (green when all cameras online, else amber).
  - Nav items: **Панель** (`#/dashboard`), **Вузли** (`#/nodes`), **Детекції** (`#/logs`),
    with active-state indication.
  - Footer: operator identity (from `/api/config` read user or a static label) + **Вийти**.
- **Top bar (per screen):** screen title + global status (`N активних детекцій`) + screen-relevant
  actions (sound toggle 🔔/🔕, ➕ Додати вузол, tile-size slider on Dashboard).

## Screens & feature mapping

### 1. Панель (Dashboard) — `#/dashboard`
- **Center — camera feeds grid:** the existing WHEP tiles, re-themed (sharp corners, corner-marks,
  green `ONLINE` / grey `OFFLINE` badge, mono stats `bitrate · uptime · 👁 readers`). Click → the
  existing fullscreen video modal. Tile-size slider retained (persisted `tileMin`).
- **Right — «Активні детекції» (Threat Logs analog):** live list built from the MQTT scan store's
  detections across all scanners. Each entry is a card: class (color via `classColor`), `freq (band)`,
  `SNR dB`, scanner id, time; newly appeared detections highlighted (reuse `diffNewKeys`); sound
  alert on new detection retained (`SoundAlerter`). If a detection has an associated recovered frame,
  clicking opens it in the image modal.
- **Bottom — node telemetry strip:** compact card per device (camera + scanner): name,
  `ONLINE/OFFLINE`, **TEMP** slot (`—°C` placeholder), uptime; scanners also show band/range +
  mini occupancy bars.

### 2. Вузли (Node Management) — `#/nodes`
- Full node cards for **all** devices (cameras + scanners): id, kind, `ONLINE/OFFLINE`, uptime,
  latency/bitrate, **TEMP** slot; scanners additionally show bands + occupancy and the **RX5808
  controls** (mode buttons + channel `<select>`). Per-card actions: **Редагувати**, **Креди/Інфо**,
  **Перезапуск** (restart the view/player), **Видалити**. Top action: **➕ Додати вузол**.
- This screen absorbs the current per-tile action buttons and the add/edit/creds/scanner-info modals
  (modals stay as modals, re-themed).

### 3. Детекції (Detection Logs) — `#/logs`
- **Main — detection history table** from `GET /api/detections` (the current 📜 journal, promoted to
  a full screen): time, scanner, band, freq, class, SNR, event (`з'явився`/`зник`). Refresh button.
- **Right — «Live Spectrum Analysis»:** the single retained spectrum graphic — a small PSD line +
  occupancy for the active/selected scanner+band, with **click-to-tune** (maps click x → nearest
  RX5808 channel → `publishCommand`). Below it, a **recovered frames** thumbnail panel (latest
  frames from the scan store's `video`), click → image modal.

## Removed / kept
- **Removed:** the scrolling waterfall canvases (`spectrum.js` waterfall section) and the current
  full-width `#spectrum-panel` layout.
- **Kept (relocated):** occupancy bars, detection list/table, recovered frames, RX5808 mode/channel/
  click-to-tune, all modals, all SSE/MQTT/WHEP logic — unchanged in behavior.

## Theme & design tokens ("Tactical Spectrum Command")
New `styles.css` driven by CSS variables:
- Surfaces: base `#121212`, panel `#1A1A1A` + `1px` border `#2E2E2E`, overlay `#242424`.
- Semantic: primary/active/safe `#00FF41`, warning/detection `#FF8C00`, threat/error `#FF3131`,
  aux/secondary telemetry `#00E5FF`.
- Text: `on-surface` light `#E5E2E1`, muted/variant greys.
- **Shapes: sharp 0px corners** everywhere; no shadows, no gradients/blurs (tonal + 1px outlines).
- Components: pill badges (blinking-green active, steady-orange tracked, pulsing-red threat),
  L-shaped **corner-marks** on high-priority cards, `label-caps` (uppercase mono) section labels,
  data tables with 1px horizontal dividers and monospace-aligned columns.

## Fonts
Self-hosted **Geist** (UI / sans) + **JetBrains Mono** (data) as `woff2` under
`dashboard/public/vendor/fonts/`, declared via `@font-face`. Fallbacks:
`Geist, system-ui, sans-serif` and `"JetBrains Mono", ui-monospace, monospace`. All dynamic data
(frequencies, SNR, coordinates, ids, logs) uses the mono face; framework/labels use Geist.

## Files
| File | Change |
|---|---|
| `dashboard/public/index.html` | New shell: sidebar + top bar + three `<section>` screens + modals. |
| `dashboard/public/styles.css` | Rewritten to the tactical theme (tokens, components, screens). |
| `dashboard/public/app.js` | Split into a shell/bootstrap; wires router + views + shared modals/api. |
| `dashboard/public/router.js` (new) | Hash router; show/hide screens; active nav state. |
| `dashboard/public/views/dashboard.js` (new) | Camera grid + active-detections panel + node strip. |
| `dashboard/public/views/nodes.js` (new) | Node management cards + RX5808 controls + device CRUD. |
| `dashboard/public/views/logs.js` (new) | Detection history table + live spectrum + frames panel. |
| `dashboard/public/spectrum.js` | Drop waterfall; keep small PSD line + occupancy render helpers. |
| `dashboard/public/login.html` | Re-themed to match. |
| `dashboard/public/vendor/fonts/*` (new) | Self-hosted Geist + JetBrains Mono `woff2`. |
| unchanged | `whep.js`, `alert.js`, `mqtt-scan.js`, `rx5808-channels.js`. |

The exact JS module split is a plan-time detail; the constraint is that each view is independently
readable and the shared data stores (SSE devices, `scanClient`) are created once and passed in.

## Testing / acceptance
Front-end visual + functional verification against the live dashboard over WireGuard using Chrome
automation:
- Each screen renders in the tactical theme (colors, mono data, sharp corners, corner-marks).
- Camera WHEP tiles play; fullscreen modal works; tile-size slider persists.
- Active-detections panel updates live; new detection highlights + sound alert fires when armed.
- Node cards show online/uptime/TEMP slot; RX5808 mode/channel/click-to-tune still command the Pi.
- Detection Logs table loads from `/api/detections`; live spectrum draws (no waterfall); recovered
  frames open in the modal.
- Add/edit/delete/creds device flows work from the Вузли screen.
- `login.html` matches the theme and still authenticates.

## Out of scope
- Wiring real per-node temperatures (only the UI slot is built now).
- Global Map / geo (no data source) and a full Settings screen.
- Any server-side (`dashboard/server.js`) or Pi-agent changes.
