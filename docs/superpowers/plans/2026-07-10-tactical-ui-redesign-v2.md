# Tactical UI Redesign v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Re-skin `dashboard/public/` into the tactical AERO-SHIELD look (sidebar + 5 screens) **on top of current `main`**, preserving every existing feature: camera feeds, FPV Viewer (merged detections + shared gen-token player), scanner/RX5808/SDR-view controls, detection journal, frames gallery. Waterfalls removed; node TEMP slots kept.

**Architecture:** Zero-build static. A thin `app.js` builds shared stores (SSE devices, `MqttScanClient`, `SoundAlerter`, `viewer.js` `viewerState`) once + owns the player lifecycles (camera `players` Map; viewer stream-keyed generation-token player), and passes a `ctx` object to pure view modules (`views/{dashboard,viewer,nodes,detections,frames}.js`). A hash router shows/hides 5 `<section>`s. A dev `?preview=1` seam + `fixtures.js` render the real page with sample data for verification.

**Tech Stack:** Vanilla ES modules, plain CSS with custom properties, self-hosted woff2, Express (dev server), Chrome automation for visual verification.

## Global Constraints

- **Zero-build static only** — no bundler/Tailwind/framework.
- **Front-end only** — do NOT change `dashboard/server.js`, `lib/*`, Pi agents, MQTT topics, or `/api/*`. Do NOT modify `whep.js`, `alert.js`, `mqtt-scan.js`, `rx5808-channels.js`, `viewer.js` (import only). `frames-gallery.js` logic (`buildFramesQuery`, `BAND_PRESETS`, `scannerOptions`, `toLocalDatetime`, `scannerIds`) is reused; only its `galleryHtml` presentation is re-themed (in a view, not by editing the file unless noted).
- **Preserve all behavior + data contracts** (see "Contracts" below) — camera WHEP, FPV Viewer scan→click→watch, generation-token viewer player, RX5808 mode/channel/click-to-tune, SDR view start/stop, detection journal, frames gallery filters+pagination, sound alerts (gesture-armed), tile-size, clipboard exec-fallback.
- **Sharp 0px corners; no shadow/gradient/blur.** Depth = tonal + 1px outlines.
- Tokens: base `#121212`, panel `#1A1A1A`, line `#2E2E2E`, primary/safe `#00FF41`, warn `#FF8C00`, threat `#FF3131`, aux `#00E5FF`, text `#E5E2E1`, muted `#9AA096`.
- Fonts: Geist (UI) + JetBrains Mono (all dynamic data). Self-hosted; fallbacks `system-ui` / `ui-monospace`.
- **UI language Ukrainian**; keep code identifiers, MQTT tokens, API field names as-is.
- **Node temperature = UI slot only** (`—°C`); the `telemetry` device field is dead — do NOT render it.
- **Waterfalls removed**; keep a small `renderMiniSpectrum` PSD line + occupancy.
- `npm test` MUST stay green. Keep every currently-exported pure helper in `spectrum.js` (`splitByKind, classColor, fmtFreq, fmtPct, frameCaption, rxtuneCaption, viewCaption, psdToPoints, detectionX, psdColor`).
- Branch: `feat/tactical-ui-redesign-v2` (off `origin/main`, already created). Commit after every task.
- **Reuse source:** the v1 branch `feat/tactical-ui-redesign` has ready theme/infra — retrieve files with `git show feat/tactical-ui-redesign:<path> > <path>`.

## Contracts (preserve exactly — from the current main code)

**Scan store** `scanClient.store[id]` (`mqtt-scan.js`):
```
{ online, status_ts, detection:{ts,detections:[{band,center_mhz,channel?,class,snr_db?,power_dbm?,bandwidth_mhz?,confidence?}],occupancy:{band:frac}}|null,
  video:{ts,center_mhz,standard,line_hz,sync_snr_db,frame_png_b64}|null,
  rxtune:{ts,freq_mhz,channel,mode,targets}|null,
  view:{ts,active,freq_mhz,until_ts,error,stream}|null,
  bands:{[b]:{low_mhz,high_mhz}}, latestPsd:{[b]:number[]}, waterfalls:{[b]:[{ts,psd}]} }
```
**Device** (SSE `/api/stream`, `/api/devices`): `{id,name,location,kind,online,readers,bytesReceived,uptimeSec,bitrateKbps}`.
**whep.js**: `startWhep(video,url,user,pass,onDead?) → Promise<{close()}>`; `close()` only blanks `video.srcObject` if the video still owns its stream (stale-close guard).
**viewer.js exports**: `RECENT_TTL_S, LIVE_STALE_S, emptyViewer, applyDetections, seedFromJournal, viewerRows, pickViewer, activeViewer, pickRxScanner, viewStream, playerKey, ageLabel, viewerListHtml, whepRetryDelay`.
**alert.js**: `detectionKey(d)`, `diffNewKeys(prev,dets)→{keys,newKeys}`, `SoundAlerter`.
**mqtt-scan.js `MqttScanClient`**: `connect({url,user,pass},onChange)`, `publishCommand(id,cmd)` (retain:true), `publishView(id,action,freqMhz)` (retain:false).
**APIs**: `GET /api/config|devices|mqtt|detections?limit=|frames?<filters>&before=`; `POST /api/devices`, `PATCH/DELETE /api/devices/:id`, `GET /api/devices/:id/push`, `POST /login|logout`.

