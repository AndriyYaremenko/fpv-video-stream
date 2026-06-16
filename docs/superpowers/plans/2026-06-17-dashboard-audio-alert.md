# Dashboard Audio Alert Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Beep + visually highlight on the dashboard when a new possible-video transmitter (any scanner detection) appears.

**Architecture:** A new `dashboard/public/alert.js` holds pure detection-key/diff helpers (unit-tested) plus a `SoundAlerter` (Web Audio oscillator). `app.js` diffs each SSE snapshot's detections against the previous one; new keys trigger a beep (when armed via a top-bar 🔔 toggle) and are passed to `spectrum.js` to highlight the new rows. No server/scanner change.

**Tech Stack:** Vanilla JS / Web Audio API frontend, `node --test` + `node:assert/strict`, `node --check` for DOM files. Tests via `npm test`.

Spec: `docs/superpowers/specs/2026-06-17-dashboard-audio-alert-design.md`

---

## File Structure

```
dashboard/public/alert.js     (new: detectionKey, diffNewKeys [pure]; SoundAlerter [audio])
dashboard/public/spectrum.js  (change: renderSpectrum highlightKeys param + is-new rows; import detectionKey)
dashboard/public/app.js       (change: 🔔 toggle, prevScanKeys diff, beep + highlight wiring)
dashboard/public/index.html   (change: 🔔 button in top bar)
dashboard/public/styles.css   (change: 🔔 states + .scan-table tr.is-new)
test/alert.test.js            (new: pure-helper tests)
README.md                     (note: 🔔 audio-alert toggle)
```

Pure key logic lives in `alert.js` and is imported by both `app.js` (beep trigger) and `spectrum.js` (row highlight) — one source of truth. `node --test` can import `alert.js` because nothing references the DOM at module load (AudioContext is created lazily in `SoundAlerter.arm()`).

---

## Task 1: `alert.js` pure helpers

**Files:**
- Create: `dashboard/public/alert.js` (pure helpers only this task)
- Test: `test/alert.test.js`

- [ ] **Step 1: Write the failing tests**

Create `test/alert.test.js`:

```javascript
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { detectionKey, diffNewKeys } from '../dashboard/public/alert.js';

test('detectionKey uses channel when present', () => {
  assert.equal(detectionKey({ band: '5.8G', channel: 'F4', center_mhz: 5801 }), '5.8G:F4');
});

test('detectionKey buckets center_mhz to 5 MHz when no channel (ignores jitter)', () => {
  assert.equal(detectionKey({ band: '5.8G', center_mhz: 5734 }), '5.8G:5735');
  assert.equal(detectionKey({ band: '5.8G', center_mhz: 5736 }), '5.8G:5735');
});

test('diffNewKeys baseline (null prev) yields no newKeys', () => {
  const { keys, newKeys } = diffNewKeys(null, [{ band: '5.8G', channel: 'F4' }]);
  assert.deepEqual(newKeys, []);
  assert.ok(keys.has('5.8G:F4'));
});

test('diffNewKeys flags a genuinely new key', () => {
  const prev = new Set(['5.8G:F4']);
  const { newKeys } = diffNewKeys(prev, [{ band: '5.8G', channel: 'F4' }, { band: '2.4G', channel: 'G3' }]);
  assert.deepEqual(newKeys, ['2.4G:G3']);
});

test('diffNewKeys: persisting key is not new, removed key ignored', () => {
  const prev = new Set(['5.8G:F4', '2.4G:G3']);
  const { keys, newKeys } = diffNewKeys(prev, [{ band: '5.8G', channel: 'F4' }]);
  assert.deepEqual(newKeys, []);
  assert.ok(keys.has('5.8G:F4') && !keys.has('2.4G:G3'));
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm test`
Expected: FAIL — `Cannot find module '../dashboard/public/alert.js'`.

- [ ] **Step 3: Implement**

Create `dashboard/public/alert.js`:

```javascript
// dashboard/public/alert.js — detection-alert helpers (pure) + Web Audio beep (browser only).

// Stable key for a detection so the same transmitter doesn't re-alert on small freq jitter.
export function detectionKey(d) {
  const band = d.band || '?';
  if (d.channel) return `${band}:${d.channel}`;
  const mhz = Math.round(Number(d.center_mhz) / 5) * 5;
  return `${band}:${mhz}`;
}

// Compare current detections to the previous key set.
// prevKeys null/undefined => baseline (no "new"); else newKeys = keys present now but not before.
export function diffNewKeys(prevKeys, detections) {
  const keys = new Set((detections || []).map(detectionKey));
  if (prevKeys === null || prevKeys === undefined) return { keys, newKeys: [] };
  const newKeys = [...keys].filter((k) => !prevKeys.has(k));
  return { keys, newKeys };
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm test`
Expected: PASS — 5 new alert tests green, rest of suite unaffected.

