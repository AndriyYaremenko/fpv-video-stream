# Dashboard Consumption of `fpv/<id>/video` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Subscribe to `fpv/<id>/video`, reduce the latest recovered frame into the scan store, render a thumbnail + caption in each scanner block, and open it larger in the existing modal on click.

**Architecture:** Pure reducer change in `mqtt-scan.js` (+ subscribe), a pure `frameCaption()` helper + DOM section in `spectrum.js`, an image branch of the existing `#modal` in `index.html`/`app.js`, and styles. Pure parts are unit-tested with `node --test`; browser DOM is validated with `node --check`.

**Tech Stack:** Plain ES modules (no build), `node --test` / `node --check`. Run from repo root.

**Reference spec:** `docs/superpowers/specs/2026-06-18-dashboard-video-frame-design.md`

---

### Task 1: Reduce + subscribe to the `video` topic

**Files:**
- Modify: `dashboard/public/mqtt-scan.js`
- Test: `test/mqtt-scan.test.js`

- [ ] **Step 1: Write the failing test** — append to `test/mqtt-scan.test.js`:

```javascript
test('reduce stores the latest video frame', () => {
  const s = reduce(emptyStore(), 'fpv/hackrf/video', JSON.stringify({
    ts: 1718700000, center_mhz: 5800, standard: 'PAL', line_hz: 15625,
    sync_snr_db: 18.3, frame_png_b64: 'QUJD',
  }));
  assert.equal(s.hackrf.video.standard, 'PAL');
  assert.equal(s.hackrf.video.center_mhz, 5800);
  assert.equal(s.hackrf.video.line_hz, 15625);
  assert.equal(s.hackrf.video.sync_snr_db, 18.3);
  assert.equal(s.hackrf.video.frame_png_b64, 'QUJD');
});

test('reduce video defaults frame to empty string when missing', () => {
  const s = reduce(emptyStore(), 'fpv/hackrf/video', JSON.stringify({ ts: 1, standard: 'NTSC' }));
  assert.equal(s.hackrf.video.frame_png_b64, '');
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `npm test`
Expected: FAIL (`s.hackrf.video` is undefined — the video topic is filtered out by the regex).

- [ ] **Step 3: Implement** — three edits in `dashboard/public/mqtt-scan.js`.

(a) In `ensure`, add `video: null` to the initial object:

```javascript
    store[id] = { online: false, status_ts: 0, detection: null, video: null, bands: {}, latestPsd: {}, waterfalls: {} };
```

(b) In `reduce`, widen the topic regex:

```javascript
  const m = /^fpv\/([^/]+)\/(spectrum|detection|status|video)$/.exec(topic || '');