**Gotchas that must survive:** viewer player keyed by **stream name** with generation-token retry (every retry callback re-checks `viewerRetry!==retry` at entry/after-await/after success+failure; `playerKey` ignores freq/until_ts for smooth retune; `pickViewer`≠`activeViewer`); view commands NOT retained; `applyDetections` ts-idempotent; 30 s viewer ticker; seed viewer from `/api/detections` on load; dual-action on 5.8G (view start + RX5808 nearest-tune); save/restore typed freq input across re-render; sound needs a gesture; clipboard exec fallback.

## ctx contract (built in Task 2; consumed by views)
```
ctx = {
  cfg, isPreview,
  devices():Device[], scanStore():store, scanners():Device[], cameras():Device[],
  viewerState():{entries,seenTs},           // the viewer.js state object
  newDetKeys():Set,                          // highlight set from diffNewKeys
  getDetections():Promise<Row[]>,            // journal (fixtures in preview)
  fetchFrames(query):Promise<{frames,...}>,  // /api/frames (fixtures in preview)
  onScanCmd(id,cmd), onViewStart(id,freq), onViewStop(id),  // publish (no-op in preview)
  requestRender(),                           // re-render active screen
  handlers:{ openVideo(d), openImage(src,cap), startTile(d,videoEl), closeTile(id), restartTile(id),
             openAddForm(), openEditForm(id), viewCreds(id), scannerInfo(id), deleteDevice(id,name),
             viewerRowClick(freq,band,scannerId), syncViewerPlayer() },
}
```
Views are pure w.r.t. `ctx`: read via accessors, wire listeners to `ctx.handlers`/`ctx.on*`, never fetch or touch globals. Camera-feeds and the viewer player are reconcile-based (reuse DOM/players; never `innerHTML=''` a container holding a live `<video>`).

---

### Task 1: Theme, fonts, components, router, dev harness (reuse from v1)

**Files (retrieve from v1 branch verbatim, then extend styles.css):**
- Create: `dashboard/public/styles.css`, `dashboard/public/vendor/fonts/*.woff2`, `dashboard/dev-serve.mjs`, `dashboard/public/dev-preview.html`, `dashboard/public/views/components.js`, `dashboard/public/router.js`.

**Interfaces produced:** the tactical CSS class contract + `components.js` atoms (`escapeHtml, el, fmtBitrate, fmtUptime, tempSlot, pip, cornerCard, occupancyStrip, detectionCard`) + `createRouter({routes,ctx})→{start(),renderActive()}`.

- [ ] **Step 1: Retrieve v1 assets**
```bash
git show feat/tactical-ui-redesign:dashboard/public/styles.css > dashboard/public/styles.css
mkdir -p dashboard/public/vendor/fonts
for f in geist-400 geist-500 geist-600 geist-700 jbmono-400 jbmono-500 jbmono-700; do git show feat/tactical-ui-redesign:dashboard/public/vendor/fonts/$f.woff2 > dashboard/public/vendor/fonts/$f.woff2; done
git show feat/tactical-ui-redesign:dashboard/dev-serve.mjs > dashboard/dev-serve.mjs
git show feat/tactical-ui-redesign:dashboard/public/dev-preview.html > dashboard/public/dev-preview.html
mkdir -p dashboard/public/views
git show feat/tactical-ui-redesign:dashboard/public/views/components.js > dashboard/public/views/components.js
git show feat/tactical-ui-redesign:dashboard/public/router.js > dashboard/public/router.js
```
Verify each file is non-empty (`wc -l`). The fonts must be > 5 KB each (`ls -la`).

- [ ] **Step 2: Extend `styles.css` with viewer/gallery/view-control component rules**