- [ ] **Step 5: Commit**

```bash
git add dashboard/public/alert.js test/alert.test.js
git commit -m "feat(dashboard): alert.js detection-key + diff helpers" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: `SoundAlerter` (Web Audio beep)

**Files:**
- Modify: `dashboard/public/alert.js` (append; browser-only, not unit-tested)

- [ ] **Step 1: Append the SoundAlerter class**

Append to `dashboard/public/alert.js`:

```javascript
// ---- Web Audio beep (browser only; not unit-tested) ----
export class SoundAlerter {
  constructor() {
    this._ctx = null;
    this._armed = false;
  }

  get armed() {
    return this._armed;
  }

  // Must be called from a user gesture (e.g. the 🔔 click) to satisfy browser autoplay policy.
  arm() {
    if (!this._ctx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      this._ctx = new AC();
    }
    if (this._ctx.state === 'suspended') this._ctx.resume();
    this._armed = true;
  }

  disarm() {
    this._armed = false;
  }

  beep(freq = 880, ms = 180) {
    if (!this._armed || !this._ctx) return;
    const ctx = this._ctx;
    const t = ctx.currentTime;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'sine';
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0.0001, t);
    gain.gain.exponentialRampToValueAtTime(0.3, t + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + ms / 1000);
    osc.connect(gain).connect(ctx.destination);
    osc.start(t);
    osc.stop(t + ms / 1000 + 0.02);
  }
}
```

- [ ] **Step 2: Verify parse + suite**

Run: `node --check dashboard/public/alert.js`
Expected: no output (valid).

Run: `npm test`
Expected: PASS — Task 1's pure-helper tests still import and pass (SoundAlerter touches `window` only inside methods, so importing the module stays DOM-free).

- [ ] **Step 3: Commit**

```bash
git add dashboard/public/alert.js
git commit -m "feat(dashboard): SoundAlerter Web Audio beep" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Highlight new rows in `spectrum.js`

**Files:**
- Modify: `dashboard/public/spectrum.js` (import `detectionKey`; thread `highlightKeys` into the table; mark `is-new` rows)

- [ ] **Step 1: Add the import**

At the top of `dashboard/public/spectrum.js`, add (after the existing first line / `BAND_RANGES` export — place it as the first import line at the very top of the file):

```javascript
import { detectionKey } from '/alert.js';
```

- [ ] **Step 2: Thread `highlightKeys` through render**

Replace the `renderSpectrum` function signature/body and `scannerBlock`'s table call. Change:

```javascript
export function renderSpectrum(container, scanners) {
  container.innerHTML = '';
  for (const s of scanners) container.appendChild(scannerBlock(s));
}
```

to:

```javascript
export function renderSpectrum(container, scanners, highlightKeys = new Set()) {
  container.innerHTML = '';
  for (const s of scanners) container.appendChild(scannerBlock(s, highlightKeys));
}
```

In `scannerBlock`, change its signature `function scannerBlock(s) {` to `function scannerBlock(s, highlightKeys) {`, and change the detection-table line from:

```javascript
  block.appendChild(detectionTable(tel.detections || []));
```

to:

```javascript
  block.appendChild(detectionTable(tel.detections || [], highlightKeys));
```

- [ ] **Step 3: Mark new rows in `detectionTable`**

Replace the whole `detectionTable` function with:

