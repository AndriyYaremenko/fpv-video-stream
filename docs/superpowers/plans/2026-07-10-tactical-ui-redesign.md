# Tactical UI Redesign (AERO-SHIELD theme) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-skin and restructure the dashboard front-end (`dashboard/public/`) into the Stitch "Multi-Node SDR Drone Monitor" / AERO-SHIELD tactical look: a fixed left sidebar + three screens (Панель / Вузли / Детекції), waterfalls removed, small live spectrum + occupancy telemetry kept, and node TEMP slots ready for future temperatures.

**Architecture:** Zero-build static site. A hash router shows/hides three `<section>` screens inside one shell. `app.js` becomes a thin bootstrap that builds the shared data stores (SSE devices, `MqttScanClient`, `SoundAlerter`) once and passes a `ctx` object to pure view modules (`views/dashboard.js`, `views/nodes.js`, `views/logs.js`). A dev-only preview seam (`index.html?preview=1` + `fixtures.js`) renders the real page with sample data so every task is verifiable in Chrome with no backend. All server APIs, MQTT/SSE/WHEP plumbing, and device semantics are unchanged.

**Tech Stack:** Vanilla ES modules, plain CSS with custom properties, self-hosted `woff2` fonts (Geist + JetBrains Mono), Express (already a dep) for the dev static server, Chrome automation for visual verification.

## Global Constraints

- **Zero-build static only** — no bundler, no Tailwind, no framework. Files under `dashboard/public/` are served verbatim by `express.static`.
- **Front-end only** — do NOT touch `dashboard/server.js`, `lib/*`, Pi agents, MQTT topics, or any `/api/*` contract.
- **Preserve all existing behavior** — WHEP playback, SSE device stream, MQTT scan store, sound alerts, tile-size persistence, device CRUD/creds, RX5808 mode/channel/click-to-tune, detection journal data.
- **Shapes: sharp 0px corners everywhere; no shadows, no gradients, no blur.** Depth = tonal layers + 1px outlines only.
- **Design tokens (Tactical Spectrum Command):** base `#121212`, panel `#1A1A1A`, panel border `#2E2E2E`, overlay `#242424`; primary/active/safe `#00FF41`, warning/detection `#FF8C00`, threat/error `#FF3131`, aux `#00E5FF`; text `#E5E2E1`, muted `#9AA096`.
- **Fonts:** Geist (UI/sans), JetBrains Mono (all dynamic data: freq, SNR, ids, logs). Self-hosted; fallbacks `Geist, system-ui, sans-serif` and `"JetBrains Mono", ui-monospace, monospace`.
- **UI language: Ukrainian** for all visible strings; keep code identifiers, ids, MQTT tokens, and API field names as-is.
- **Node temperature = UI slot only** (`—°C` placeholder). Do not wire real temperatures.
- **`spectrum.js` pure helpers are unit-tested** (`test/spectrum.test.js`): keep exporting `splitByKind, classColor, fmtFreq, fmtPct, psdToPoints, detectionX, psdColor, frameCaption, rxtuneCaption` unchanged. `npm test` must stay green.
- Branch: `feat/tactical-ui-redesign` (already created). Commit after every task.

---

## Data shapes (reference — do not re-derive)

**Device** (from `GET /api/devices` and SSE `/api/stream`):
```
{ id, name, location, kind: 'camera'|'scanner', online: bool,
  bitrateKbps: number|null, uptimeSec: number|null, readers: number,
  telemetry?: { rssi?: number, freq?: string, alarm?: bool } }
```

**Scan store** (`scanClient.store[scannerId]`, from `mqtt-scan.js`):
```
{ online: bool, status_ts: number,
  bands: { [band]: { low_mhz, high_mhz } },
  latestPsd: { [band]: number[] },           // dBm
  waterfalls: { [band]: [{ psd: number[] }] },// still buffered, NOT rendered
  detection: { ts, occupancy: { [band]: frac }, detections: [
     { band, center_mhz, class:'analog'|'digital'|..., power_dbm, bandwidth_mhz,
       confidence, channel?, snr_db? } ] } | null,
  video: { frame_png_b64, standard, center_mhz, sync_snr_db, ts } | null,
  rxtune: { freq_mhz, channel, mode } | null }
```

**Detection journal row** (`GET /api/detections`):
```
{ ts, scanner_id, band, center_mhz, channel?, class, snr_db, event:'appeared'|'gone' }
```

**RX5808 command** (`scanClient.publishCommand(scannerId, cmd)`): `{ mode }` or `{ mode:'manual', channel }`.

---

## ctx contract (built in Task 3, consumed by Tasks 4–6)

`app.js` builds one `ctx` and passes it to every view's `render(container, ctx)`:
```
ctx = {
  cfg,                       // { webrtcBase, readUser, readPass }
  isPreview,                 // bool — true under ?preview=1
  devices:   () => Device[], // latest snapshot
  scanStore: () => object,   // scanClient.store
  scanners:  () => Device[], // devices where kind==='scanner'
  cameras:   () => Device[], // devices where kind!=='scanner'
  newDetKeys:() => Set<string>, // scan detection keys highlighted this tick
  getDetections: () => Promise<Row[]>, // journal; fixtures in preview, fetch in prod
  onScanClick: (scannerId, cmd) => void, // -> publishCommand (no-op in preview)
  handlers: {
    openVideo(device),        // fullscreen WHEP modal
    openImage(src, caption),  // fullscreen image modal
    startTile(device, videoEl),// begin WHEP into a <video> (no-op in preview)
    restartTile(id),
    openAddForm(), openEditForm(id), viewCreds(id), scannerInfo(id),
    deleteDevice(id, name),
  },
}
```
Views must be **pure w.r.t. `ctx`**: read data through the accessors, attach event listeners that call `ctx.handlers.*`, and never fetch or touch globals directly. `render` fully rebuilds its container's inner content (idempotent).

---

### Task 1: Design tokens, fonts, base + component CSS, dev tooling

**Files:**
- Rewrite: `dashboard/public/styles.css`
- Create: `dashboard/public/vendor/fonts/*.woff2` (Geist 400/500/600/700, JetBrains Mono 400/500/700)
- Create: `dashboard/dev-serve.mjs`
- Create: `dashboard/public/dev-preview.html` (static styleguide — plain HTML+CSS, no modules)

**Interfaces:**
- Produces: CSS custom properties on `:root` and the component class contract used by all later tasks: `.app` (grid shell), `.sidebar`, `.nav-item`(`.active`), `.topbar`, `.panel`, `.card`(`.corner` for corner-marks), `.pip`(`.on`/`.off`/`.warn`/`.threat`), `.btn`(`.btn-primary`/`.btn-ghost`/`.btn-alert`), `.data-table`, `.occ-bar`/`.occ-track`/`.occ-fill`, `.label-caps`, `.mono`, `.det-card`(`.is-new`), `.node-card`, `.tile`.

- [ ] **Step 1: Download self-hosted fonts**