Append this block to the end of `dashboard/public/styles.css`:
```css
/* ---- FPV Viewer screen ---- */
.viewer{display:grid;grid-template-columns:360px 1fr;gap:var(--gutter);padding:var(--gutter);}
.viewer-list{display:flex;flex-direction:column;gap:6px;max-height:calc(100vh - 120px);overflow:auto;}
.viewer-row{border:1px solid var(--line);border-left:3px solid var(--muted);padding:7px 10px;cursor:pointer;background:var(--panel-2);}
.viewer-row.analog{border-left-color:var(--on);} .viewer-row.digital{border-left-color:var(--warn);}
.viewer-row.recent{opacity:.55;} .viewer-row.is-viewing{outline:1px solid var(--aux);}
.viewer-row .vr-top{display:flex;justify-content:space-between;gap:8px;font-family:var(--mono);}
.viewer-row .vr-freq{font-weight:700;font-size:15px;} .viewer-row .vr-meta{font-size:11px;color:var(--muted);margin-top:3px;}
.viewer-stage{display:flex;flex-direction:column;gap:10px;min-width:0;}
.viewer-stage video{width:100%;aspect-ratio:4/3;background:#000;border:1px solid var(--line);}
.view-controls{display:flex;gap:8px;align-items:center;flex-wrap:wrap;}
.view-controls input{width:120px;padding:5px 7px;background:#0d0d0d;border:1px solid var(--line);color:var(--text);font-family:var(--mono);}
.view-badge{font-family:var(--mono);font-size:11px;color:var(--aux);} .view-err{color:var(--threat);font-size:11px;font-family:var(--mono);}
/* ---- Frames gallery screen ---- */
.frames-toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;padding:var(--gutter);border-bottom:1px solid var(--line);}
.frames-toolbar select,.frames-toolbar input{background:#0d0d0d;border:1px solid var(--line);color:var(--text);padding:5px 7px;font-family:var(--mono);font-size:12px;}
.frames-grid2{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;padding:var(--gutter);}
.frames-grid2 figure{margin:0;border:1px solid var(--line);background:var(--panel-2);cursor:pointer;}
.frames-grid2 img{width:100%;display:block;} .frames-grid2 figcaption{font-family:var(--mono);font-size:10px;color:var(--muted);padding:4px 6px;}
.frames-more{display:flex;justify-content:center;padding:var(--gutter);}
```

- [ ] **Step 3: Verify the styleguide in Chrome (controller does this)**
Implementer: run `node --check dashboard/dev-serve.mjs`. Do NOT run Chrome — controller screenshots `http://127.0.0.1:8081/dev-preview.html`.

- [ ] **Step 4: Commit**
```bash
git add dashboard/public/styles.css dashboard/public/vendor/fonts dashboard/dev-serve.mjs dashboard/public/dev-preview.html dashboard/public/views/components.js dashboard/public/router.js
git commit -m "feat(ui): v2 theme/fonts/components/router + dev harness (reused from v1)"
```

---

### Task 2: App shell + modals + fixtures + app.js bootstrap (stub screens)

**Files:**
- Create: `dashboard/public/index.html` (rewrite), `dashboard/public/modals.js`, `dashboard/public/fixtures.js`, `dashboard/public/app.js` (rewrite), and 5 stub views `dashboard/public/views/{dashboard,viewer,nodes,detections,frames}.js`.