```javascript
function detectionTable(dets, highlightKeys = new Set()) {
  if (!dets.length) return el('p', 'scan-empty', 'немає активних передавачів');
  const sorted = [...dets].sort((a, b) => (b.power_dbm ?? -999) - (a.power_dbm ?? -999));
  const table = el('table', 'scan-table',
    '<thead><tr><th></th><th>Бенд</th><th>Частота</th><th>Клас</th><th>RSSI</th><th>Смуга</th><th>Впевн.</th></tr></thead>');
  const tb = el('tbody');
  for (const d of sorted) {
    const isNew = highlightKeys.has(detectionKey(d));
    const tr = el('tr', isNew ? 'is-new' : null);
    const freq = `${fmtFreq(d.center_mhz)}${d.channel ? ` (${escapeHtml(d.channel)})` : ''}`;
    tr.innerHTML = `
      <td>${isNew ? '⚠' : ''}</td>
      <td>${escapeHtml(d.band)}</td>
      <td>${freq}</td>
      <td><span class="cls" style="color:${classColor(d.class)}">${escapeHtml(d.class)}</span></td>
      <td>${d.power_dbm == null ? '—' : escapeHtml(String(d.power_dbm))} dBm</td>
      <td>${d.bandwidth_mhz == null ? '—' : escapeHtml(String(d.bandwidth_mhz))} МГц</td>
      <td>${fmtPct(d.confidence)}</td>`;
    tb.appendChild(tr);
  }
  table.appendChild(tb);
  return table;
}
```

(`el(tag, cls)` already treats a falsy `cls` as "no class".)

- [ ] **Step 4: Verify parse + suite**

Run: `node --check dashboard/public/spectrum.js`
Expected: no output (valid).

Run: `npm test`
Expected: PASS (no test imports `spectrum.js`; confirms the rest stays green).

- [ ] **Step 5: Commit**

```bash
git add dashboard/public/spectrum.js
git commit -m "feat(dashboard): highlight new detection rows in spectrum table" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Top-bar 🔔 toggle + app.js wiring + styles

**Files:**
- Modify: `dashboard/public/index.html` (🔔 button)
- Modify: `dashboard/public/app.js` (imports, alerter/prevScanKeys, toggle handler, diff+beep+highlight in render)
- Modify: `dashboard/public/styles.css` (button states + `.is-new`)

- [ ] **Step 1: Add the 🔔 button to `index.html`**

In `dashboard/public/index.html`, insert the button immediately before the `<button id="restart-all" ...>` line:

```html
    <button id="sound-toggle" title="Звук сповіщень">🔕</button>
```

- [ ] **Step 2: `app.js` — imports + module state**

In `dashboard/public/app.js`, add this import right after `import { splitByKind, renderSpectrum } from '/spectrum.js';`:

```javascript
import { diffNewKeys, SoundAlerter } from '/alert.js';
```

Add this right after `const spectrumPanel = document.getElementById('spectrum-panel');`:

```javascript
const alerter = new SoundAlerter();
let prevScanKeys = null;
```

- [ ] **Step 3: `app.js` — 🔔 toggle handler**

Add this block right after the `spectrumPanel.addEventListener('click', ...)` delegation listener (added in SP4):

```javascript
// ---- sound-alert toggle ----
const soundBtn = document.getElementById('sound-toggle');
function setSoundUI() {
  soundBtn.textContent = alerter.armed ? '🔔' : '🔕';
  soundBtn.classList.toggle('armed', alerter.armed);
  soundBtn.title = alerter.armed ? 'Звук сповіщень: увімкнено' : 'Звук сповіщень: вимкнено';
}
function setArmed(on) {
  if (on) alerter.arm(); else alerter.disarm();
  localStorage.setItem('soundArmed', on ? '1' : '0');
  setSoundUI();
}
soundBtn.addEventListener('click', () => setArmed(!alerter.armed));
// Restore preference. Autoplay needs a user gesture, so if previously armed, arm on the
// first interaction anywhere on the page.
if (localStorage.getItem('soundArmed') === '1') {
  const resume = () => { alerter.arm(); setSoundUI(); document.removeEventListener('pointerdown', resume); };
  document.addEventListener('pointerdown', resume, { once: true });
  soundBtn.textContent = '🔔';
  soundBtn.classList.add('armed');
} else {
  setSoundUI();
}
```

- [ ] **Step 4: `app.js` — diff + beep + highlight in `render()`**

In `render()`, replace the single line `renderSpectrumPanel(scanners);` with:

```javascript
  const allDets = scanners.flatMap((s) => (s.telemetry && s.telemetry.detections) || []);
  const { keys, newKeys } = diffNewKeys(prevScanKeys, allDets);
  if (prevScanKeys !== null && newKeys.length && alerter.armed) alerter.beep();
  prevScanKeys = keys;
  renderSpectrumPanel(scanners, new Set(newKeys));