Run (dev machine has internet; if a URL 404s, try the package's current version or skip that weight — fallbacks cover it):
```bash
cd dashboard/public/vendor && mkdir -p fonts && cd fonts
curl -fsSL -o geist-400.woff2 https://cdn.jsdelivr.net/npm/geist@1.3.1/dist/fonts/geist-sans/Geist-Regular.woff2
curl -fsSL -o geist-500.woff2 https://cdn.jsdelivr.net/npm/geist@1.3.1/dist/fonts/geist-sans/Geist-Medium.woff2
curl -fsSL -o geist-600.woff2 https://cdn.jsdelivr.net/npm/geist@1.3.1/dist/fonts/geist-sans/Geist-SemiBold.woff2
curl -fsSL -o geist-700.woff2 https://cdn.jsdelivr.net/npm/geist@1.3.1/dist/fonts/geist-sans/Geist-Bold.woff2
curl -fsSL -o jbmono-400.woff2 https://cdn.jsdelivr.net/fontsource/fonts/jetbrains-mono@latest/latin-400-normal.woff2
curl -fsSL -o jbmono-500.woff2 https://cdn.jsdelivr.net/fontsource/fonts/jetbrains-mono@latest/latin-500-normal.woff2
curl -fsSL -o jbmono-700.woff2 https://cdn.jsdelivr.net/fontsource/fonts/jetbrains-mono@latest/latin-700-normal.woff2
ls -la
```
Expected: 8 `.woff2` files, each > 5 KB.

- [ ] **Step 2: Write `styles.css` — tokens, font-face, reset, shell + components**

Replace the entire file with:
```css
/* dashboard/public/styles.css — Tactical Spectrum Command theme. Sharp 0px, tonal, 1px outlines. */
@font-face { font-family:'Geist'; font-weight:400; font-display:swap; src:url('/vendor/fonts/geist-400.woff2') format('woff2'); }
@font-face { font-family:'Geist'; font-weight:500; font-display:swap; src:url('/vendor/fonts/geist-500.woff2') format('woff2'); }
@font-face { font-family:'Geist'; font-weight:600; font-display:swap; src:url('/vendor/fonts/geist-600.woff2') format('woff2'); }
@font-face { font-family:'Geist'; font-weight:700; font-display:swap; src:url('/vendor/fonts/geist-700.woff2') format('woff2'); }
@font-face { font-family:'JetBrains Mono'; font-weight:400; font-display:swap; src:url('/vendor/fonts/jbmono-400.woff2') format('woff2'); }
@font-face { font-family:'JetBrains Mono'; font-weight:500; font-display:swap; src:url('/vendor/fonts/jbmono-500.woff2') format('woff2'); }
@font-face { font-family:'JetBrains Mono'; font-weight:700; font-display:swap; src:url('/vendor/fonts/jbmono-700.woff2') format('woff2'); }

:root{
  --bg:#121212; --panel:#1a1a1a; --panel-2:#161616; --line:#2e2e2e; --overlay:#242424;
  --on:#00ff41; --warn:#ff8c00; --threat:#ff3131; --aux:#00e5ff;
  --text:#e5e2e1; --muted:#9aa096; --off:#6b7280;
  --sans:'Geist',system-ui,sans-serif; --mono:'JetBrains Mono',ui-monospace,monospace;
  --gutter:16px; --pad:12px;
}
*{box-sizing:border-box;}
html,body{height:100%;}
body{margin:0;background:var(--bg);color:var(--text);font-family:var(--sans);
  font-size:14px;line-height:1.4;-webkit-font-smoothing:antialiased;}
.mono{font-family:var(--mono);font-variant-numeric:tabular-nums;}
.label-caps{font-family:var(--mono);font-size:11px;font-weight:700;letter-spacing:.1em;
  text-transform:uppercase;color:var(--muted);}
.hidden{display:none !important;}
a{color:var(--aux);}

/* shell: fixed sidebar + fluid main */
.app{display:grid;grid-template-columns:230px 1fr;min-height:100vh;}
.sidebar{background:var(--panel-2);border-right:1px solid var(--line);display:flex;
  flex-direction:column;padding:14px 10px;gap:14px;position:sticky;top:0;height:100vh;}
.brand{font-weight:700;letter-spacing:.04em;font-size:15px;padding:2px 6px;}
.brand small{display:block;color:var(--muted);font-size:10px;letter-spacing:.15em;}
.status-pill{font-family:var(--mono);font-size:11px;letter-spacing:.06em;padding:6px 8px;
  border:1px solid var(--line);color:var(--on);}
.status-pill.warn{color:var(--warn);}
.nav{display:flex;flex-direction:column;gap:2px;}
.nav-item{display:flex;align-items:center;gap:10px;padding:9px 10px;color:var(--muted);
  cursor:pointer;border-left:2px solid transparent;font-size:13px;user-select:none;}
.nav-item:hover{color:var(--text);background:var(--panel);}
.nav-item.active{color:var(--on);border-left-color:var(--on);background:var(--panel);}
.sidebar .spacer{flex:1;}
.operator{border-top:1px solid var(--line);padding-top:10px;font-size:12px;color:var(--muted);
  display:flex;align-items:center;justify-content:space-between;gap:8px;}

/* top bar */
.topbar{display:flex;align-items:center;gap:12px;padding:12px 18px;border-bottom:1px solid var(--line);
  background:var(--panel-2);flex-wrap:wrap;}
.topbar h1{margin:0;font-size:16px;font-weight:600;letter-spacing:.01em;}
.topbar .global-status{font-family:var(--mono);font-size:12px;color:var(--muted);}
.topbar .grow{flex:1;}
.size-ctl{display:flex;align-items:center;gap:6px;color:var(--muted);font-size:12px;}
.size-ctl input[type=range]{width:110px;accent-color:var(--on);cursor:pointer;}

/* buttons — hard edges */
.btn{font:inherit;background:transparent;color:var(--text);border:1px solid var(--line);
  padding:7px 12px;cursor:pointer;border-radius:0;letter-spacing:.02em;}
.btn:hover{border-color:var(--muted);}
.btn-primary{background:var(--on);border-color:var(--on);color:#001b06;font-weight:600;}
.btn-primary:hover{filter:brightness(1.08);}
.btn-ghost{border-color:var(--on);color:var(--on);}
.btn-alert{background:var(--warn);border-color:var(--warn);color:#241200;font-weight:600;}

/* panels & cards + L-corner marks */
.panel{background:var(--panel);border:1px solid var(--line);padding:var(--pad);}
.card{position:relative;background:var(--panel);border:1px solid var(--line);padding:var(--pad);}
.card.corner::before,.card.corner::after,
.card.corner>.cm-tl,.card.corner>.cm-br{content:'';position:absolute;width:9px;height:9px;
  border-color:var(--on);pointer-events:none;}
.card.corner::before{top:-1px;left:-1px;border-top:2px solid;border-left:2px solid;}
.card.corner::after{top:-1px;right:-1px;border-top:2px solid;border-right:2px solid;border-color:var(--on);}
.card.corner>.cm-bl{content:'';position:absolute;bottom:-1px;left:-1px;width:9px;height:9px;
  border-bottom:2px solid var(--on);border-left:2px solid var(--on);}
.card.corner>.cm-br{bottom:-1px;right:-1px;border-bottom:2px solid var(--on);border-right:2px solid var(--on);}

/* status pips */
.pip{display:inline-flex;align-items:center;gap:6px;font-family:var(--mono);font-size:10px;
  font-weight:700;letter-spacing:.08em;padding:3px 7px;border:1px solid currentColor;}
.pip::before{content:'';width:7px;height:7px;background:currentColor;display:inline-block;}
.pip.on{color:var(--on);} .pip.on::before{animation:blink 1.6s steps(1) infinite;}
.pip.off{color:var(--off);}
.pip.warn{color:var(--warn);}
.pip.threat{color:var(--threat);} .pip.threat::before{animation:blink .7s steps(1) infinite;}
@keyframes blink{50%{opacity:.25;}}

/* data table */
.data-table{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:12px;}
.data-table th{text-align:left;color:var(--muted);font-weight:700;letter-spacing:.06em;
  text-transform:uppercase;font-size:10px;padding:6px 8px;border-bottom:1px solid var(--line);}
.data-table td{padding:7px 8px;border-bottom:1px solid var(--line);white-space:nowrap;}
.data-table tr.is-new td{background:rgba(255,140,0,.08);}

/* occupancy bars */
.occ-bar{display:grid;grid-template-columns:52px 1fr 42px;align-items:center;gap:8px;
  font-family:var(--mono);font-size:11px;margin:4px 0;}
.occ-label{color:var(--muted);letter-spacing:.05em;}
.occ-track{height:8px;background:var(--panel-2);border:1px solid var(--line);}
.occ-fill{display:block;height:100%;background:var(--on);}
.occ-val{color:var(--text);text-align:right;}

/* detection (threat-log) card */
.det-card{border:1px solid var(--line);border-left:3px solid var(--muted);padding:8px 10px;
  margin-bottom:8px;background:var(--panel-2);}
.det-card.analog{border-left-color:var(--on);}
.det-card.digital{border-left-color:var(--warn);}
.det-card.is-new{outline:1px solid var(--warn);}
.det-card .dc-top{display:flex;justify-content:space-between;align-items:baseline;gap:8px;}
.det-card .dc-freq{font-family:var(--mono);font-weight:700;font-size:15px;}
.det-card .dc-meta{font-family:var(--mono);font-size:11px;color:var(--muted);margin-top:3px;}

/* node card (management + bottom strip) */
.node-card{position:relative;background:var(--panel);border:1px solid var(--line);padding:12px;}
.node-card .nc-head{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:8px;}
.node-card .nc-title{font-weight:600;}
.node-card .nc-sub{color:var(--muted);font-size:11px;font-family:var(--mono);}
.node-card .nc-grid{display:grid;grid-template-columns:1fr 1fr;gap:6px 14px;font-family:var(--mono);font-size:12px;}
.node-card .nc-grid .k{color:var(--muted);font-size:10px;letter-spacing:.06em;text-transform:uppercase;display:block;}
.node-card .nc-actions{display:flex;gap:6px;margin-top:10px;flex-wrap:wrap;}
.node-card .nc-actions .btn{padding:5px 9px;font-size:12px;}
.temp-hot{color:var(--threat);} .temp-warm{color:var(--warn);}

/* camera tiles (dashboard) */
.grid{display:grid;gap:var(--gutter);grid-template-columns:repeat(auto-fill,minmax(var(--tile-min,320px),1fr));}
.tile{position:relative;aspect-ratio:16/9;background:#000;border:1px solid var(--line);overflow:hidden;cursor:pointer;}
.tile.offline{opacity:.55;}
.tile video{width:100%;height:100%;object-fit:cover;background:#000;}
.tile .tile-overlay{position:absolute;inset:0;display:flex;flex-direction:column;justify-content:space-between;
  padding:8px;pointer-events:none;background:linear-gradient(to top,rgba(0,0,0,.6),transparent 45%);}
.tile .tile-top{display:flex;justify-content:space-between;align-items:flex-start;}
.tile .tile-actions{display:flex;gap:4px;opacity:0;transition:opacity .15s;pointer-events:auto;}
.tile:hover .tile-actions{opacity:1;}
.tile .tile-btn{background:rgba(0,0,0,.6);border:1px solid var(--line);color:#fff;cursor:pointer;
  font-size:13px;line-height:1;padding:4px 6px;}
.tile .tile-meta strong{display:block;font-size:13px;}
.tile .tile-meta small{color:#cbd5e1;font-size:11px;}
.tile .tile-stats{font-family:var(--mono);font-size:11px;color:#cbd5e1;}

/* dashboard layout: feeds | detections ; node strip below */
.dash{display:grid;grid-template-columns:1fr 320px;gap:var(--gutter);padding:var(--gutter);}
.dash .feeds{min-width:0;}
.dash .threats{min-width:0;}
.dash .node-strip{grid-column:1 / -1;display:grid;gap:var(--gutter);
  grid-template-columns:repeat(auto-fill,minmax(220px,1fr));}
.screen-pad{padding:var(--gutter);}
.section-title{margin:0 0 10px;}

/* logs layout: history | side (spectrum + frames) */
.logs{display:grid;grid-template-columns:1fr 300px;gap:var(--gutter);padding:var(--gutter);}
.mini-spectrum{width:100%;height:60px;display:block;background:var(--panel-2);border:1px solid var(--line);}
.frames-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(84px,1fr));gap:6px;}
.frames-grid img{width:100%;border:1px solid var(--line);cursor:pointer;display:block;}

/* RX5808 controls */
.rx5808-ctl{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0;}
.rx-mode{font-family:var(--mono);font-size:11px;padding:4px 8px;border:1px solid var(--line);
  background:transparent;color:var(--muted);cursor:pointer;}
.rx-mode.active{color:var(--on);border-color:var(--on);}
.rx5808-ch{font-family:var(--mono);font-size:11px;background:var(--panel-2);color:var(--text);
  border:1px solid var(--line);padding:4px 6px;}

/* modals — fullscreen viewer + form card */
.modal{position:fixed;inset:0;background:rgba(0,0,0,.92);display:flex;align-items:center;
  justify-content:center;flex-direction:column;z-index:50;}
.modal.hidden{display:none;}
.modal video,.modal .modal-image{max-width:92vw;max-height:82vh;background:#000;}
.modal-caption{margin-top:10px;font-family:var(--mono);font-size:12px;}
.modal-close{position:absolute;top:14px;right:16px;font-size:20px;background:transparent;color:#fff;border:none;cursor:pointer;}
.modal2{position:fixed;inset:0;background:rgba(0,0,0,.7);display:flex;align-items:center;
  justify-content:center;z-index:60;padding:16px;}
.modal2.hidden{display:none;}
.modal2 .card2{position:relative;background:var(--overlay);border:2px solid var(--line);padding:18px;
  width:min(640px,96vw);max-height:90vh;overflow:auto;}
.card2 .card-close{position:absolute;top:8px;right:10px;background:transparent;border:none;color:var(--text);font-size:18px;cursor:pointer;}
.card2 h2{margin:.1rem 0 .7rem;font-size:17px;}
.muted{color:var(--muted);} .muted.small{font-size:12px;}
.form{display:flex;flex-direction:column;gap:12px;}
.form label{display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--muted);}
.form input,.form select{padding:9px;border:1px solid var(--line);background:#0d0d0d;color:var(--text);
  font:inherit;border-radius:0;}
.form input:focus,.form select:focus{outline:none;border-color:var(--on);}
.form-err{color:var(--threat);font-size:12px;min-height:1em;margin:0;}
.form-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:4px;}
.cred-row{margin:10px 0;}
.cred-label{display:flex;justify-content:space-between;font-size:12px;color:var(--muted);margin-bottom:4px;}
.copy{background:transparent;border:1px solid var(--line);color:var(--text);padding:3px 9px;font-size:11px;cursor:pointer;}
.cred-row pre{margin:0;background:#0b0b0b;border:1px solid var(--line);padding:9px;font-size:12px;
  line-height:1.35;white-space:pre-wrap;word-break:break-all;color:#d6dee8;font-family:var(--mono);}

@media(max-width:900px){
  .app{grid-template-columns:1fr;}
  .sidebar{position:static;height:auto;flex-direction:row;flex-wrap:wrap;align-items:center;}
  .sidebar .spacer{display:none;}
  .dash,.logs{grid-template-columns:1fr;}
}
```

- [ ] **Step 3: Write `dashboard/dev-serve.mjs`**

```js
// dashboard/dev-serve.mjs — DEV ONLY. Serves dashboard/public/ with no auth so the fixture
// preview (index.html?preview=1) and the styleguide (dev-preview.html) render with no backend.
import express from 'express';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
const __dirname = dirname(fileURLToPath(import.meta.url));
const app = express();
app.use(express.static(join(__dirname, 'public')));
const port = Number(process.env.DEV_PORT || 8081);
app.listen(port, '127.0.0.1', () =>
  console.log(`Dev preview: http://127.0.0.1:${port}/dev-preview.html  and  http://127.0.0.1:${port}/index.html?preview=1`));
```

- [ ] **Step 4: Write `dashboard/public/dev-preview.html` (static styleguide)**

Plain HTML that links the CSS and shows one of every component so the theme is verifiable before any JS exists:
```html
<!doctype html><html lang="uk"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Styleguide — Tactical</title><link rel="stylesheet" href="/styles.css"></head>
<body style="padding:24px;display:flex;flex-direction:column;gap:18px;max-width:900px;margin:0 auto;">
  <h1 class="mono">STYLEGUIDE</h1>
  <div><span class="pip on">ONLINE</span> <span class="pip off">OFFLINE</span>
       <span class="pip warn">TRACK</span> <span class="pip threat">THREAT</span></div>
  <div style="display:flex;gap:8px;flex-wrap:wrap">
    <button class="btn">Ghost line</button><button class="btn btn-primary">Primary</button>
    <button class="btn btn-ghost">Green ghost</button><button class="btn btn-alert">Acknowledge</button></div>
  <div class="card corner"><span class="cm-bl"></span><span class="cm-br"></span>
    <div class="label-caps">Node telemetry</div>
    <div class="mono" style="font-size:20px">42.8°C · 5.8G</div></div>
  <div class="det-card analog is-new"><div class="dc-top">
    <span class="dc-freq mono">5800 МГц</span><span class="pip warn">NEW</span></div>
    <div class="dc-meta">5.8G · SNR 18 dB · bladerf · 12:03:20</div></div>
  <div class="occ-bar"><span class="occ-label">5.8G</span>
    <span class="occ-track"><span class="occ-fill" style="width:32%"></span></span><span class="occ-val">32%</span></div>
  <table class="data-table"><thead><tr><th>Час</th><th>Частота</th><th>Клас</th><th>SNR</th></tr></thead>
    <tbody><tr class="is-new"><td>12:03:20</td><td>5800 МГц</td><td style="color:var(--on)">analog</td><td>18 dB</td></tr>
    <tr><td>12:01:00</td><td>1280 МГц</td><td style="color:var(--warn)">digital</td><td>12 dB</td></tr></tbody></table>
</body></html>
```

- [ ] **Step 5: Run the dev server and verify the styleguide in Chrome**

Run: `node dashboard/dev-serve.mjs` (leave running).
In Chrome (claude-in-chrome), navigate to `http://127.0.0.1:8081/dev-preview.html`, take a screenshot.
Expected: charcoal `#121212` background, **Geist** headings + **JetBrains Mono** data, terminal-green pips/buttons, **sharp corners**, green L-corner marks on the card, orange/green detection accents, 1px-divider table. Confirm no missing-font fallback (JetBrains Mono is clearly monospaced). If fonts didn't download, the mono/sans fallbacks still apply — note it and continue.

- [ ] **Step 6: Commit**

```bash
git add dashboard/public/styles.css dashboard/public/vendor/fonts dashboard/dev-serve.mjs dashboard/public/dev-preview.html
git commit -m "feat(ui): tactical theme tokens, self-hosted fonts, dev preview server"
```

---

### Task 2: Shared UI components module

**Files:**
- Create: `dashboard/public/views/components.js`
- Modify: `dashboard/public/dev-preview.html` (add a module block that mounts components with sample data)

**Interfaces:**
- Consumes: `classColor`, `fmtFreq`, `fmtPct` from `spectrum.js` (unchanged exports).
- Produces (all pure, return a DOM `Element` unless noted):
  - `escapeHtml(s) -> string`
  - `fmtBitrate(kbps) -> string`, `fmtUptime(sec) -> string`, `telemetryLine(t) -> string`, `tempSlot(celsius|null) -> string` (`—°C` when null; adds `.temp-warm`/`.temp-hot` class markup ≥60/≥75)
  - `el(tag, cls?, html?) -> Element`
  - `pip(online) -> string` (HTML for `<span class="pip on|off">`)
  - `cornerCard(innerHtml) -> Element` (`.card.corner` with the two extra `.cm-bl/.cm-br` spans)
  - `occupancyStrip(bands, occupancy) -> Element` (`.occ-bar` per band; `bands` = `{[band]:{low_mhz,high_mhz}}`)
  - `detectionCard(det, isNew) -> Element` (`.det-card` colored by `det.class`)

- [ ] **Step 1: Write `views/components.js`**

```js
// dashboard/public/views/components.js — pure UI atoms shared by all screens.
import { classColor, fmtFreq, fmtPct } from '/spectrum.js';

export function escapeHtml(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
export function el(tag,cls,html){const e=document.createElement(tag);if(cls)e.className=cls;if(html!=null)e.innerHTML=html;return e;}
export function fmtBitrate(k){return k==null?'—':k>=1000?`${(k/1000).toFixed(1)} Mbps`:`${k} kbps`;}
export function fmtUptime(s){if(s==null)return '—';const h=Math.floor(s/3600),m=Math.floor((s%3600)/60);return h?`${h}год ${m}хв`:`${m}хв`;}
export function telemetryLine(t){const p=[];if(!t)return '';if(t.rssi!=null)p.push(`RSSI ${t.rssi}`);if(t.freq!=null)p.push(String(t.freq));if(t.alarm)p.push('⚠ ALARM');return p.join(' · ');}
export function tempSlot(c){if(c==null)return '<span class="mono">—°C</span>';const cls=c>=75?'temp-hot':c>=60?'temp-warm':'';return `<span class="mono ${cls}">${c.toFixed(1)}°C</span>`;}
export function pip(online){return `<span class="pip ${online?'on':'off'}">${online?'ONLINE':'OFFLINE'}</span>`;}
export function cornerCard(innerHtml){const c=el('div','card corner',innerHtml);c.insertAdjacentHTML('beforeend','<span class="cm-bl"></span><span class="cm-br"></span>');return c;}
export function occupancyStrip(bands,occupancy){const wrap=el('div','occ');for(const band of Object.keys(bands||{})){const frac=(occupancy&&occupancy[band])||0;wrap.appendChild(el('div','occ-bar',`<span class="occ-label">${escapeHtml(band)}</span><span class="occ-track"><span class="occ-fill" style="width:${Math.round(frac*100)}%"></span></span><span class="occ-val">${fmtPct(frac)}</span>`));}return wrap;}
export function detectionCard(det,isNew){const cls=det.class==='analog'?'analog':det.class==='digital'?'digital':'';const c=el('div',`det-card ${cls}${isNew?' is-new':''}`);const chan=det.channel?` (${escapeHtml(det.channel)})`:'';const snr=det.snr_db!=null?` · SNR ${det.snr_db} dB`:'';c.innerHTML=`<div class="dc-top"><span class="dc-freq mono">${fmtFreq(det.center_mhz)}</span>${isNew?'<span class="pip warn">NEW</span>':''}</div><div class="dc-meta">${escapeHtml(det.band||'')}${chan}${snr} · <span style="color:${classColor(det.class)}">${escapeHtml(det.class||'')}</span></div>`;return c;}
```

- [ ] **Step 2: Add a component mount block to `dev-preview.html`**

Append before `</body>`:
```html
<h2 class="mono">COMPONENTS (module)</h2>
<div id="cmp-mount" style="display:flex;flex-direction:column;gap:10px"></div>
<script type="module">
  import { cornerCard, detectionCard, occupancyStrip, tempSlot, pip } from '/views/components.js';
  const m=document.getElementById('cmp-mount');
  m.appendChild(cornerCard(`<div class="label-caps">NODE-BLADERF</div>${pip(true)} ${tempSlot(63.5)}`));
  m.appendChild(detectionCard({band:'5.8G',center_mhz:5800,class:'analog',channel:'F4',snr_db:18},true));
  m.appendChild(occupancyStrip({'5.8G':{},'900M':{}},{'5.8G':0.32,'900M':0.5}));
</script>
```

- [ ] **Step 3: Verify in Chrome**

With `node dashboard/dev-serve.mjs` running, reload `http://127.0.0.1:8081/dev-preview.html`, screenshot.
Expected: the module-rendered corner card (green marks, ONLINE pip, `63.5°C` in amber `temp-warm`), the NEW analog detection card (green left border + orange NEW outline), and two occupancy bars. No console errors (check console messages).

- [ ] **Step 4: Commit**

```bash
git add dashboard/public/views/components.js dashboard/public/dev-preview.html
git commit -m "feat(ui): shared tactical UI component atoms"
```

---

### Task 3: App shell, router, modals, bootstrap + fixture preview seam

**Files:**
- Rewrite: `dashboard/public/index.html`
- Create: `dashboard/public/router.js`
- Create: `dashboard/public/modals.js`
- Create: `dashboard/public/fixtures.js` (dev-only sample data)
- Rewrite: `dashboard/public/app.js`
- Create (stubs, filled in Tasks 4–6): `dashboard/public/views/dashboard.js`, `views/nodes.js`, `views/logs.js`

**Interfaces:**
- `router.js` — `createRouter({routes, onChange}) -> { start(), go(hash), current() }`. `routes` = `[{hash,label,icon,mount(container,ctx)}]`. Renders nav into `.nav`, toggles sections, calls the active route's `mount`. Exposes `renderActive()` via `onChange`/re-mount.
- `modals.js` — `createModals(ctx) -> { openVideo, openImage, openAddForm, openEditForm, viewCreds, scannerInfo, deleteDevice }` (the current app.js modal + device-CRUD logic, re-themed to `.card2`). Reads `ctx.cfg`, calls `fetch('/api/...')` exactly as today; in preview these are simply not triggered destructively (forms open but the operator won't submit).
- `app.js` — builds `ctx` (see contract above), instantiates `MqttScanClient`, `SoundAlerter`, the router, and `modals`; wires topbar actions; drives re-render on SSE + MQTT ticks (prod) or once from fixtures (preview).
- View stubs export `render(container, ctx)` that (for now) writes a titled placeholder.

- [ ] **Step 1: Write `index.html` shell**

```html
<!doctype html>
<html lang="uk">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>FPV — Тактичний монітор</title>
  <link rel="preload" href="/vendor/fonts/jbmono-400.woff2" as="font" type="font/woff2" crossorigin>
  <link rel="stylesheet" href="/styles.css" />
</head>
<body>
  <div class="app">
    <aside class="sidebar">
      <div class="brand">FPV<small>SDR MONITOR</small></div>
      <div id="status-pill" class="status-pill">—</div>
      <nav class="nav" id="nav"></nav>
      <div class="spacer"></div>
      <div class="operator"><span id="operator-name" class="mono">operator</span>
        <button id="logout" class="btn">Вийти</button></div>
    </aside>
    <div class="main">
      <header class="topbar">
        <h1 id="screen-title">Панель</h1>
        <span id="global-status" class="global-status"></span>
        <span class="grow"></span>
        <label class="size-ctl" id="size-ctl" title="Розмір вікон">▭
          <input type="range" id="tile-size" min="240" max="720" step="20" /></label>
        <button id="sound-toggle" class="btn" title="Звук сповіщень">🔕</button>
        <button id="add-device" class="btn btn-primary">➕ Додати вузол</button>
      </header>
      <main id="screens">
        <section id="screen-dashboard" class="screen"></section>
        <section id="screen-nodes" class="screen hidden"></section>
        <section id="screen-logs" class="screen hidden"></section>
      </main>
    </div>
  </div>

  <div id="modal" class="modal hidden">
    <button id="modal-close" class="modal-close">✕</button>
    <video id="modal-video" autoplay playsinline muted></video>
    <img id="modal-image" class="modal-image hidden" alt="recovered frame" />
    <div id="modal-caption" class="modal-caption"></div>
  </div>
  <div id="form-modal" class="modal2 hidden">
    <div class="card2"><button class="card-close" data-close>✕</button>
      <div id="form-modal-body"></div></div>
  </div>

  <script src="/vendor/mqtt.min.js"></script>
  <script type="module" src="/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Write `router.js`**

```js
// dashboard/public/router.js — hash router: builds sidebar nav, toggles screen sections.
export function createRouter({ routes, ctx }) {
  const nav = document.getElementById('nav');
  const title = document.getElementById('screen-title');
  const sizeCtl = document.getElementById('size-ctl');
  nav.innerHTML = '';
  for (const r of routes) {
    const item = document.createElement('div');
    item.className = 'nav-item'; item.dataset.hash = r.hash;
    item.innerHTML = `<span>${r.icon}</span><span>${r.label}</span>`;
    item.addEventListener('click', () => { location.hash = r.hash; });
    nav.appendChild(item);
  }
  function currentRoute() {
    return routes.find(r => r.hash === location.hash) || routes[0];
  }
  function renderActive() {
    const r = currentRoute();
    for (const s of document.querySelectorAll('.screen')) s.classList.add('hidden');
    document.getElementById(r.section).classList.remove('hidden');
    for (const it of nav.children) it.classList.toggle('active', it.dataset.hash === r.hash);
    title.textContent = r.label;
    sizeCtl.classList.toggle('hidden', r.hash !== '#/dashboard'); // slider only on dashboard
    r.mount(document.getElementById(r.section), ctx);
  }
  window.addEventListener('hashchange', renderActive);
  return { start() { if (!location.hash) location.hash = routes[0].hash; renderActive(); }, renderActive, currentRoute };
}
```

- [ ] **Step 3: Write `modals.js`**

Move the modal + device-CRUD helpers from the old `app.js` verbatim in behavior, re-themed to `.card2`. Full content:
```js
// dashboard/public/modals.js — fullscreen viewer + form/creds modals + device CRUD.
import { startWhep } from '/whep.js';
import { escapeHtml } from '/views/components.js';

export function createModals(ctx) {
  const modal = document.getElementById('modal');
  const mVideo = document.getElementById('modal-video');
  const mImage = document.getElementById('modal-image');
  const mCap = document.getElementById('modal-caption');
  const formModal = document.getElementById('form-modal');
  const formBody = document.getElementById('form-modal-body');
  let modalPlayer = null;

  function showForm(html) { formBody.innerHTML = html; formModal.classList.remove('hidden'); }
  function hideForm() { formModal.classList.add('hidden'); formBody.innerHTML = ''; }
  formModal.addEventListener('click', (e) => {
    if (e.target === formModal || e.target.hasAttribute('data-close')) hideForm();
    const copyBtn = e.target.closest('.copy');
    if (copyBtn) { const pre = copyBtn.closest('.cred-row').querySelector('pre'); copyText(pre.textContent, copyBtn); }
  });
  async function copyText(text, btn) {
    try { await navigator.clipboard.writeText(text); }
    catch { const ta=document.createElement('textarea'); ta.value=text; ta.style.position='fixed'; ta.style.opacity='0';
      document.body.appendChild(ta); ta.select(); try{document.execCommand('copy');}catch{} document.body.removeChild(ta); }
    if (btn){const t=btn.textContent;btn.textContent='скопійовано ✓';setTimeout(()=>btn.textContent=t,1200);}
  }

  function openVideo(d) {
    mImage.classList.add('hidden'); mVideo.classList.remove('hidden');
    if (modalPlayer){modalPlayer.close();modalPlayer=null;}
    mCap.textContent = `${d.name} — ${d.location||''}`;
    modal.classList.remove('hidden');
    if (d.online && !ctx.isPreview) startWhep(mVideo, `${ctx.cfg.webrtcBase}/${d.id}/whep`, ctx.cfg.readUser, ctx.cfg.readPass).then(p=>modalPlayer=p).catch(()=>{});
    document.getElementById('modal-close').onclick = () => { if(modalPlayer){modalPlayer.close();modalPlayer=null;} modal.classList.add('hidden'); };
  }
  function openImage(src, caption) {
    if (modalPlayer){modalPlayer.close();modalPlayer=null;}
    mVideo.classList.add('hidden'); mImage.src=src; mImage.classList.remove('hidden');
    mCap.textContent = caption||''; modal.classList.remove('hidden');
    document.getElementById('modal-close').onclick = () => { mImage.classList.add('hidden'); mImage.src=''; mVideo.classList.remove('hidden'); modal.classList.add('hidden'); };
  }

  function credRow(label,value){return `<div class="cred-row"><div class="cred-label"><span>${label}</span><button type="button" class="copy">копіювати</button></div><pre>${escapeHtml(value)}</pre></div>`;}
  function showCreds(device,push,isNew){showForm(`<h2>${isNew?'✅ Вузол створено':'🔑 Креди вузла'}: ${escapeHtml(device.id)}</h2><p class="muted">${escapeHtml(device.name||'')}${device.location?` · ${escapeHtml(device.location)}`:''}</p>${credRow('Publish пароль',device.publish_pass)}${credRow('Команда пушу — RTSP',push.rtsp)}${credRow('Команда пушу — SRT',push.srt)}<p class="muted small">Налаштуй WireGuard на Pi вручну, потім встав цю команду пушу.</p><div class="form-actions"><button type="button" data-close class="btn btn-primary">Готово</button></div>`);}
  function scannerInfoModal(device,isNew){showForm(`<h2>${isNew?'✅ Сканер створено':'📡 Сканер'}: ${escapeHtml(device.id)}</h2><p class="muted">${escapeHtml(device.name||'')}${device.location?` · ${escapeHtml(device.location)}`:''}</p><p class="muted small">Вузол-сканер — відео не публікує. Дані йдуть у MQTT.</p>${credRow('SCAN_ID на Pi',device.id)}${credRow('MQTT-топіки',`fpv/${device.id}/{spectrum,detection,status,video}`)}<div class="form-actions"><button type="button" data-close class="btn btn-primary">Готово</button></div>`);}

  function openAddForm(){showForm(`<h2>Додати вузол</h2><form id="add-form" class="form"><label>Device ID <small>(порожньо = автоген)</small><input name="id" autocomplete="off"/></label><label>Тип<select name="kind"><option value="camera">Камера</option><option value="scanner">Сканер (HackRF)</option></select></label><label>Назва<input name="name" required/></label><label>Локація<input name="location"/></label><p class="form-err" id="add-err"></p><div class="form-actions"><button type="button" data-close class="btn btn-ghost">Скасувати</button><button type="submit" class="btn btn-primary">Створити</button></div></form>`);
    document.getElementById('add-form').addEventListener('submit', submitAdd);}
  async function submitAdd(e){e.preventDefault();const fd=new FormData(e.target);const payload={id:(fd.get('id')||'').trim(),name:(fd.get('name')||'').trim(),location:(fd.get('location')||'').trim(),kind:fd.get('kind')||'camera'};const errEl=document.getElementById('add-err');errEl.textContent='';const res=await fetch('/api/devices',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const body=await res.json().catch(()=>({}));if(!res.ok){errEl.textContent=body.error||`Помилка ${res.status}`;return;}if(body.scanner)scannerInfoModal(body.device,true);else showCreds(body.device,body.push,true);}
  function openEditForm(id){const d=ctx.devices().find(x=>x.id===id)||{id,name:'',location:''};showForm(`<h2>Редагувати: ${escapeHtml(id)}</h2><form id="edit-form" class="form"><label>Назва<input name="name" value="${escapeHtml(d.name||'')}" required/></label><label>Локація<input name="location" value="${escapeHtml(d.location||'')}"/></label><p class="muted small">ID та пароль не змінюються.</p><p class="form-err" id="edit-err"></p><div class="form-actions"><button type="button" data-close class="btn btn-ghost">Скасувати</button><button type="submit" class="btn btn-primary">Зберегти</button></div></form>`);
    document.getElementById('edit-form').addEventListener('submit',(e)=>submitEdit(e,id));}
  async function submitEdit(e,id){e.preventDefault();const fd=new FormData(e.target);const payload={name:(fd.get('name')||'').trim(),location:(fd.get('location')||'').trim()};const errEl=document.getElementById('edit-err');errEl.textContent='';const res=await fetch(`/api/devices/${encodeURIComponent(id)}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});if(!res.ok){const b=await res.json().catch(()=>({}));errEl.textContent=b.error||`Помилка ${res.status}`;return;}hideForm();ctx.requestRender();}
  async function viewCreds(id){const res=await fetch(`/api/devices/${encodeURIComponent(id)}/push`);if(!res.ok){alert('Не вдалося отримати креди');return;}const body=await res.json();showCreds(body.device,body.push,false);}
  function scannerInfo(id){const d=ctx.devices().find(x=>x.id===id)||{id,name:id,location:''};scannerInfoModal(d,false);}
  async function deleteDevice(id,name){if(!confirm(`Видалити вузол «${name||id}»?`))return;const res=await fetch(`/api/devices/${encodeURIComponent(id)}`,{method:'DELETE'});if(!res.ok){alert('Помилка видалення');return;}ctx.requestRender();}

  return { openVideo, openImage, openAddForm, openEditForm, viewCreds, scannerInfo, deleteDevice, hideForm };
}
```

- [ ] **Step 4: Write `fixtures.js` (dev-only sample data)**

```js
// dashboard/public/fixtures.js — DEV ONLY sample data for ?preview=1. Harmless in prod (never imported unless preview).
const psd = (n, base) => Array.from({length:n}, (_,i) => base + 8*Math.sin(i/4) - (i%7===0?18:0) + (i%13)*0.7);
const PNG = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mНplaceholder';
export const FIXTURES = {
  config: { webrtcBase:'', readUser:'read', readPass:'x' },
  operator: 'operator_042',
  devices: [
    { id:'cam-north', name:'Вхідні ворота', location:'Периметр — Північ', kind:'camera', online:true, bitrateKbps:2100, uptimeSec:5400, readers:2, telemetry:{rssi:-62,freq:'5800',alarm:false} },
    { id:'cam-yard', name:'Двір', location:'Периметр — Схід', kind:'camera', online:false },
    { id:'bladerf', name:'Сканер bladeRF', location:'Дах', kind:'scanner', online:true, uptimeSec:9000 },
  ],
  detections: [
    { ts:1751900000, scanner_id:'bladerf', band:'5.8G', center_mhz:5800, channel:'F4', class:'analog', snr_db:18, event:'appeared' },
    { ts:1751899000, scanner_id:'bladerf', band:'1.2G', center_mhz:1280, class:'digital', snr_db:12, event:'gone' },
    { ts:1751898000, scanner_id:'bladerf', band:'900M', center_mhz:915, class:'digital', snr_db:9, event:'appeared' },
  ],
  scanStore: {
    bladerf: {
      online:true, status_ts:1751900000,
      bands:{ '5.8G':{low_mhz:5645,high_mhz:5945}, '1.2G':{low_mhz:1080,high_mhz:1360}, '900M':{low_mhz:840,high_mhz:960} },
      latestPsd:{ '5.8G':psd(64,-70), '1.2G':psd(64,-80), '900M':psd(64,-75) },
      waterfalls:{ '5.8G':[], '1.2G':[], '900M':[] },
      detection:{ ts:1751900000, occupancy:{'5.8G':0.32,'1.2G':0.08,'900M':0.5},
        detections:[ {band:'5.8G',center_mhz:5800,class:'analog',power_dbm:-42,bandwidth_mhz:18,confidence:0.9,channel:'F4',snr_db:18},
          {band:'900M',center_mhz:915,class:'digital',power_dbm:-55,bandwidth_mhz:10,confidence:0.7} ] },
      video:{ frame_png_b64:'', standard:'PAL', center_mhz:5800, sync_snr_db:18.3, ts:1751900000 },
      rxtune:{ freq_mhz:5865, channel:'A1', mode:'scan' },
    },
  },
};
```
(The `frame_png_b64` is left empty; the frames panel shows an empty state in preview. `PNG` const is unused scaffolding — omit it if lint complains.)

- [ ] **Step 5: Write `app.js` bootstrap with the preview seam and view stubs**

```js
// dashboard/public/app.js — shell bootstrap: builds ctx + data stores, wires router/modals/topbar.
import { SoundAlerter, diffNewKeys } from '/alert.js';
import { MqttScanClient } from '/mqtt-scan.js';
import { startWhep } from '/whep.js';
import { createRouter } from '/router.js';
import { createModals } from '/modals.js';
import { render as renderDashboard } from '/views/dashboard.js';
import { render as renderNodes } from '/views/nodes.js';
import { render as renderLogs } from '/views/logs.js';

const PREVIEW = new URLSearchParams(location.search).has('preview');
const alerter = new SoundAlerter();
const scanClient = new MqttScanClient();
let cfg = null, devices = [], newDetKeys = new Set(), prevScanKeys = null, fx = null;

const ctx = {
  get cfg(){return cfg;}, isPreview: PREVIEW,
  devices: () => devices,
  scanStore: () => scanClient.store,
  scanners: () => devices.filter(d => d.kind === 'scanner'),
  cameras:  () => devices.filter(d => d.kind !== 'scanner'),
  newDetKeys: () => newDetKeys,
  getDetections: async () => {
    if (PREVIEW) return fx.detections;
    try { const r = await fetch('/api/detections?limit=200'); return r.ok ? r.json() : []; } catch { return []; }
  },
  onScanClick: (id, cmd) => { if (!PREVIEW) scanClient.publishCommand(id, cmd); },
  requestRender: () => router.renderActive(),
  handlers: {},
};

const routes = [
  { hash:'#/dashboard', label:'Панель',   icon:'▤', section:'screen-dashboard', mount:renderDashboard },
  { hash:'#/nodes',     label:'Вузли',     icon:'▦', section:'screen-nodes',     mount:renderNodes },
  { hash:'#/logs',      label:'Детекції',  icon:'≣', section:'screen-logs',      mount:renderLogs },
];
const router = createRouter({ routes, ctx });
const modals = createModals(ctx);
ctx.handlers = {
  openVideo: modals.openVideo, openImage: modals.openImage,
  openAddForm: modals.openAddForm, openEditForm: modals.openEditForm,
  viewCreds: modals.viewCreds, scannerInfo: modals.scannerInfo, deleteDevice: modals.deleteDevice,
  startTile: (d, videoEl) => { if (!PREVIEW && d.online) startWhep(videoEl, `${cfg.webrtcBase}/${d.id}/whep`, cfg.readUser, cfg.readPass).catch(()=>{}); },
  restartTile: (id) => { const d = devices.find(x=>x.id===id); if (d) router.renderActive(); },
};

// topbar + sidebar wiring
const soundBtn = document.getElementById('sound-toggle');
function setSoundUI(){ soundBtn.textContent = alerter.armed ? '🔔' : '🔕'; soundBtn.classList.toggle('btn-ghost', alerter.armed); }
soundBtn.addEventListener('click', () => { alerter.armed ? alerter.disarm() : alerter.arm(); localStorage.setItem('soundArmed', alerter.armed?'1':'0'); setSoundUI(); });
if (localStorage.getItem('soundArmed')==='1'){ document.addEventListener('pointerdown', ()=>{alerter.arm();setSoundUI();}, {once:true}); soundBtn.textContent='🔔'; } else setSoundUI();
document.getElementById('add-device').addEventListener('click', () => modals.openAddForm());
document.getElementById('logout').addEventListener('click', async () => { if(!PREVIEW) await fetch('/logout',{method:'POST'}); location.href='/login.html'; });
const sizeInput = document.getElementById('tile-size');
sizeInput.value = localStorage.getItem('tileMin') || '320';
document.documentElement.style.setProperty('--tile-min', `${sizeInput.value}px`);
sizeInput.addEventListener('input', () => { document.documentElement.style.setProperty('--tile-min', `${sizeInput.value}px`); localStorage.setItem('tileMin', sizeInput.value); });

function computeNewDetKeys(){
  const all = ctx.scanners().flatMap(s => (scanClient.store[s.id]?.detection?.detections)||[]);
  const { keys, newKeys } = diffNewKeys(prevScanKeys, all);
  newDetKeys = new Set(newKeys);
  if (prevScanKeys!==null && newKeys.length && alerter.armed) alerter.beep();
  prevScanKeys = Object.keys(scanClient.store).length ? keys : null;
}
function updateStatus(){
  const cams = ctx.cameras(); const online = cams.filter(d=>d.online).length;
  const pill = document.getElementById('status-pill');
  pill.textContent = `ОПЕРАЦІЙНИЙ · ${online}/${cams.length}`;
  pill.classList.toggle('warn', online < cams.length);
  const dets = ctx.scanners().flatMap(s => (scanClient.store[s.id]?.detection?.detections)||[]).length;
  document.getElementById('global-status').textContent = `${dets} активних детекцій`;
}

async function boot(){
  if (PREVIEW){
    fx = (await import('/fixtures.js')).FIXTURES;
    cfg = fx.config; devices = fx.devices; scanClient.store = structuredClone(fx.scanStore);
    document.getElementById('operator-name').textContent = fx.operator;
    computeNewDetKeys(); updateStatus(); router.start();
    return;
  }
  const c = await fetch('/api/config'); if (c.status===401){location.href='/login.html';return;} cfg = await c.json();
  devices = await fetch('/api/devices').then(r=>r.json());
  computeNewDetKeys(); updateStatus(); router.start();
  const es = new EventSource('/api/stream');
  es.onmessage = (e) => { devices = JSON.parse(e.data); computeNewDetKeys(); updateStatus(); router.renderActive(); };
  es.onerror = () => es.close();
  try { const mq = await fetch('/api/mqtt').then(r=>r.ok?r.json():null);
    if (mq && mq.url) scanClient.connect(mq, () => { computeNewDetKeys(); updateStatus(); router.renderActive(); }); } catch {}
}
boot();
```

- [ ] **Step 6: Write the three view stubs**

`dashboard/public/views/dashboard.js`, `views/nodes.js`, `views/logs.js` — each identical placeholder for now (replaced in Tasks 4–6):
```js
// dashboard/public/views/dashboard.js
export function render(container, ctx){ container.className='screen screen-pad';
  container.innerHTML = `<h2 class="section-title">Панель — ${ctx.cameras().length} камер</h2>`; }
```
(Same shape for `nodes.js` → “Вузли”, `logs.js` → “Детекції”.)

- [ ] **Step 7: Verify shell + router in Chrome (fixtures)**

With `node dashboard/dev-serve.mjs` running, navigate to `http://127.0.0.1:8081/index.html?preview=1`, screenshot.
Expected: sidebar with brand `FPV / SDR MONITOR`, green status pill `ОПЕРАЦІЙНИЙ · 1/2` (warn amber since one camera offline), three nav items, topbar `Панель` + `2 активних детекцій`. Click **Вузли** and **Детекції** → the section swaps, nav active state moves, title updates, and the tile-size slider hides off-dashboard. Read console messages — expect **no errors**.

- [ ] **Step 8: Commit**

```bash
git add dashboard/public/index.html dashboard/public/router.js dashboard/public/modals.js dashboard/public/fixtures.js dashboard/public/app.js dashboard/public/views/
git commit -m "feat(ui): sidebar+router shell, modals module, fixture preview seam"
```

---

### Task 4: Dashboard view (feeds + active detections + node strip)

**Files:**
- Rewrite: `dashboard/public/views/dashboard.js`

**Interfaces:**
- Consumes: `ctx` (contract above); `components.js` atoms; `classColor`,`fmtFreq` from `spectrum.js`.
- Produces: `render(container, ctx)` building `.dash` (feeds grid + threats panel + node strip). Reuses `ctx.handlers.startTile/openVideo/restartTile/openEditForm/viewCreds/deleteDevice/openImage`.

- [ ] **Step 1: Write `views/dashboard.js`**

```js
// dashboard/public/views/dashboard.js — camera feeds + active detections + node telemetry strip.
import { el, pip, cornerCard, occupancyStrip, detectionCard, fmtBitrate, fmtUptime, tempSlot, escapeHtml } from '/views/components.js';
import { detectionKey } from '/alert.js';
import { frameCaption } from '/spectrum.js';

export function render(container, ctx) {
  container.className = 'screen';
  container.innerHTML = '';
  const dash = el('div', 'dash');

  // --- feeds ---
  const feeds = el('div', 'feeds');
  feeds.appendChild(el('div', 'label-caps', 'LIVE ФІДИ'));
  const grid = el('div', 'grid');
  for (const d of ctx.cameras()) {
    const tile = el('section', `tile${d.online?'':' offline'}`);
    tile.innerHTML = `<video autoplay playsinline muted></video>
      <div class="tile-overlay"><div class="tile-top">${pip(d.online)}
        <div class="tile-actions">
          <button class="tile-btn" data-act="restart" title="Перезапуск">🔄</button>
          <button class="tile-btn" data-act="creds" title="Креди">🔑</button>
          <button class="tile-btn" data-act="edit" title="Редагувати">✏️</button>
          <button class="tile-btn" data-act="del" title="Видалити">🗑</button></div></div>
        <div class="tile-meta"><strong>${escapeHtml(d.name)}</strong><small>${escapeHtml(d.location||'')}</small>
          <div class="tile-stats">${d.online?`${fmtBitrate(d.bitrateKbps)} · ${fmtUptime(d.uptimeSec)} · 👁 ${d.readers??0}`:''}</div></div></div>`;
    const video = tile.querySelector('video');
    tile.addEventListener('click', (e) => { if (!e.target.closest('[data-act]')) ctx.handlers.openVideo(d); });
    tile.querySelector('[data-act=restart]').addEventListener('click',(e)=>{e.stopPropagation();ctx.handlers.restartTile(d.id);});
    tile.querySelector('[data-act=creds]').addEventListener('click',(e)=>{e.stopPropagation();ctx.handlers.viewCreds(d.id);});
    tile.querySelector('[data-act=edit]').addEventListener('click',(e)=>{e.stopPropagation();ctx.handlers.openEditForm(d.id);});
    tile.querySelector('[data-act=del]').addEventListener('click',(e)=>{e.stopPropagation();ctx.handlers.deleteDevice(d.id,d.name);});
    grid.appendChild(tile);
    ctx.handlers.startTile(d, video);
  }
  if (!ctx.cameras().length) grid.appendChild(el('p','muted','Немає камер. Додай вузол.'));
  feeds.appendChild(grid);

  // --- active detections (threat logs) ---
  const threats = el('div', 'threats');
  threats.appendChild(el('div','label-caps','АКТИВНІ ДЕТЕКЦІЇ'));
  const store = ctx.scanStore(); const newKeys = ctx.newDetKeys();
  const dets = ctx.scanners().flatMap(s => (store[s.id]?.detection?.detections||[]).map(x=>({...x, _sid:s.id})))
    .sort((a,b)=>(b.power_dbm??-999)-(a.power_dbm??-999));
  if (!dets.length) threats.appendChild(el('p','muted','Немає активних передавачів.'));
  for (const d of dets) {
    const card = detectionCard(d, newKeys.has(detectionKey(d)));
    const v = store[d._sid]?.video;
    if (v && v.frame_png_b64) { card.style.cursor='pointer';
      card.addEventListener('click',()=>ctx.handlers.openImage(`data:image/png;base64,${v.frame_png_b64}`, frameCaption(v))); }
    threats.appendChild(card);
  }

  // --- node telemetry strip ---
  const strip = el('div','node-strip');
  for (const d of ctx.devices()) {
    const live = store[d.id];
    const isScanner = d.kind==='scanner';
    const online = isScanner ? !!(live&&live.online) : d.online;
    const card = cornerCard(`<div class="nc-head"><span class="nc-title">${escapeHtml(d.name)}</span>${pip(online)}</div>
      <div class="nc-grid">
        <div><span class="k">TEMP</span>${tempSlot(null)}</div>
        <div><span class="k">UPTIME</span><span class="mono">${fmtUptime(d.uptimeSec)}</span></div>
      </div>`);
    if (isScanner && live) card.appendChild(occupancyStrip(live.bands, live.detection?.occupancy||{}));
    strip.appendChild(card);
  }

  dash.appendChild(feeds); dash.appendChild(threats); dash.appendChild(strip);
  container.appendChild(dash);
}
```

- [ ] **Step 2: Verify in Chrome (fixtures)**

Reload `http://127.0.0.1:8081/index.html?preview=1` on `#/dashboard`, screenshot.
Expected: two camera tiles (one dimmed OFFLINE), the right **АКТИВНІ ДЕТЕКЦІЇ** column with the analog `5800 МГц` card (green border, NEW outline) and the digital `915 МГц` card, and a bottom strip of three corner-mark node cards each showing `TEMP —°C` + uptime, the bladeRF card also showing occupancy bars. No console errors.

- [ ] **Step 3: Commit**

```bash
git add dashboard/public/views/dashboard.js
git commit -m "feat(ui): dashboard screen — feeds, active detections, node telemetry strip"
```

---

### Task 5: Node Management view (cards + CRUD + RX5808 controls)

**Files:**
- Rewrite: `dashboard/public/views/nodes.js`

**Interfaces:**
- Consumes: `ctx`; `components.js`; RX5808 channel list `RX5808_CHANNELS` from `/rx5808-channels.js`.
- Produces: `render(container, ctx)` building a responsive grid of `.node-card` for every device with actions + (scanners) RX5808 mode buttons + channel `<select>` wired to `ctx.onScanClick`.

- [ ] **Step 1: Write `views/nodes.js`**

```js
// dashboard/public/views/nodes.js — node management: all devices as cards, CRUD, RX5808 controls.
import { el, pip, occupancyStrip, fmtUptime, fmtBitrate, tempSlot, telemetryLine, escapeHtml } from '/views/components.js';
import { RX5808_CHANNELS } from '/rx5808-channels.js';

function rx5808Controls(scannerId, activeMode, ctx) {
  const row = el('div', 'rx5808-ctl');
  for (const m of ['auto','scan','random','manual']) {
    const b = el('button', `rx-mode${m===activeMode?' active':''}`, m);
    b.addEventListener('click', () => ctx.onScanClick(scannerId, { mode:m }));
    row.appendChild(b);
  }
  const sel = el('select', 'rx5808-ch');
  for (const ch of RX5808_CHANNELS){ const o=document.createElement('option'); o.value=ch.name; o.textContent=`${ch.name} · ${ch.freq}`; sel.appendChild(o); }
  sel.addEventListener('change', () => ctx.onScanClick(scannerId, { mode:'manual', channel: sel.value }));
  row.appendChild(sel);
  return row;
}

export function render(container, ctx) {
  container.className = 'screen screen-pad';
  container.innerHTML = '';
  container.appendChild(el('div','label-caps','КЕРУВАННЯ ВУЗЛАМИ'));
  const grid = el('div','node-strip');
  const store = ctx.scanStore();
  for (const d of ctx.devices()) {
    const isScanner = d.kind==='scanner';
    const live = store[d.id];
    const online = isScanner ? !!(live&&live.online) : d.online;
    const card = el('div','node-card');
    card.innerHTML = `<div class="nc-head"><div><div class="nc-title">${escapeHtml(d.name)}</div>
        <div class="nc-sub">${escapeHtml(d.id)} · ${isScanner?'SCANNER':'CAMERA'}</div></div>${pip(online)}</div>
      <div class="nc-grid">
        <div><span class="k">TEMP</span>${tempSlot(null)}</div>
        <div><span class="k">UPTIME</span><span class="mono">${fmtUptime(d.uptimeSec)}</span></div>
        <div><span class="k">${isScanner?'ЛОКАЦІЯ':'BITRATE'}</span><span class="mono">${isScanner?escapeHtml(d.location||'—'):fmtBitrate(d.bitrateKbps)}</span></div>
        <div><span class="k">${isScanner?'ДЕТЕКЦІЙ':'TELEMETRY'}</span><span class="mono">${isScanner?((live?.detection?.detections?.length)||0):escapeHtml(telemetryLine(d.telemetry)||'—')}</span></div>
      </div>`;
    if (isScanner && live) card.appendChild(occupancyStrip(live.bands, live.detection?.occupancy||{}));
    if (isScanner) card.appendChild(rx5808Controls(d.id, live?.rxtune?.mode||null, ctx));
    const actions = el('div','nc-actions',
      `<button class="btn" data-act="edit">✏️ Редагувати</button>
       <button class="btn" data-act="${isScanner?'info':'creds'}">🔑 ${isScanner?'Інфо':'Креди'}</button>
       ${isScanner?'':'<button class="btn" data-act="restart">🔄 Перезапуск</button>'}
       <button class="btn" data-act="del">🗑 Видалити</button>`);
    actions.querySelector('[data-act=edit]').addEventListener('click',()=>ctx.handlers.openEditForm(d.id));
    actions.querySelector('[data-act=del]').addEventListener('click',()=>ctx.handlers.deleteDevice(d.id,d.name));
    const infoBtn = actions.querySelector('[data-act=info]'); if(infoBtn) infoBtn.addEventListener('click',()=>ctx.handlers.scannerInfo(d.id));
    const credBtn = actions.querySelector('[data-act=creds]'); if(credBtn) credBtn.addEventListener('click',()=>ctx.handlers.viewCreds(d.id));
    const reBtn = actions.querySelector('[data-act=restart]'); if(reBtn) reBtn.addEventListener('click',()=>ctx.handlers.restartTile(d.id));
    card.appendChild(actions);
    grid.appendChild(card);
  }
  container.appendChild(grid);
}
```

- [ ] **Step 2: Verify in Chrome (fixtures)**

Navigate to `#/nodes`, screenshot. Expected: three node cards (two cameras, one scanner). The bladeRF card shows `SCANNER`, TEMP `—°C`, occupancy bars, the 4 RX5808 mode buttons (`scan` highlighted green), a channel dropdown, and actions `Редагувати / Інфо / Видалити`. Camera cards show `CAMERA`, bitrate, `Перезапуск`. Click **Редагувати** on a camera → themed `.card2` edit modal opens; close it. Click an RX5808 **auto** button — in preview `onScanClick` is a no-op (confirm no console error). No errors.

- [ ] **Step 3: Commit**

```bash
git add dashboard/public/views/nodes.js
git commit -m "feat(ui): node management screen — cards, CRUD, RX5808 controls"
```

---

### Task 6: Detection Logs view + spectrum trim

**Files:**
- Modify: `dashboard/public/spectrum.js` (remove waterfall DOM; add `renderMiniSpectrum`)
- Rewrite: `dashboard/public/views/logs.js`

**Interfaces:**
- `spectrum.js` keeps ALL current pure-helper exports unchanged (tests depend on them). Removes the unused DOM functions `renderSpectrum/scannerBlock/bandCell/rx5808Controls/detectionTable` (superseded by views). Adds:
  - `renderMiniSpectrum(canvas, { psd, range, dets, rxFreq, tunable }) -> void` — draws only the PSD polyline + detection/RX marks into an existing `<canvas>` (no waterfall).
- `logs.js` — `render(container, ctx)`: async-loads `ctx.getDetections()`, builds the history `.data-table`, the side **Live Spectrum Analysis** (band picker + `<canvas class="mini-spectrum">` with click-to-tune) and a recovered-frames grid.

- [ ] **Step 1: Trim `spectrum.js` — remove waterfall DOM, add `renderMiniSpectrum`**

Delete the functions `renderSpectrum`, `scannerBlock`, `rx5808Controls`, `bandCell`, `detectionTable`, and the local `el` helper (lines from `// ---- DOM rendering` to end, i.e. everything after `psdColor`). Keep every exported pure helper above it unchanged. Append:
```js
// ---- mini live spectrum (PSD line + marks only; NO waterfall) ----
export function renderMiniSpectrum(canvas, { psd = [], range = {}, dets = [], rxFreq = null, tunable = false }) {
  const w = canvas.width, h = canvas.height, c = canvas.getContext('2d');
  c.clearRect(0, 0, w, h);
  const pts = psdToPoints(psd, w, h);
  if (pts.length) { c.strokeStyle = '#00e5ff'; c.lineWidth = 1; c.beginPath();
    pts.forEach((p, i) => (i ? c.lineTo(p.x, p.y) : c.moveTo(p.x, p.y))); c.stroke(); }
  for (const d of dets) { const x = detectionX(d.center_mhz, range.low_mhz, range.high_mhz, w);
    c.strokeStyle = classColor(d.class); c.lineWidth = 2; c.beginPath(); c.moveTo(x, 0); c.lineTo(x, h); c.stroke(); }
  if (rxFreq != null && range.low_mhz != null && rxFreq >= range.low_mhz && rxFreq <= range.high_mhz) {
    const x = detectionX(rxFreq, range.low_mhz, range.high_mhz, w);
    c.strokeStyle = '#39d0ff'; c.lineWidth = 2; c.beginPath(); c.moveTo(x, 0); c.lineTo(x, h); c.stroke(); }
  if (tunable) canvas.classList.add('tunable');
}
```

- [ ] **Step 2: Run the unit tests — spectrum helpers still pass**

Run: `npm test`
Expected: PASS. In particular `test/spectrum.test.js` (splitByKind, classColor, fmtFreq, fmtPct, psdToPoints, detectionX, psdColor, frameCaption, rxtuneCaption) is green — the trim only removed untested DOM code.

- [ ] **Step 3: Write `views/logs.js`**

```js
// dashboard/public/views/logs.js — detection history + live spectrum + recovered frames.
import { el, escapeHtml } from '/views/components.js';
import { classColor, fmtFreq, renderMiniSpectrum, frameCaption } from '/spectrum.js';
import { nearestRxChannel } from '/rx5808-channels.js';

function historyTable(rows){
  if (!rows.length) return el('p','muted','Журнал порожній.');
  const t = el('table','data-table','<thead><tr><th>Час</th><th>Сканер</th><th>Бенд</th><th>Частота</th><th>Клас</th><th>SNR</th><th>Подія</th></tr></thead>');
  const tb = el('tbody');
  for (const e of rows){
    const d=new Date(Number(e.ts)*1000); const p=n=>String(n).padStart(2,'0');
    const when=`${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
    const freq=`${fmtFreq(e.center_mhz)}${e.channel?` (${escapeHtml(e.channel)})`:''}`;
    const ev=e.event==='gone'?'<span style="color:var(--muted)">зник</span>':'<span style="color:var(--on)">з\\'явився</span>';
    tb.appendChild(el('tr',null,`<td>${when}</td><td>${escapeHtml(e.scanner_id||'')}</td><td>${escapeHtml(e.band||'')}</td><td>${freq}</td><td style="color:${classColor(e.class)}">${escapeHtml(e.class||'')}</td><td>${e.snr_db==null?'—':escapeHtml(String(e.snr_db))} dB</td><td>${ev}</td>`));
  }
  t.appendChild(tb); return t;
}

export function render(container, ctx){
  container.className='screen';
  container.innerHTML='';
  const layout=el('div','logs');
  const main=el('div','logs-main'); main.appendChild(el('div','label-caps','ІСТОРІЯ ДЕТЕКЦІЙ'));
  const tableSlot=el('div',null,'<p class="muted">Завантаження…</p>'); main.appendChild(tableSlot);

  const side=el('div','logs-side');
  const scanners=ctx.scanners(); const store=ctx.scanStore();
  const sid=scanners[0]?.id; const live=sid?store[sid]:null;
  const bands=live?Object.keys(live.bands||{}):[];
  side.appendChild(el('div','label-caps','LIVE SPECTRUM'));
  if (live && bands.length){
    let band=bands.find(b=>b==='5.8G')||bands[0];
    const picker=el('div','rx5808-ctl');
    for (const b of bands){ const btn=el('button',`rx-mode${b===band?' active':''}`,b); btn.addEventListener('click',()=>{band=b;draw();for(const x of picker.children)x.classList.toggle('active',x.textContent===b);}); picker.appendChild(btn); }
    side.appendChild(picker);
    const canvas=document.createElement('canvas'); canvas.width=300; canvas.height=60; canvas.className='mini-spectrum'; side.appendChild(canvas);
    function draw(){ const range=live.bands[band]||{}; const psd=(live.latestPsd&&live.latestPsd[band])||[];
      const dets=(live.detection?.detections||[]).filter(d=>d.band===band); const rxFreq=live.rxtune?.freq_mhz??null;
      renderMiniSpectrum(canvas,{psd,range,dets,rxFreq,tunable:range.low_mhz!=null}); }
    canvas.addEventListener('click',(e)=>{ const range=live.bands[band]||{}; if(range.low_mhz==null)return;
      const r=canvas.getBoundingClientRect(); const x=Math.min(r.width,Math.max(0,e.clientX-r.left));
      const freq=range.low_mhz+(x/r.width)*(range.high_mhz-range.low_mhz); const ch=nearestRxChannel(freq);
      if(ch) ctx.onScanClick(sid,{mode:'manual',channel:ch.name}); });
    draw();
  } else side.appendChild(el('p','muted','Немає активного сканера.'));

  side.appendChild(el('div','label-caps','ВІДНОВЛЕНІ КАДРИ'));
  const frames=el('div','frames-grid');
  let any=false;
  for (const s of scanners){ const v=store[s.id]?.video; if (v&&v.frame_png_b64){ any=true;
    const img=document.createElement('img'); img.src=`data:image/png;base64,${v.frame_png_b64}`; img.alt='frame';
    img.addEventListener('click',()=>ctx.handlers.openImage(img.src,frameCaption(v))); frames.appendChild(img);} }
  if (!any) frames.appendChild(el('p','muted','Кадрів ще немає.'));
  side.appendChild(frames);

  layout.appendChild(main); layout.appendChild(side); container.appendChild(layout);
  ctx.getDetections().then(rows => { tableSlot.innerHTML=''; tableSlot.appendChild(historyTable(rows)); });
}
```

- [ ] **Step 4: Verify in Chrome (fixtures)**

Navigate to `#/logs`, screenshot. Expected: left **ІСТОРІЯ ДЕТЕКЦІЙ** table with 3 rows (mono, 1px dividers, class colored, `з'явився`/`зник`); right side a band picker (`5.8G` active), a `mini-spectrum` canvas drawing a **cyan PSD line with a green detection mark** and **no waterfall**, and a “Кадрів ще немає.” empty state (fixture frame is blank). Click a point on the spectrum — no console error (preview no-op). No errors.

- [ ] **Step 5: Commit**

```bash
git add dashboard/public/spectrum.js dashboard/public/views/logs.js
git commit -m "feat(ui): detection logs screen; drop waterfall, add mini live spectrum"
```

---

### Task 7: Re-theme the login page

**Files:**
- Rewrite: `dashboard/public/login.html`

**Interfaces:**
- Standalone page (served before auth). Must keep posting the same form: `POST /login` with fields `user`, `pass` (see `server.js` `/login`), and honor `?error=1`.

- [ ] **Step 1: Inspect the current login form contract**

Run: `cat dashboard/public/login.html`
Confirm the field names (`user`, `pass`), the form `action`/`method`, and the `?error=1` handling. Preserve exactly.

- [ ] **Step 2: Write the themed `login.html`**

```html
<!doctype html>
<html lang="uk">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>FPV — Вхід</title>
  <link rel="stylesheet" href="/styles.css" />
</head>
<body style="display:flex;align-items:center;justify-content:center;min-height:100vh;">
  <form class="card2" method="POST" action="/login" style="width:min(360px,92vw);border:2px solid var(--line);">
    <div class="brand" style="font-size:18px;margin-bottom:4px;">FPV<small>SDR MONITOR · ВХІД</small></div>
    <p class="form-err" id="err"></p>
    <div class="form">
      <label>Користувач<input name="user" autocomplete="username" required autofocus /></label>
      <label>Пароль<input name="pass" type="password" autocomplete="current-password" required /></label>
      <div class="form-actions"><button type="submit" class="btn btn-primary" style="width:100%;">Увійти</button></div>
    </div>
  </form>
  <script>
    if (new URLSearchParams(location.search).has('error'))
      document.getElementById('err').textContent = 'Невірний логін або пароль';
  </script>
</body>
</html>
```
(Keep the exact field names/action/method found in Step 1; only the markup/theme changes.)

- [ ] **Step 3: Verify in Chrome**

With the dev server running, navigate to `http://127.0.0.1:8081/login.html?error=1`, screenshot. Expected: centered tactical card, `FPV / SDR MONITOR · ВХІД`, two dark inputs with green focus border, full-width green **Увійти** button, red error line `Невірний логін або пароль`. Confirm the form still has `method="POST" action="/login"` and fields `user`/`pass`.

- [ ] **Step 4: Commit**

```bash
git add dashboard/public/login.html
git commit -m "feat(ui): re-theme login page to tactical style"
```

---

### Task 8: Integration cleanup + live acceptance

**Files:**
- Modify (only if leftovers found): any file still referencing removed markup/classes.

**Interfaces:** none new — this task confirms the whole works end-to-end and removes dead references.

- [ ] **Step 1: Grep for dead references**

Run:
```bash
grep -rn "spectrum-panel\|renderSpectrum\|scan-block\|scan-charts\|chart-wf\|waterfall" dashboard/public || echo "clean"
```
Expected: `clean` (no references to the removed spectrum panel / waterfall in `dashboard/public`). If any remain in `app.js`/`index.html`, remove them and re-verify the affected screen in Chrome.

- [ ] **Step 2: Syntax-check all modules**

Run:
```bash
for f in dashboard/public/app.js dashboard/public/router.js dashboard/public/modals.js dashboard/public/spectrum.js dashboard/public/views/*.js; do node --check "$f" && echo "ok $f"; done
npm test
```
Expected: `ok` for every file; `npm test` PASS.

- [ ] **Step 3: Full fixture walkthrough in Chrome**

With `node dashboard/dev-serve.mjs` running, at `http://127.0.0.1:8081/index.html?preview=1`: visit all three screens, open a camera fullscreen (image/video modal), open Add-node and Edit modals, open a recovered frame. Screenshot each. Confirm: consistent tactical theme, sharp corners, mono data, no console errors on any screen.

- [ ] **Step 4: Commit any cleanup**

```bash
git add -A dashboard/public
git commit -m "chore(ui): remove dead spectrum-panel references after redesign" --allow-empty
```

- [ ] **Step 5: Live acceptance (operator-driven, over WireGuard)**

The dev server + fixtures verify rendering/interaction but not live data. Hand off to the operator to deploy via the normal flow (`install.sh` on the dev server, per project deploy notes) and verify over WG:
- Login works; dashboard loads with the tactical theme.
- Camera tiles play **live WHEP video**; fullscreen modal plays.
- **Активні детекції** update live from real scanners; a new detection highlights and (when sound armed) beeps.
- Node cards show live online/uptime; TEMP shows `—°C` (placeholder, expected).
- **Детекції** table loads from `/api/detections`; live spectrum draws with no waterfall; recovered frames open.
- RX5808 mode/channel/click-to-tune still command the Pi (`fpv/<id>/rxcmd`) — verify with a real scanner if RX5808 is in use.
- Add/edit/delete/creds device flows work.

Document any live-only issues as follow-up; they are front-end wiring bugs (data plumbing itself is unchanged).

---

## Self-Review (author check against the spec)

**Spec coverage:**
- Sidebar + 3 screens → Tasks 3–6. ✓
- Waterfalls removed → Task 6 Step 1. ✓
- Small live spectrum + occupancy kept → Task 6 (`renderMiniSpectrum`) + `occupancyStrip` (Tasks 4–5). ✓
- RX5808 full functionality → Task 5 (mode/channel) + Task 6 (click-to-tune). ✓
- Node TEMP slots (placeholder) → `tempSlot(null)` in Tasks 4–5. ✓
- Tactical theme + tokens + corner-marks + pips + data tables → Task 1. ✓
- Self-hosted fonts → Task 1. ✓
- Login re-theme → Task 7. ✓
- Preserve WHEP/SSE/MQTT/CRUD/sound/tile-size → Tasks 3–5 (ctx + modals + app.js). ✓
- No server/Pi/API changes; zero-build; Ukrainian strings → Global Constraints, honored throughout. ✓

**Placeholder scan:** No "TBD/TODO/handle edge cases". `fixtures.js` blank frame + unused `PNG` const are called out explicitly. TEMP `—°C` is an intended product placeholder, not a plan gap.

**Type consistency:** `ctx` accessors (`devices()/scanStore()/scanners()/cameras()/newDetKeys()/getDetections()/onScanClick()/requestRender()`) and `ctx.handlers.*` names are identical across Tasks 3–6. `render(container, ctx)` signature uniform. `renderMiniSpectrum(canvas, opts)` defined in Task 6 Step 1, consumed in Task 6 Step 3. Component names (`el/pip/cornerCard/occupancyStrip/detectionCard/tempSlot/fmtBitrate/fmtUptime/telemetryLine/escapeHtml`) defined in Task 2, used consistently.

**Note for executor:** `alert.js` must export `diffNewKeys`, `SoundAlerter`, and `detectionKey` (used by `app.js`, `dashboard.js`, and the current code). Confirm these exports exist before Task 3/4 (they are used by today's `app.js` and `spectrum.js`); if `diffNewKeys`/`detectionKey` live under different names, adjust the imports to match.