**Interfaces:** `ctx` (above); `render(container, ctx)` per view. `app.js` owns the camera `players` Map + the viewer generation-token player (relocated from main's `app.js:327-402`, behavior-preserving) + SSE/MQTT/30 s-ticker + `?preview` seam.

- [ ] **Step 1: Study main's app.js player/viewer logic**
Read the CURRENT `dashboard/public/app.js` (on this branch it is `origin/main`'s version). Note verbatim, to relocate WITHOUT behavior change into the new `app.js`:
- camera `players` Map lifecycle (`startPlayer`/`restartTile`/`restartAll`, reconcile in `render`);
- the viewer player: `viewerPlayer`, `viewerStreamKey`, `viewerRetry` generation token, `syncViewerPlayer(store,viewerId,view)`, `startViewerWhep(video,stream,retry,attempt)` — and the re-check discipline;
- `renderViewer()` (folds detections via `applyDetections`, `pickViewer`/`activeViewer`, updates the viewer panel), the seed-from-journal on init, and the `setInterval(renderViewer, 30000)` ticker;
- `renderScan()` save/restore of typed `.view-freq` inputs;
- `computeNewDetKeys`/beep, `connectSSE`, device CRUD + modals, journal + frames modal fetchers.

- [ ] **Step 2: Write `index.html` shell**
```html
<!doctype html><html lang="uk"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>FPV — Тактичний монітор</title>
<link rel="preload" href="/vendor/fonts/jbmono-400.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="/styles.css"/></head>
<body><div class="app">
  <aside class="sidebar">
    <div class="brand">FPV<small>SDR MONITOR</small></div>
    <div id="status-pill" class="status-pill">—</div>
    <nav class="nav" id="nav"></nav><div class="spacer"></div>
    <div class="operator"><span id="operator-name" class="mono">operator</span>
      <button id="logout" class="btn">Вийти</button></div>
  </aside>
  <div class="main">
    <header class="topbar"><h1 id="screen-title">Панель</h1>
      <span id="global-status" class="global-status"></span><span class="grow"></span>
      <label class="size-ctl" id="size-ctl" title="Розмір вікон">▭
        <input type="range" id="tile-size" min="240" max="720" step="20"/></label>
      <button id="sound-toggle" class="btn" title="Звук сповіщень">🔕</button>
      <button id="restart-all" class="btn" title="Перезапустити перегляди">🔄</button>
      <button id="add-device" class="btn btn-primary">➕ Додати вузол</button></header>
    <main id="screens">
      <section id="screen-dashboard" class="screen"></section>
      <section id="screen-viewer" class="screen hidden"></section>
      <section id="screen-nodes" class="screen hidden"></section>
      <section id="screen-detections" class="screen hidden"></section>
      <section id="screen-frames" class="screen hidden"></section>
    </main></div></div>
  <div id="modal" class="modal hidden"><button id="modal-close" class="modal-close">✕</button>
    <video id="modal-video" autoplay playsinline muted></video>
    <img id="modal-image" class="modal-image hidden" alt="frame"/><div id="modal-caption" class="modal-caption"></div></div>
  <div id="form-modal" class="modal2 hidden"><div class="card2"><button class="card-close" data-close>✕</button>
    <div id="form-modal-body"></div></div></div>
  <script src="/vendor/mqtt.min.js"></script><script type="module" src="/app.js"></script>
</body></html>
```

- [ ] **Step 3: Create `modals.js`** — retrieve v1's verbatim (viewer/image modal + add/edit/creds/scanner-info + device CRUD), it already matches the contracts:
```bash
git show feat/tactical-ui-redesign:dashboard/public/modals.js > dashboard/public/modals.js
```
Verify it imports `startWhep` from `/whep.js` and `escapeHtml` from `/views/components.js`, and exports `createModals(ctx)→{openVideo,openImage,openAddForm,openEditForm,viewCreds,scannerInfo,deleteDevice}`.

- [ ] **Step 4: Write `fixtures.js`** (dev sample data incl. an ACTIVE view-state + frames):
```js
// dashboard/public/fixtures.js — DEV ONLY sample data for ?preview=1.
const psd = (n, base) => Array.from({length:n}, (_,i) => base + 8*Math.sin(i/4) - (i%7===0?18:0) + (i%13)*0.7);
export const FIXTURES = {
  config: { webrtcBase:'', readUser:'read', readPass:'x' },
  operator: 'operator_042',
  devices: [
    { id:'cam-north', name:'Вхідні ворота', location:'Периметр — Північ', kind:'camera', online:true, bitrateKbps:2100, uptimeSec:5400, readers:2 },
    { id:'cam-yard', name:'Двір', location:'Периметр — Схід', kind:'camera', online:false },
    { id:'bladerf', name:'Сканер bladeRF', location:'Дах', kind:'scanner', online:true, uptimeSec:9000 },
  ],
  detections: [
    { ts:1751900000, scanner_id:'bladerf', band:'5.8G', center_mhz:5800, channel:'F4', class:'analog', snr_db:18, power_dbm:-42, event:'appeared' },
    { ts:1751899000, scanner_id:'bladerf', band:'1.2G', center_mhz:1280, class:'digital', snr_db:12, power_dbm:-55, event:'gone' },
  ],
  frames: { frames: [
    { id:'bladerf/1751900000_5800', scanner_id:'bladerf', ts:1751900000, center_mhz:5800, standard:'PAL', line_hz:15625, sync_snr_db:18.3, url:'' },
  ] },
  scanStore: { bladerf: {
    online:true, status_ts:1751900000,
    bands:{ '5.8G':{low_mhz:5645,high_mhz:5945}, '1.2G':{low_mhz:1080,high_mhz:1360}, '900M':{low_mhz:840,high_mhz:960} },
    latestPsd:{ '5.8G':psd(64,-70), '1.2G':psd(64,-80), '900M':psd(64,-75) }, waterfalls:{ '5.8G':[], '1.2G':[], '900M':[] },
    detection:{ ts:1751900000, occupancy:{'5.8G':0.32,'1.2G':0.08,'900M':0.5}, detections:[
      { band:'5.8G',center_mhz:5800,class:'analog',power_dbm:-42,bandwidth_mhz:18,confidence:0.9,channel:'F4',snr_db:18 },
      { band:'900M',center_mhz:915,class:'digital',power_dbm:-55,bandwidth_mhz:10,confidence:0.7 } ] },
    video:{ ts:1751900000, center_mhz:5800, standard:'PAL', line_hz:15625, sync_snr_db:18.3, frame_png_b64:'' },
    rxtune:{ ts:1751900000, freq_mhz:5865, channel:'A1', mode:'scan', targets:[] },
    view:{ ts:1751900000, active:true, freq_mhz:5800, until_ts:1751900600, error:null, stream:'bladerf-view' },
  } },
};
```

- [ ] **Step 5: Write `app.js` bootstrap**
Write a thin bootstrap that: builds `players` Map + `viewerState = emptyViewer()` (from `/viewer.js`); defines the `ctx` (accessors + handlers); the **camera player lifecycle** (reuse-aware reconcile: `startTile` idempotent, `closeTile`, `restartTile` real teardown, prune removed) — copy the semantics from v1 `feat/tactical-ui-redesign:dashboard/public/app.js` (retrieve it for reference: `git show feat/tactical-ui-redesign:dashboard/public/app.js`); the **viewer generation-token player** — relocate `syncViewerPlayer`/`startViewerWhep`/`viewerRetry`/`viewerPlayer`/`viewerStreamKey` from main's app.js VERBATIM in behavior, exposed as `ctx.handlers.syncViewerPlayer()` (called by the viewer view after it mounts `#viewer-video` into the DOM); fold detections via `applyDetections` on each tick (ts-idempotent) and seed via `seedFromJournal` on init; wire SSE (`connectSSE` with reconnect), MQTT (`scanClient.connect(mq, onData)`), the 30 s viewer ticker, sound toggle (gesture-arm), tile-size, add-device, restart-all, logout; the `?preview` seam (load `/fixtures.js`, seed store + viewerState, set `window.__rerender`, skip network/WHEP). Provide `onScanCmd`/`onViewStart`/`onViewStop` → `scanClient.publishCommand`/`publishView` (no-op in preview). Screens: `render(container, ctx)` dispatched by the router.
Publish signatures: `ctx.onViewStart(id,freq)` → `scanClient.publishView(id,'start',freq)`; `ctx.onViewStop(id)` → `scanClient.publishView(id,'stop')`; `ctx.onScanCmd(id,cmd)` → `scanClient.publishCommand(id,cmd)`.
Routes: `[{hash:'#/dashboard',label:'Панель',icon:'▤',section:'screen-dashboard',mount:renderDashboard},{hash:'#/viewer',label:'FPV Viewer',icon:'🎯',section:'screen-viewer',mount:renderViewer},{hash:'#/nodes',label:'Вузли',icon:'▦',section:'screen-nodes',mount:renderNodes},{hash:'#/detections',label:'Детекції',icon:'≣',section:'screen-detections',mount:renderDetections},{hash:'#/frames',label:'Кадри',icon:'🖼️',section:'screen-frames',mount:renderFrames}]`.
`size-ctl` visible only on `#/dashboard`. This task's app.js may be large; if a piece is unclear, report DONE_WITH_CONCERNS rather than guessing the viewer gen-token logic.

- [ ] **Step 6: Write 5 stub views** — each `export function render(container,ctx){ container.className='screen screen-pad'; container.innerHTML='<h2 class="section-title">TITLE</h2>'; }` (titles: Панель/FPV Viewer/Вузли/Детекції/Кадри). Replaced in Tasks 4–8.

- [ ] **Step 7: Verify** — implementer runs `node --check` on every JS file created/modified. Controller screenshots `index.html?preview=1`, checks nav across all 5 screens + console errors.

- [ ] **Step 8: Commit**
```bash
git add dashboard/public/index.html dashboard/public/modals.js dashboard/public/fixtures.js dashboard/public/app.js dashboard/public/views/
git commit -m "feat(ui): v2 shell (sidebar+5 screens), modals, fixtures, bootstrap w/ camera+viewer players"
```

---

### Task 3: spectrum.js trim → mini-spectrum (keep pure + view helpers)

**Files:** Modify `dashboard/public/spectrum.js`.

**Interfaces:** keep all current exported pure helpers unchanged (`splitByKind, classColor, fmtFreq, fmtPct, frameCaption, rxtuneCaption, viewCaption, psdToPoints, detectionX, psdColor`). Remove the DOM block renderer (`renderSpectrum, scannerBlock, rx5808Controls, viewControls, bandCell, detectionTable`, local `el`). Add `renderMiniSpectrum`.

- [ ] **Step 1: Read current spectrum.js**, find where the pure helpers end and the `// ---- DOM rendering` section begins.

- [ ] **Step 2: Remove the DOM section, append `renderMiniSpectrum`:**
```js
// ---- mini live spectrum (PSD line + marks only; NO waterfall) ----
export function renderMiniSpectrum(canvas, { psd = [], range = {}, dets = [], rxFreq = null, tunable = false }) {
  const w = canvas.width, h = canvas.height, c = canvas.getContext('2d');
  c.clearRect(0, 0, w, h);
  const pts = psdToPoints(psd, w, h);
  if (pts.length) { c.strokeStyle = '#00e5ff'; c.lineWidth = 1; c.beginPath();
    pts.forEach((p, i) => (i ? c.lineTo(p.x, p.y) : c.moveTo(p.x, p.y))); c.stroke(); }
  for (const d of dets) { const x = detectionX(d.center_mhz, range.low_mhz, range.high_mhz, w);
    c.strokeStyle = classColor(d.class); c.lineWidth = 2; c.beginPath(); c.moveTo(x,0); c.lineTo(x,h); c.stroke(); }
  if (rxFreq != null && range.low_mhz != null && rxFreq >= range.low_mhz && rxFreq <= range.high_mhz) {
    const x = detectionX(rxFreq, range.low_mhz, range.high_mhz, w);
    c.strokeStyle = '#39d0ff'; c.lineWidth = 2; c.beginPath(); c.moveTo(x,0); c.lineTo(x,h); c.stroke(); }
  if (tunable) canvas.classList.add('tunable');
}
```
If any removed function was imported by a non-view module, STOP and report — nothing outside the new views should import the DOM renderer.

- [ ] **Step 3: Verify** — `npm test` MUST pass (report counts); `node --check dashboard/public/spectrum.js`. No Chrome.

- [ ] **Step 4: Commit** — `git add dashboard/public/spectrum.js && git commit -m "feat(ui): spectrum.js — drop waterfall DOM, add renderMiniSpectrum"`

---

### Task 4: Dashboard view (feeds + node strip + detection count)

**Files:** Rewrite `dashboard/public/views/dashboard.js`.

**Interfaces:** `render(container, ctx)` building `.dash` (reused skeleton, reconcile tiles by id, never `innerHTML=''` a live-video container); node strip via `cornerCard`+`occupancyStrip`+`tempSlot`; a compact active-detections count.

- [ ] **Step 1: Write it** — retrieve v1's dashboard view as the base (it already does reconcile + node strip): `git show feat/tactical-ui-redesign:dashboard/public/views/dashboard.js`. Adapt: the "active detections" right column may be trimmed to a compact count/summary here (the full merged list lives on the FPV Viewer screen); keep the reconcile-tiles logic and the node strip verbatim. Do NOT render `d.telemetry`.

- [ ] **Step 2: Verify** — `node --check`. Controller screenshots `#/dashboard` + a `window.__rerender` reuse check (same `<video>` node).

- [ ] **Step 3: Commit** — `git commit -m "feat(ui): v2 dashboard screen — feeds + node telemetry strip"`

---

### Task 5: FPV Viewer view (merged list + shared player + mini-spectrum + view controls)

**Files:** Rewrite `dashboard/public/views/viewer.js` (the VIEW; not the `viewer.js` state module).

**Interfaces:** `render(container, ctx)` building `.viewer` (list + stage). Uses `viewer.js` exports (`viewerRows`, `viewerListHtml` or build rows from `viewerRows`, `activeViewer`, `pickViewer`, `viewStream`) and `renderMiniSpectrum`. Mounts `#viewer-video` into `.viewer-stage` then calls `ctx.handlers.syncViewerPlayer()` so app.js's gen-token player attaches.

- [ ] **Step 1: Write the view**
Build:
- Left `.viewer-list`: rows from `viewerRows(ctx.viewerState(), nowS, ctx.scanStore())` (or reuse `viewerListHtml` re-themed). Each row: `.viewer-row` (class by `class`, `.recent` when not live, `.is-viewing` when its freq matches the active view ±3 MHz), `data-vwfreq`/`data-vwband`/`data-sid`. Click → `ctx.handlers.viewerRowClick(freq, band, scannerId)` which (in app.js) publishes `view start` to `pickViewer(store)` and, on 5.8G, RX5808 nearest-tune (dual action).
- Right `.viewer-stage`: `<video id="viewer-video" autoplay playsinline muted>`, a `.view-controls` row (freq input + ▶ дивитись + ■ свіп + `#viewer-badge` + `#viewer-err`), and a `<canvas class="mini-spectrum">` for the active scanner+band via `renderMiniSpectrum`, with canvas freq-pick (fill input; on 5.8G RX5808 nearest-tune) and a latest recovered-frame thumbnail → `ctx.handlers.openImage`.
- After building, call `ctx.handlers.syncViewerPlayer()` (app.js binds the gen-token player to `#viewer-video`).
Preserve: `■ свіп` calls `ctx.onViewStop(activeViewerId)`; ▶ calls `ctx.onViewStart(pickViewerId, freq)`; the stop button hidden unless a view is active; badge shows `viewCaption(view)`.

- [ ] **Step 2: Verify** — `node --check`. Controller screenshots `#/viewer` (fixture has an active view: list shows the 5800 analog row `is-viewing`, stage shows the player slot + mini-spectrum + badge). Console clean. A `window.__rerender` check: `#viewer-video` node persists across re-render (no player churn).

- [ ] **Step 3: Commit** — `git commit -m "feat(ui): v2 FPV Viewer screen — merged list + shared gen-token player + mini-spectrum"`

---

### Task 6: Nodes view (device cards + RX5808 + per-scanner view controls + CRUD)

**Files:** Rewrite `dashboard/public/views/nodes.js`.

**Interfaces:** `render(container, ctx)`; per-device `.node-card`; scanners get `occupancyStrip`, RX5808 mode buttons + channel `<select>` (→ `ctx.onScanCmd`), and SDR view controls (freq input + ▶ → `ctx.onViewStart`, ■ → `ctx.onViewStop`). Actions Edit/Creds-or-Info/Restart/Delete via `ctx.handlers`.

- [ ] **Step 1: Write it** — base on v1's `views/nodes.js` (`git show feat/tactical-ui-redesign:dashboard/public/views/nodes.js`): keep card layout + RX5808 controls; ADD a per-scanner SDR-view control row (freq input + ▶/■ wired to `ctx.onViewStart(id,freq)`/`ctx.onViewStop(id)`). Import `RX5808_CHANNELS` from `/rx5808-channels.js`. Do NOT render `d.telemetry`.

- [ ] **Step 2: Verify** — `node --check`. Controller screenshots `#/nodes`; confirm RX5808 buttons + view controls render; open an edit modal.

- [ ] **Step 3: Commit** — `git commit -m "feat(ui): v2 nodes screen — cards, RX5808 + SDR view controls, CRUD"`

---

### Task 7: Detections view (journal history table)

**Files:** Rewrite `dashboard/public/views/detections.js`.

**Interfaces:** `render(container, ctx)`: history `.data-table` from `ctx.getDetections()` (cached; refresh button; no refetch on tick) — reuse v1's logs history-table + cache pattern (`git show feat/tactical-ui-redesign:dashboard/public/views/logs.js` — take only the history-table half; drop the spectrum/frames side which now live on Viewer/Frames screens).

- [ ] **Step 1: Write it** (history table + «оновити» refresh + module-level cache; escape `з'явився` with a single backslash).
- [ ] **Step 2: Verify** — `node --check`. Controller screenshots `#/detections` (3 fixture rows, class-colored, event labels).
- [ ] **Step 3: Commit** — `git commit -m "feat(ui): v2 detections screen — journal history table"`

---

### Task 8: Frames gallery view (filter toolbar + pagination)

**Files:** Rewrite `dashboard/public/views/frames.js`.

**Interfaces:** `render(container, ctx)` building `.frames-toolbar` + `.frames-grid2` + «Показати ще». Reuse `frames-gallery.js` logic: `import { BAND_PRESETS, buildFramesQuery, scannerOptions, toLocalDatetime, scannerIds } from '/frames-gallery.js'`. Fetch via `ctx.fetchFrames(query)`; append on «Показати ще» with `before` = oldest-shown `ts`; tiles → `ctx.handlers.openImage(frame.url, caption)`.

- [ ] **Step 1: Read `frames-gallery.js`** to learn `buildFramesQuery(filters)` input shape + `BAND_PRESETS` + `scannerOptions(ids, sel)` + the frame record fields (`{id,scanner_id,ts,center_mhz,standard,sync_snr_db,url}`).

- [ ] **Step 2: Write the view** — a themed toolbar (scanner `<select>` from `scannerOptions`, band-preset `<select>` from `BAND_PRESETS`, standard, SNR-min, a time-range preset + optional datetime inputs via `toLocalDatetime`), a «Застосувати» that builds `buildFramesQuery(filters)` and calls `ctx.fetchFrames(query)` (replacing the grid), a `.frames-grid2` of `<figure><img><figcaption>` (caption via `frameCaption` from `/spectrum.js`), and «Показати ще» that fetches the next page with `before=<oldest ts>` and appends. Click a figure → `ctx.handlers.openImage`.
`ctx.fetchFrames(query)`: in prod `fetch('/api/frames?'+query).then(r=>r.json())`; in preview returns `FIXTURES.frames`.

- [ ] **Step 3: Verify** — `node --check`. Controller screenshots `#/frames` (toolbar + 1 fixture frame or empty state). Note: fixture frame has empty `url` → the `<img>` is blank; verify the toolbar + grid layout render and no console error.

- [ ] **Step 4: Commit** — `git commit -m "feat(ui): v2 frames gallery screen — filter toolbar + pagination"`

---

### Task 9: Login re-theme + integration cleanup + acceptance

**Files:** `dashboard/public/login.html`; cleanup any dead refs.

- [ ] **Step 1: Re-theme login** — `git show feat/tactical-ui-redesign:dashboard/public/login.html > dashboard/public/login.html` (verify it posts `POST /login` with `user`/`pass` and honors `?error=1` — matches the server).

- [ ] **Step 2: Dead-reference sweep**
```bash
grep -rn "spectrum-panel\|viewer-panel\|renderSpectrum\|renderViewer\|scan-block\|chart-wf\|waterfall\|d.telemetry\|telemetryLine" dashboard/public || echo clean
```
Legit remaining: `waterfalls` (store field in `mqtt-scan.js`), comments. Remove any real dead references in `app.js`/views/`index.html` (e.g. leftover `telemetryLine`). Report the final grep.

- [ ] **Step 3: Syntax + tests**
```bash
for f in dashboard/public/app.js dashboard/public/router.js dashboard/public/modals.js dashboard/public/spectrum.js dashboard/public/views/*.js; do node --check "$f" && echo "ok $f"; done
npm test
```
Both must pass (report `npm test` counts).

- [ ] **Step 4: Commit** — `git add -A dashboard/public && git commit -m "feat(ui): v2 re-theme login + integration cleanup" --allow-empty`

- [ ] **Step 5: Live acceptance (operator/controller, over WireGuard)** — after deploy from `main` (once merged): login themed; camera WHEP plays; **FPV Viewer scan→click→watch** works (merged list, shared player, sweep stop, retune smoothness); RX5808 mode/channel/click-tune; SDR view start/stop; detection journal loads; frames gallery filters + «Показати ще» work; sound alert beeps when armed. Document any live-only issues (front-end wiring only — data plumbing unchanged).

---

## Self-Review (author check vs spec)

**Spec coverage:** sidebar+5 screens (Tasks 2,4–8) ✓; camera feeds + node TEMP strip (Task 4) ✓; FPV Viewer merged list + gen-token player + mini-spectrum + view controls + dual-action (Tasks 2,5) ✓; Nodes RX5808 + per-scanner view controls + CRUD (Task 6) ✓; detection journal (Task 7) ✓; frames gallery filters+pagination (Task 8) ✓; waterfalls removed / mini-spectrum kept (Task 3) ✓; theme+fonts (Task 1) ✓; login (Task 9) ✓; `telemetry` dropped (Tasks 4,6,9) ✓; `npm test` green (Tasks 3,9) ✓; contracts preserved (Task 2 relocates main's player/viewer logic verbatim; Task 9 sweep) ✓.

**Placeholder scan:** no TBD/TODO. Node TEMP `—°C` is an intended product placeholder. Fixture frame `url:''` is intentional (blank preview thumbnail).

**Type consistency:** `ctx` accessors/handlers identical across tasks; `render(container,ctx)` uniform; `renderMiniSpectrum(canvas,opts)` defined Task 3, consumed Tasks 5. viewer.js/frames-gallery.js/rx5808-channels.js exports referenced match the map.

**Risk note for executor:** Tasks 2 and 5 carry the real risk (relocating the viewer generation-token player without breaking the `viewerRetry!==retry` discipline, `playerKey` stream-keying, `pickViewer`≠`activeViewer`, non-retained view commands, ts-idempotent folding, 30 s ticker). Preserve main's logic verbatim in behavior; the preview seam cannot exercise live WHEP, so the controller must live-accept the Viewer over WG before merge.