```

And update `renderSpectrumPanel` to accept + forward the highlight set. Change:

```javascript
function renderSpectrumPanel(scanners) {
  if (!scanners.length) {
    spectrumPanel.classList.add('hidden');
    spectrumPanel.innerHTML = '';
    return;
  }
  spectrumPanel.classList.remove('hidden');
  renderSpectrum(spectrumPanel, scanners);
}
```

to:

```javascript
function renderSpectrumPanel(scanners, highlightKeys = new Set()) {
  if (!scanners.length) {
    spectrumPanel.classList.add('hidden');
    spectrumPanel.innerHTML = '';
    return;
  }
  spectrumPanel.classList.remove('hidden');
  renderSpectrum(spectrumPanel, scanners, highlightKeys);
}
```

- [ ] **Step 5: `styles.css` — button + row highlight**

Append to `dashboard/public/styles.css`:

```css
/* sound-alert toggle + new-detection highlight */
#sound-toggle.armed { color: var(--on); border-color: var(--on); }
.scan-table tr.is-new { background: rgba(244,183,64,.18); }
.scan-table tr.is-new td { font-weight: 600; }
```

- [ ] **Step 6: Verify parse + suite**

Run: `node --check dashboard/public/app.js`
Expected: no output (valid).

Run: `npm test`
Expected: PASS (full suite green).

- [ ] **Step 7: (Optional) manual browser check**

On a running dashboard with a registered scanner posting telemetry: click 🔔 (turns green), and when a new detection appears the row highlights with ⚠ and a beep plays. Muting (🔕) keeps the highlight, drops the sound. (No jsdom harness in this repo — audio/DOM verified by eye, like `app.js`/`whep.js`.)

- [ ] **Step 8: Commit**

```bash
git add dashboard/public/index.html dashboard/public/app.js dashboard/public/styles.css
git commit -m "feat(dashboard): 🔔 sound-alert toggle + new-detection beep/highlight wiring" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: README note + final suite

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Document the toggle**

In `README.md`, in the `## Scan service (HackRF)` section (after the "Show a scanner on the dashboard" subsection), append:

````markdown
The dashboard's top bar has a **🔔 sound toggle**: when enabled, a new detected transmitter
(any class) plays a short beep and its row in the Spectrum panel is highlighted with ⚠. The beep
needs the toggle clicked once per session (browser autoplay policy); the visual highlight works
regardless.
````

- [ ] **Step 2: Full suite + parse checks**

Run: `npm test`
Expected: PASS — whole suite green (includes the new `test/alert.test.js`).

Run: `node --check dashboard/public/alert.js dashboard/public/spectrum.js dashboard/public/app.js`
Expected: no output (all valid).

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs(dashboard): document the 🔔 sound-alert toggle" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage** (spec § → task):
- §2 trigger = any detection → Task 4 (`allDets` from all scanners, no class filter). Behaviour beep+visual → Tasks 2/3/4. Oscillator → Task 2. Audio gating via 🔔 (gesture) → Task 4. "New" = key absent in prev snapshot, key = band:channel|band:freq↔5MHz → Task 1 (`detectionKey`/`diffNewKeys`). First snapshot baseline (no storm) → Task 1 (`prevKeys===null ⇒ []`) + Task 4 (`prevScanKeys` starts `null`).
- §3 data flow → Task 4 render() block. §4.1 alert.js → Tasks 1/2. §4.2 app.js → Task 4. §4.3 spectrum.js highlight → Task 3. §4.4 index.html button → Task 4. §4.5 styles → Task 4.
- §5 edge cases: autoplay (gesture arm) → Task 2/4; baseline → Task 1/4; jitter bucket → Task 1; persisting no re-beep → Task 1; empty → Task 1 (`detections||[]`); multi-scanner pooled → Task 4 (`flatMap`). §6 testing → Task 1 unit + node --check across tasks. §7 deliverables → match Tasks 1–5.

**Placeholder scan:** no TBD/TODO; every code step has full code; commands have expected output; DOM/audio steps marked not-unit-tested with `node --check` guards.

**Type/name consistency:** `detectionKey(d)`, `diffNewKeys(prevKeys, detections) -> {keys, newKeys}`, `SoundAlerter` (`arm`/`disarm`/`armed`/`beep`), `renderSpectrum(container, scanners, highlightKeys)`, `renderSpectrumPanel(scanners, highlightKeys)`, `prevScanKeys`, `alerter`, `#sound-toggle`, `.is-new` — used identically across tasks and match the spec. `detectionKey` imported by both `app.js` (via `diffNewKeys`) and `spectrum.js`.