```

(c) In `reduce`, add a `video` branch after the `detection` branch:

```javascript
  } else if (kind === 'video') {
    s.video = {
      ts: data.ts || 0,
      center_mhz: data.center_mhz,
      standard: data.standard,
      line_hz: data.line_hz,
      sync_snr_db: data.sync_snr_db,
      frame_png_b64: data.frame_png_b64 || '',
    };
```

(d) In `MqttScanClient.connect`, add the video topic to the subscribe list:

```javascript
    client.on('connect', () => client.subscribe(['fpv/+/spectrum', 'fpv/+/detection', 'fpv/+/status', 'fpv/+/video']));
```

- [ ] **Step 4: Run it to confirm it passes**

Run: `npm test`
Expected: PASS (the whole suite green, including the 2 new tests).

- [ ] **Step 5: Validate the browser module parses**

Run: `node --check dashboard/public/mqtt-scan.js`
Expected: no output (exit 0).

- [ ] **Step 6: Commit**

```bash
git add dashboard/public/mqtt-scan.js test/mqtt-scan.test.js
git commit -m "feat(dashboard): reduce + subscribe fpv/<id>/video (latest frame)"
```

---

### Task 2: `frameCaption()` helper + frame section in `scannerBlock`

**Files:**
- Modify: `dashboard/public/spectrum.js`
- Test: `test/spectrum.test.js`

- [ ] **Step 1: Write the failing test** — append to `test/spectrum.test.js`:

```javascript
import { frameCaption } from '../dashboard/public/spectrum.js';

test('frameCaption combines standard, freq, snr, time', () => {
  const cap = frameCaption({ standard: 'PAL', center_mhz: 5800, sync_snr_db: 18.3, ts: 1718700000 });
  assert.match(cap, /PAL/);
  assert.match(cap, /5800/);
  assert.match(cap, /18\.3/);
  assert.match(cap, /\d{2}:\d{2}:\d{2}/);     // HH:MM:SS, tz-independent shape
});

test('frameCaption tolerates missing snr and ts', () => {
  const cap = frameCaption({ standard: 'NTSC', center_mhz: 1200 });
  assert.match(cap, /NTSC/);
  assert.match(cap, /1200/);
  assert.doesNotMatch(cap, /SNR/);
});

test('frameCaption is empty for nullish input', () => {
  assert.equal(frameCaption(null), '');
});
```

(If `test/spectrum.test.js` already imports from `spectrum.js`, add `frameCaption` to that existing
import line instead of adding a second import.)

- [ ] **Step 2: Run it to confirm it fails**

Run: `npm test`
Expected: FAIL (`frameCaption` is not exported).

- [ ] **Step 3: Implement** — two edits in `dashboard/public/spectrum.js`.

(a) Add the exported helper (near `fmtFreq`):

```javascript
export function frameCaption(v) {
  if (!v) return '';
  const parts = [];
  if (v.standard) parts.push(String(v.standard));
  if (v.center_mhz != null) parts.push(fmtFreq(v.center_mhz));
  if (v.sync_snr_db != null) parts.push(`SNR ${Number(v.sync_snr_db).toFixed(1)} dB`);
  if (v.ts) {
    const d = new Date(Number(v.ts) * 1000);
    const p = (n) => String(n).padStart(2, '0');
    parts.push(`${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`);
  }
  return parts.join(' · ');
}
```

(b) In `scannerBlock`, right after `block.appendChild(occ);`, insert the frame section:

```javascript
  if (live && live.video && live.video.frame_png_b64) {
    const fw = el('div', 'scan-frame-wrap');
    const img = el('img', 'scan-frame');
    img.alt = 'recovered frame';
    img.src = `data:image/png;base64,${live.video.frame_png_b64}`;
    fw.appendChild(img);
    fw.appendChild(el('div', 'scan-frame-cap', escapeHtml(frameCaption(live.video))));
    block.appendChild(fw);
  }
```

- [ ] **Step 4: Run it to confirm it passes**

Run: `npm test`
Expected: PASS (whole suite green).

- [ ] **Step 5: Validate the browser module parses**

Run: `node --check dashboard/public/spectrum.js`
Expected: no output (exit 0).

- [ ] **Step 6: Commit**

```bash
git add dashboard/public/spectrum.js test/spectrum.test.js
git commit -m "feat(dashboard): frameCaption + recovered-frame thumbnail in scanner block"
```

---

### Task 3: Modal image + click-to-enlarge + topic doc

**Files:**
- Modify: `dashboard/public/index.html`
- Modify: `dashboard/public/app.js`

- [ ] **Step 1: Add the modal image element** — in `dashboard/public/index.html`, inside `#modal`, after the `<video id="modal-video" ...>` line, add:

```html
    <img id="modal-image" class="modal-image hidden" alt="recovered frame" />
```

So the modal block reads:
```html
  <div id="modal" class="modal hidden">
    <button id="modal-close" class="modal-close">✕</button>
    <video id="modal-video" autoplay playsinline muted></video>
    <img id="modal-image" class="modal-image hidden" alt="recovered frame" />
    <div id="modal-caption" class="modal-caption"></div>
```

- [ ] **Step 2: Add the frame-click branch** — in `dashboard/public/app.js`, replace the start of the `spectrumPanel` click listener:

```javascript
spectrumPanel.addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-act]');
  if (!btn) return;
```

with:

```javascript
spectrumPanel.addEventListener('click', (e) => {
  const frame = e.target.closest('.scan-frame');
  if (frame) {
    const cap = frame.parentElement.querySelector('.scan-frame-cap');
    openImageModal(frame.src, cap ? cap.textContent : '');
    return;
  }
  const btn = e.target.closest('button[data-act]');
  if (!btn) return;
```

- [ ] **Step 3: Add `openImageModal` and make `openModal` reset the image** — in `dashboard/public/app.js`.

Add this function right after `openModal` (it shares `modalPlayer`):

```javascript
function openImageModal(src, caption) {
  const modal = document.getElementById('modal');
  const video = document.getElementById('modal-video');
  const img = document.getElementById('modal-image');
  if (modalPlayer) { modalPlayer.close(); modalPlayer = null; }
  video.classList.add('hidden');
  img.src = src;
  img.classList.remove('hidden');
  document.getElementById('modal-caption').textContent = caption || '';
  modal.classList.remove('hidden');
  const close = () => {
    img.classList.add('hidden');
    img.src = '';
    video.classList.remove('hidden');
    modal.classList.add('hidden');
  };
  document.getElementById('modal-close').onclick = close;
}
```

And in `openModal`, right after `const video = document.getElementById('modal-video');`, reset the
image so a camera open never shows a stale frame:

```javascript
  document.getElementById('modal-image').classList.add('hidden');
  video.classList.remove('hidden');
```

- [ ] **Step 4: Update the scanner topic doc** — in `dashboard/public/app.js`, in `scannerInfoModal`, change:

```javascript
    ${credRow('MQTT-топіки', `fpv/${device.id}/{spectrum,detection,status}`)}
```

to:

```javascript
    ${credRow('MQTT-топіки', `fpv/${device.id}/{spectrum,detection,status,video}`)}
```

- [ ] **Step 5: Validate the browser module parses**

Run: `node --check dashboard/public/app.js`
Expected: no output (exit 0).

- [ ] **Step 6: Run the full test suite (no regressions)**

Run: `npm test`
Expected: PASS (unchanged — these are DOM-only edits).

- [ ] **Step 7: Commit**

```bash
git add dashboard/public/index.html dashboard/public/app.js
git commit -m "feat(dashboard): click recovered frame -> enlarge in modal; topic doc +video"
```

---

### Task 4: Styles for the frame thumbnail + modal image

**Files:**
- Modify: `dashboard/public/styles.css`

- [ ] **Step 1: Add the styles** — append to `dashboard/public/styles.css`:

```css
.scan-frame-wrap { margin:.5rem 0; }
.scan-frame { max-width:320px; width:100%; border:1px solid var(--line); border-radius:4px; cursor:pointer; display:block; }
.scan-frame-cap { color:#9aa4b2; font-size:.8rem; margin-top:.25rem; }
.modal-image { max-width:92vw; max-height:82vh; background:#000; image-rendering:pixelated; }
.modal-image.hidden { display:none; }
.modal video.hidden { display:none; }
```

- [ ] **Step 2: Sanity-check the scan panel still renders** (no test harness for CSS)

Run: `node --check dashboard/public/spectrum.js && node --check dashboard/public/app.js`
Expected: no output (exit 0) — confirms the JS that references these classes still parses.

- [ ] **Step 3: Commit**

```bash
git add dashboard/public/styles.css
git commit -m "style(dashboard): recovered-frame thumbnail + modal image"
```

---

## Self-Review notes (for the implementer)

- **Spec coverage:** Task 1 = reducer + subscribe (§4.1); Task 2 = `frameCaption` + render (§4.2);
  Task 3 = modal image + click + topic doc (§4.3, §4.4); Task 4 = styles (§4.5). Testing per §6.
- **Commit trailers:** append the repo convention to every commit message:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01Fr3LCjweDyLf1WRPz9PNUX`.
- **Caveat:** only the ≤320px thumbnail is transmitted; the modal upscales it. The caption metadata
  is the value-add (§2). A full-res endpoint is out of scope.
- **Manual check after deploy:** retained `fpv/<id>/video` → thumbnail+caption in the scanner block,
  click opens the modal, spectrum/detection/camera views unaffected.
