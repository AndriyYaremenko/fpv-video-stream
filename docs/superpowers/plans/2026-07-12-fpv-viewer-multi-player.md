# FPV Viewer — per-viewer players + channel switching — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework the «FPV Viewer» screen from one auto-picked player into a grid with one player+controls per online view-capable SDR, per-row viewer buttons to route detections, and prev/next-detection steppers.

**Architecture:** Pure list/selection helpers in `viewer.js` (unit-tested); a reconciled card grid in `views/viewer.js` (built once per viewer id, never re-innerHTML'd — mirrors `dashboard.js` tile reconcile); a Map of WHEP players keyed by viewer id in `app.js` (generalizes the current single gen-token player). Front-end only — zero server/agent/MQTT changes.

**Tech Stack:** Vanilla ES modules, node:test, WHEP (WebRTC), dev-preview harness (`dev-serve.mjs` + `?preview=1` + `fixtures.js` + `window.__rerender`).

## Global Constraints

- **Front-end ONLY.** No changes to `agent/`, server, MQTT topics, or `lib/`. The store/`fpv/<id>/view` contract is unchanged.
- **Reconcile-safety (hard rule):** a card holds a live WHEP `<video>` and a user-typed MHz `<input>`. NEVER re-`innerHTML` a card's container on a data tick — build once per viewer id, update only text nodes / button state / mini-spectrum. (See [[tactical-ui-redesign]] gotcha.)
- Viewer identity is the scanner id (`store[id]`, e.g. `bladerf`, `hackrf`). A viewer is "available" when `store[id].online && store[id].view`.
- Per-card video element id = **`viewer-video-${id}`** (contract between `views/viewer.js` and `app.js`).
- Keep all existing exports in `viewer.js` (`pickViewer`, `activeViewer`, `viewStream`, `playerKey`, `viewerListHtml`, `whepRetryDelay`, …) — `test/viewer.test.js` imports them.
- Freq bounds for a view start: `100 <= f <= 6000`.
- `npm test` must stay green after every task (browser code is not unit-tested; the visual gate in Task 4 covers it).
- Deploy = rebuild+recreate the dashboard container only (`--no-deps`), wg-easy/mediamtx/mosquitto untouched.
- Commit footer:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01JLJdXSvLMBojY7w2dcUwuN
  ```
- Branch: `feat/fpv-viewer-multi-player` (spec already committed there).

## File structure

- Modify `dashboard/public/viewer.js` — add `viewerCards`, `stepDetectionFreq`, `viewerLabel` (pure).
- Modify `test/viewer.test.js` — tests for the three new helpers.
- Modify `dashboard/public/app.js` — `syncViewerPlayers()` (Map by id) replaces `syncViewerPlayer()`; `viewerRowClick(freq, band, viewerId)`; ctx.handlers wiring; import `viewerCards`.
- Modify `dashboard/public/views/viewer.js` — reconciled card grid + per-row viewer buttons + steppers + per-card mini-spectrum; remove the recovered-frame thumbnail.
- Modify `dashboard/public/fixtures.js` — add a second view-capable scanner (`hackrf`) to `scanStore`.
- Modify `dashboard/public/styles.css` — `.viewer-cards` grid + `.viewer-card` styles.

---

### Task 1: Pure helpers — `viewerCards`, `stepDetectionFreq`, `viewerLabel`

**Files:**
- Modify: `dashboard/public/viewer.js`
- Test: `test/viewer.test.js`

**Interfaces:**
- Consumes: existing `viewStream(store, id)` (same file).
- Produces:
  - `viewerLabel(id: string) -> string`
  - `viewerCards(store) -> Array<{id, label, stream, view}>` — online view-capable scanners, sorted by id.
  - `stepDetectionFreq(rows, curFreq, dir) -> number|null` — freq of the prev/next detection (`dir` ±1) among `rows` (each `{center_mhz}`), sorted ascending, wrapping at ends; `null` if no rows.

- [ ] **Step 1: Write the failing tests**

Append to `test/viewer.test.js` (imports at top: add `viewerCards, stepDetectionFreq, viewerLabel` to the existing import from `../dashboard/public/viewer.js`):

```javascript
test('viewerLabel maps known ids, passes through unknown', () => {
  assert.equal(viewerLabel('bladerf'), 'bladeRF');
  assert.equal(viewerLabel('hackrf'), 'HackRF');
  assert.equal(viewerLabel('scan-07'), 'scan-07');
});

test('viewerCards lists online view-capable scanners, sorted by id, with stream', () => {
  const store = {
    hackrf: { online: true, view: { active: false, stream: 'hackrf-view' } },
    bladerf: { online: true, view: { active: true, stream: 'bladerf-view' } },
    offline: { online: false, view: { stream: 'x-view' } },
    noview: { online: true },
  };
  const cards = viewerCards(store);
  assert.deepEqual(cards.map((c) => c.id), ['bladerf', 'hackrf']);   // sorted; offline/noview excluded
  assert.equal(cards[0].label, 'bladeRF');
  assert.equal(cards[0].stream, 'bladerf-view');
  assert.equal(cards[1].stream, 'hackrf-view');
  assert.equal(cards[0].view.active, true);
});

test('viewerCards falls back to <id>-view when the announce omits stream', () => {
  const cards = viewerCards({ bladerf: { online: true, view: {} } });
  assert.equal(cards[0].stream, 'bladerf-view');
});

test('viewerCards handles empty/undefined store', () => {
  assert.deepEqual(viewerCards({}), []);
  assert.deepEqual(viewerCards(undefined), []);
});

test('stepDetectionFreq steps to the next/prev detection and wraps', () => {
  const rows = [{ center_mhz: 5800 }, { center_mhz: 1200 }, { center_mhz: 5865 }]; // unsorted input
  assert.equal(stepDetectionFreq(rows, 1200, 1), 5800);      // next up
  assert.equal(stepDetectionFreq(rows, 5800, 1), 5865);
  assert.equal(stepDetectionFreq(rows, 5865, 1), 1200);      // wrap to lowest
  assert.equal(stepDetectionFreq(rows, 5865, -1), 5800);     // prev down
  assert.equal(stepDetectionFreq(rows, 1200, -1), 5865);     // wrap to highest
});

test('stepDetectionFreq: null/невідома поточна частота -> перший (up) / останній (down)', () => {
  const rows = [{ center_mhz: 1200 }, { center_mhz: 5865 }];
  assert.equal(stepDetectionFreq(rows, null, 1), 1200);
  assert.equal(stepDetectionFreq(rows, null, -1), 5865);
  assert.equal(stepDetectionFreq(rows, 3000, 1), 5865);      // next above 3000
  assert.equal(stepDetectionFreq(rows, 3000, -1), 1200);     // prev below 3000
});

test('stepDetectionFreq: empty rows -> null; single detection returns itself', () => {
  assert.equal(stepDetectionFreq([], 5800, 1), null);
  assert.equal(stepDetectionFreq([{ center_mhz: 5800 }], 5800, 1), 5800);
  assert.equal(stepDetectionFreq([{ center_mhz: 5800 }], 5800, -1), 5800);
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx node --test test/viewer.test.js`
Expected: FAIL — `viewerCards`/`stepDetectionFreq`/`viewerLabel` are not exported (`SyntaxError` or `not a function`).

- [ ] **Step 3: Write minimal implementation**

Add to `dashboard/public/viewer.js` (after `viewStream`, keeping all existing exports):

```javascript
export function viewerLabel(id) {
  const map = { bladerf: 'bladeRF', hackrf: 'HackRF' };
  return map[id] || id;
}

// Online, view-capable scanners as render cards — one player per card. Sorted by id for a stable grid.
export function viewerCards(store) {
  return Object.keys(store || {})
    .filter((id) => store[id] && store[id].online && store[id].view)
    .sort()
    .map((id) => ({ id, label: viewerLabel(id), stream: viewStream(store, id), view: store[id].view }));
}

// Freq of the prev/next detection relative to curFreq (dir ±1), among rows sorted ascending, wrapping
// at the ends. curFreq null/NaN -> lowest (up) or highest (down). Empty rows -> null.
export function stepDetectionFreq(rows, curFreq, dir) {
  const freqs = [...new Set((rows || []).map((r) => r.center_mhz))].sort((a, b) => a - b);
  if (!freqs.length) return null;
  if (curFreq == null || !Number.isFinite(curFreq)) return dir > 0 ? freqs[0] : freqs[freqs.length - 1];
  if (dir > 0) {
    const nxt = freqs.find((f) => f > curFreq + 1e-6);
    return nxt != null ? nxt : freqs[0];
  }
  const prevs = freqs.filter((f) => f < curFreq - 1e-6);
  return prevs.length ? prevs[prevs.length - 1] : freqs[freqs.length - 1];
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx node --test test/viewer.test.js`
Expected: PASS (existing tests + new ones).

- [ ] **Step 5: Run the full Node suite (no regression)**

Run: `npm test`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add dashboard/public/viewer.js test/viewer.test.js
git commit -m "feat(viewer): pure helpers viewerCards/stepDetectionFreq/viewerLabel

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JLJdXSvLMBojY7w2dcUwuN"
```

---

### Task 2: `app.js` — per-viewer WHEP player Map + explicit-target row click

**Files:**
- Modify: `dashboard/public/app.js` (imports; the `syncViewerPlayer`/`startViewerWhep` block at ~40-92; `viewerRowClick` at ~152-163; `ctx.handlers` at ~194-196)

**Interfaces:**
- Consumes: `viewerCards` (Task 1); existing `viewStream`, `pickViewer`, `pickRxScanner`, `whepRetryDelay`, `startWhep`, `nearestRxChannel`, `cfg`.
- Produces (contract for Task 3):
  - `ctx.handlers.syncViewerPlayers()` — call after every viewer DOM update; binds a WHEP player to each `#viewer-video-${id}` for `viewerCards(store)`, tears down players for viewers no longer present.
  - `ctx.handlers.viewerRowClick(freq, band, viewerId)` — start a view on the explicit `viewerId` (falls back to `pickViewer` if that id isn't a live viewer); 5.8G also nudges the RX5808.

- [ ] **Step 1: Update the import**

In `dashboard/public/app.js`, change the `/viewer.js` import (line ~9-12) to add `viewerCards`:

```javascript
import {
  emptyViewer, applyDetections, seedFromJournal,
  pickViewer, pickRxScanner, viewStream, activeViewer, playerKey, whepRetryDelay, viewerCards,
} from '/viewer.js';
```

- [ ] **Step 2: Replace the single-player block with a per-id Map**

Replace the whole block from `let viewerPlayer = null;` through the end of `startViewerWhep` (app.js ~40-92) with:

```javascript
// ==== FPV Viewer per-viewer WHEP players (Map keyed by scanner id) ==========
// One player per online view-capable SDR. Generalizes the single gen-token player:
// each id owns its own {player, streamKey, retry}; retry-object identity is the
// generation token, replaced whenever that id's stream changes or the card leaves.
const viewerPlayers = new Map();   // id -> { player, streamKey, retry:{timer,inflight} }

function syncViewerPlayers() {
  const store = scanClient.store;
  const cards = ctx.scanners().length ? viewerCards(store) : [];
  const wantIds = new Set(cards.map((c) => c.id));
  for (const [id, st] of viewerPlayers) {          // tear down players whose card is gone
    if (!wantIds.has(id)) {
      if (st.player) st.player.close();
      if (st.retry.timer) clearTimeout(st.retry.timer);
      viewerPlayers.delete(id);
    }
  }
  for (const c of cards) {
    const video = document.getElementById(`viewer-video-${c.id}`);
    if (!video) continue;                          // card not mounted yet
    let st = viewerPlayers.get(c.id);
    if (!st) { st = { player: null, streamKey: '', retry: { timer: null, inflight: false } }; viewerPlayers.set(c.id, st); }
    const want = PREVIEW ? '' : c.stream;
    if (want !== st.streamKey) {
      if (st.player) st.player.close();
      st.player = null;
      st.streamKey = want;
      if (st.retry.timer) clearTimeout(st.retry.timer);
      st.retry = { timer: null, inflight: false };  // new generation for this id
      if (!want) { video.srcObject = null; continue; }
      startViewerWhep(c.id, video, c.stream, st.retry, 0);
      continue;
    }
    if (want && !st.player && !st.retry.timer && !st.retry.inflight) {
      startViewerWhep(c.id, video, c.stream, st.retry, 0);   // same key, died, retries gave up -> re-kick
    }
  }
}

async function startViewerWhep(id, video, stream, retry, attempt) {
  const st0 = viewerPlayers.get(id);
  if (PREVIEW || !st0 || st0.retry !== retry || attempt > 40) return;
  retry.inflight = true;
  try {
    const p = await startWhep(video, `${cfg.webrtcBase}/${stream}/whep`, cfg.readUser, cfg.readPass,
      () => { const s = viewerPlayers.get(id); if (s && s.player === p) { p.close(); s.player = null; } });
    retry.inflight = false;
    const s = viewerPlayers.get(id);
    if (!s || s.retry !== retry || s.player) { p.close(); return; }   // superseded or a sibling won
    s.player = p;
  } catch {
    retry.inflight = false;
    const s = viewerPlayers.get(id);
    if (!s || s.retry !== retry) return;                              // superseded: stay inert
    retry.timer = setTimeout(() => { retry.timer = null; startViewerWhep(id, video, stream, retry, attempt + 1); },
      whepRetryDelay(attempt));
  }
}
```

- [ ] **Step 3: Route the row click to an explicit viewer**

Replace `viewerRowClick` (app.js ~152-163) with:

```javascript
// FPV Viewer row/button click: start the view on the explicitly-chosen viewerId (the row
// renders a button per online viewer); fall back to pickViewer if that id isn't a live viewer.
// 5.8G also nudges the RX5808 to the nearest hardware channel (unchanged).
function viewerRowClick(freq, band, viewerId) {
  if (PREVIEW) return;
  const store = scanClient.store;
  const chosen = (viewerId && store[viewerId] && store[viewerId].online && store[viewerId].view) ? viewerId : null;
  const vid = chosen || pickViewer(store);
  if (!vid || !Number.isFinite(freq) || freq < 100 || freq > 6000) return;
  scanClient.publishView(vid, 'start', freq);
  if (band === '5.8G') {
    const rxId = pickRxScanner(store);
    const ch = nearestRxChannel(freq);
    if (rxId && ch) scanClient.publishCommand(rxId, { mode: 'manual', channel: ch.name });
  }
}
```

- [ ] **Step 4: Rename the handler**

In `ctx.handlers` (app.js ~194-196), change `syncViewerPlayer,` to `syncViewerPlayers,` (keep `viewerRowClick,`).

- [ ] **Step 5: Run the Node suite (no regression)**

Run: `npm test`
Expected: PASS (browser code untested; this confirms nothing else broke).

- [ ] **Step 6: Syntax check the module**

Run: `node --check dashboard/public/app.js`
Expected: no output (valid syntax).

- [ ] **Step 7: Commit**

```bash
git add dashboard/public/app.js
git commit -m "feat(viewer): per-viewer WHEP player Map + explicit-target row click

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JLJdXSvLMBojY7w2dcUwuN"
```

---

### Task 3: `views/viewer.js` — reconciled per-viewer card grid

**Files:**
- Modify: `dashboard/public/views/viewer.js` (full rewrite of the stage; list rows gain viewer buttons)

**Interfaces:**
- Consumes: `viewerCards`, `stepDetectionFreq`, `viewerLabel`, `viewerRows`, `ageLabel` (from `/viewer.js`); `renderMiniSpectrum`, `classColor`, `fmtFreq`, `viewCaption` (from `/spectrum.js`); `nearestRxChannel`; `el`, `escapeHtml` (from `/views/components.js`); `ctx.handlers.syncViewerPlayers`, `ctx.onViewStart`, `ctx.onViewStop`, `ctx.onScanCmd`, `ctx.handlers.viewerRowClick`.
- Produces: one `<video id="viewer-video-${id}">` per online viewer; calls `ctx.handlers.syncViewerPlayers()` after each DOM update.

- [ ] **Step 1: Rewrite `views/viewer.js`**

Replace the entire file with:

```javascript
// dashboard/public/views/viewer.js — «FPV Viewer»: merged detection list (left) +
// a reconciled grid of per-viewer player cards (right), one per online view-capable SDR.
// Cards (and their live #viewer-video-<id> + MHz input) are built ONCE per id and reused
// across renders — never re-innerHTML'd — so WHEP players and typed input survive data ticks.
import { el, escapeHtml } from '/views/components.js';
import { viewerRows, viewerCards, stepDetectionFreq, ageLabel } from '/viewer.js';
import { renderMiniSpectrum, classColor, fmtFreq, viewCaption } from '/spectrum.js';
import { nearestRxChannel } from '/rx5808-channels.js';

export function render(container, ctx) {
  container.className = 'screen';
  let root = container.querySelector('.viewer');
  if (!root) {
    container.innerHTML = '';
    root = el('div', 'viewer');
    const list = el('div', 'viewer-list');
    list.addEventListener('click', (e) => {
      const btn = e.target.closest('.vw-go');
      if (!btn) return;
      const freq = Number(btn.dataset.vwfreq);
      if (!Number.isFinite(freq)) return;
      ctx.handlers.viewerRowClick(freq, btn.dataset.vwband || '', btn.dataset.vid || '');
    });
    root.appendChild(list);
    root.appendChild(el('div', 'viewer-cards'));
    container.appendChild(root);
  }
  update(root, ctx);
}

function update(root, ctx) {
  const nowS = Math.floor(Date.now() / 1000);
  const store = ctx.scanStore();
  const cards = ctx.scanners().length ? viewerCards(store) : [];
  const rows = viewerRows(ctx.viewerState(), nowS);

  renderList(root.querySelector('.viewer-list'), cards, rows, nowS, store);
  reconcileCards(root.querySelector('.viewer-cards'), cards, ctx, rows, store);

  ctx.handlers.syncViewerPlayers();   // (re)bind WHEP players to the mounted #viewer-video-<id>
}

// ---- left: merged detection list; each row carries a ▶ button per online viewer ----
function renderList(list, cards, rows, nowS, store) {
  // Highlight rows whose freq matches ANY active view session.
  const activeFreqs = cards.filter((c) => c.view && c.view.active).map((c) => c.view.freq_mhz);
  list.innerHTML = '';
  if (!cards.length) list.appendChild(el('p', 'muted', 'SDR view недоступний (view-сканер офлайн)'));
  if (!rows.length) { list.appendChild(el('p', 'muted', 'детекцій немає — чекаємо на скан')); return; }
  for (const e of rows) list.appendChild(rowEl(e, cards, nowS, activeFreqs));
}

function rowEl(entry, cards, nowS, activeFreqs) {
  const viewing = activeFreqs.some((f) => f != null && Math.abs(entry.center_mhz - f) < 3);
  const clsName = entry.class === 'analog' ? 'analog' : entry.class === 'digital' ? 'digital' : '';
  const row = el('div', `viewer-row${clsName ? ` ${clsName}` : ''}${entry.live ? '' : ' recent'}${viewing ? ' is-viewing' : ''}`);
  const freqTxt = `${fmtFreq(entry.center_mhz)}${entry.channel ? ` (${escapeHtml(entry.channel)})` : ''}`;
  const snr = entry.snr_db == null ? '—' : `${Number(entry.snr_db).toFixed(1)} dB`;
  const src = Object.keys(entry.seen_by || {}).map(escapeHtml).join(', ') || '—';
  const age = entry.live ? 'зараз' : ageLabel(nowS, entry.last_seen);
  const btns = cards.map((c) =>
    `<button type="button" class="vw-go" data-vid="${escapeHtml(c.id)}" data-vwfreq="${Number(entry.center_mhz)}" ` +
    `data-vwband="${escapeHtml(entry.band || '')}">▶ ${escapeHtml(c.label)}</button>`).join('');
  row.innerHTML = `<div class="vr-top"><span class="vr-freq mono">${freqTxt}</span>` +
    `<span class="mono" style="color:${classColor(entry.class)}">${escapeHtml(entry.class || '')}</span></div>` +
    `<div class="vr-meta">${escapeHtml(entry.band || '')} · SNR ${snr} · ${src} · ${age}</div>` +
    `<div class="vr-go">${btns}</div>`;
  return row;
}

// ---- right: reconcile a card per viewer id (reuse; never destroy a live <video> or MHz input) ----
function reconcileCards(wrap, cards, ctx, rows, store) {
  const wantIds = new Set(cards.map((c) => c.id));
  for (const child of [...wrap.children]) {
    const id = child.id ? child.id.replace('viewer-card-', '') : '';
    if (id && !wantIds.has(id)) child.remove();
  }
  const empty = wrap.querySelector('.viewer-cards-empty'); if (empty) empty.remove();
  for (const c of cards) {
    let card = document.getElementById(`viewer-card-${c.id}`);
    if (!card) { card = buildCard(c, ctx, rows); wrap.appendChild(card); }
    card.__ctxRows = rows;                                  // fresh rows for the steppers' click reads
    updateCard(card, c, store);
  }
  if (!cards.length) wrap.appendChild(el('p', 'muted viewer-cards-empty', 'Немає доступних вьюверів.'));
}

function buildCard(c, ctx, rows) {
  const card = el('div', 'viewer-card');
  card.id = `viewer-card-${c.id}`;
  card.__ctxRows = rows;
  card.innerHTML = `
    <div class="vc-head mono">${escapeHtml(c.label)}</div>
    <video id="viewer-video-${escapeHtml(c.id)}" autoplay playsinline muted></video>
    <div class="view-controls">
      <button type="button" class="btn vc-step" data-dir="-1">◀</button>
      <input class="vc-freq" type="number" min="100" max="6000" step="1" placeholder="МГц" />
      <button type="button" class="btn vc-step" data-dir="1">▶</button>
      <button type="button" class="btn vc-play">▶ дивитись</button>
      <button type="button" class="btn vc-stop" hidden>■ свіп</button>
      <span class="vc-badge view-badge"></span>
      <span class="vc-err view-err"></span>
    </div>
    <canvas class="mini-spectrum" width="300" height="60"></canvas>`;

  const freqInput = card.querySelector('.vc-freq');
  const curFreq = () => {
    const view = card.__view;
    if (view && view.active && view.freq_mhz != null) return view.freq_mhz;
    const v = Number(freqInput.value);
    return Number.isFinite(v) ? v : null;
  };
  card.querySelectorAll('.vc-step').forEach((b) => b.addEventListener('click', () => {
    const f = stepDetectionFreq(card.__ctxRows, curFreq(), Number(b.dataset.dir));
    if (f != null) { freqInput.value = String(f); ctx.onViewStart(c.id, f); }
  }));
  card.querySelector('.vc-play').addEventListener('click', () => {
    const f = Number(freqInput.value);
    if (Number.isFinite(f) && f >= 100 && f <= 6000) ctx.onViewStart(c.id, f);
  });
  card.querySelector('.vc-stop').addEventListener('click', () => ctx.onViewStop(c.id));

  const canvas = card.querySelector('canvas.mini-spectrum');
  canvas.addEventListener('click', (e) => {
    const lo = Number(canvas.dataset.lowMhz), hi = Number(canvas.dataset.highMhz);
    if (!Number.isFinite(lo) || !Number.isFinite(hi)) return;
    const rect = canvas.getBoundingClientRect();
    const x = Math.min(rect.width, Math.max(0, e.clientX - rect.left));
    const freq = Math.round(lo + (x / (rect.width || 1)) * (hi - lo));
    freqInput.value = String(freq);
    if (canvas.classList.contains('tunable') && canvas.dataset.sid) {
      const ch = nearestRxChannel(freq);
      if (ch) ctx.onScanCmd(canvas.dataset.sid, { mode: 'manual', channel: ch.name });
    }
  });
  return card;
}

// The band behind a card's mini-spectrum: band containing the active view freq; else 5.8G; else first.
function pickBand(live, view) {
  const bands = (live && live.bands) || {};
  if (view && view.active && view.freq_mhz != null) {
    for (const [name, range] of Object.entries(bands)) {
      if (range && view.freq_mhz >= range.low_mhz && view.freq_mhz <= range.high_mhz) return name;
    }
  }
  if (bands['5.8G']) return '5.8G';
  const keys = Object.keys(bands);
  return keys.length ? keys[0] : null;
}

function updateCard(card, c, store) {
  const view = c.view;
  card.__view = view;                                        // for the steppers' curFreq()
  card.querySelector('.vc-badge').textContent = view ? viewCaption(view) : '';
  card.querySelector('.vc-err').textContent = (view && view.error) || '';
  card.querySelector('.vc-stop').hidden = !(view && view.active);
  card.classList.toggle('is-active', !!(view && view.active));

  const live = store[c.id] || null;
  const band = live ? pickBand(live, view) : null;
  const range = (live && band && live.bands && live.bands[band]) || {};
  const psd = (live && band && live.latestPsd && live.latestPsd[band]) || [];
  const dets = live ? ((live.detection && live.detection.detections) || []).filter((d) => d.band === band) : [];
  const rxFreq = live && live.rxtune ? live.rxtune.freq_mhz : null;

  const canvas = card.querySelector('canvas.mini-spectrum');
  canvas.classList.remove('tunable');
  if (range.low_mhz != null) {
    canvas.dataset.lowMhz = range.low_mhz; canvas.dataset.highMhz = range.high_mhz; canvas.dataset.sid = c.id;
  } else { delete canvas.dataset.lowMhz; delete canvas.dataset.highMhz; delete canvas.dataset.sid; }
  renderMiniSpectrum(canvas, { psd, range, dets, rxFreq, tunable: band === '5.8G' });
}
```

- [ ] **Step 2: Syntax check**

Run: `node --check dashboard/public/views/viewer.js`
Expected: no output.

- [ ] **Step 3: Run the Node suite (no regression)**

Run: `npm test`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add dashboard/public/views/viewer.js
git commit -m "feat(viewer): reconciled per-viewer card grid with steppers + row buttons

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JLJdXSvLMBojY7w2dcUwuN"
```

---

### Task 4: Fixtures + CSS + visual gate

**Files:**
- Modify: `dashboard/public/fixtures.js` (add a second view-capable scanner to `scanStore`)
- Modify: `dashboard/public/app.js` (one-line `window.__store` dev seam in the preview boot branch)
- Modify: `dashboard/public/styles.css` (`.viewer-cards`, `.viewer-card`, `.vr-go`)

**Interfaces:**
- Consumes: the Task 1-3 code.

- [ ] **Step 1: Add a second viewer to the preview fixtures**

In `dashboard/public/fixtures.js`, inside `scanStore` (after the `bladerf: { … }` entry, before the closing `}` of `scanStore`), add a `hackrf` viewer so preview shows two cards:

```javascript
  , hackrf: {
    online: true, status_ts: NOW,
    bands: { '5.8G': { low_mhz: 5645, high_mhz: 5945 } },
    latestPsd: { '5.8G': psd(64, -72) }, waterfalls: { '5.8G': [] },
    detection: { ts: NOW, occupancy: { '5.8G': 0.2 }, detections: [
      { band: '5.8G', center_mhz: 5865, class: 'analog', power_dbm: -48, bandwidth_mhz: 17, confidence: 0.9, channel: 'A1', snr_db: 16 } ] },
    rxtune: { ts: NOW, freq_mhz: 5865, channel: 'A1', mode: 'manual', targets: [] },
    view: { ts: NOW, active: false, freq_mhz: null, until_ts: null, error: null, stream: 'hackrf-view' },
  }
```

(Comma-first keeps the existing `bladerf` entry untouched; verify the object still parses.)

Also add a dev-only store seam so the visual gate can flip a viewer offline. In `dashboard/public/app.js`, in `boot()`'s `if (PREVIEW) {` branch, right after `scanClient.store = structuredClone(fx.scanStore);`, add:

```javascript
    window.__store = scanClient.store;   // dev-only seam: visual gate mutates online/view then __rerender()
```

- [ ] **Step 2: Add the grid CSS**

Append to `dashboard/public/styles.css`:

```css
/* FPV Viewer — per-viewer card grid */
.viewer { display: flex; gap: 16px; align-items: flex-start; }
.viewer-list { flex: 0 0 320px; max-width: 360px; }
.viewer-cards { flex: 1 1 auto; display: flex; flex-wrap: wrap; gap: 12px; }
.viewer-card { flex: 1 1 320px; min-width: 300px; max-width: 520px; border: 1px solid var(--line); border-radius: 6px; padding: 8px; }
.viewer-card.is-active { border-color: var(--accent, #4ad); }
.viewer-card .vc-head { font-weight: 600; margin-bottom: 4px; }
.viewer-card video { width: 100%; aspect-ratio: 4 / 3; background: #000; display: block; border-radius: 4px; }
.viewer-card .view-controls { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin: 6px 0; }
.viewer-card .vc-freq { width: 84px; }
.viewer-card .mini-spectrum { width: 100%; height: 60px; }
.viewer-row .vr-go { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 4px; }
.viewer-row .vw-go { font-size: 12px; padding: 2px 8px; cursor: pointer; }
@media (max-width: 860px) { .viewer { flex-direction: column; } .viewer-list { flex-basis: auto; max-width: none; width: 100%; } }
```

(If any of these var names / base button classes differ from the codebase, match the existing convention — do NOT invent new design tokens.)

- [ ] **Step 3: Syntax-check the touched browser modules + run the Node suite**

Run: `node --check dashboard/public/fixtures.js && node --check dashboard/public/app.js && npm test`
Expected: no `--check` output; `npm test` PASS. (fixtures.js is not imported by node tests, so `--check` is its only automated guard before the visual gate.)

- [ ] **Step 4: Visual gate (dev-preview) — REQUIRED, do not skip**

Start the preview server and drive it (playwright/chrome MCP, or report to the controller to run it):

```bash
node dashboard/dev-serve.mjs &     # serves dashboard/public with ?preview support
```

Load `http://localhost:<port>/?preview=1#/viewer` and verify:
1. **Two cards** render side by side: `bladeRF` (badge shows its active session «▶ 5800 …») and `HackRF` (idle, `■ свіп` hidden). Each has a `<video>`, `◀ [МГц] ▶`, `▶ дивитись`.
2. **Row buttons:** each detection row shows `▶ bladeRF` and `▶ HackRF`.
3. **Stepper:** type nothing; click `▶` on a card → the MHz field fills with a detection freq (via `stepDetectionFreq`).
4. **Reconcile-safety:** type `5750` into HackRF's MHz field, then run `window.__rerender()` twice. Assert the typed `5750` survives and the `<video id="viewer-video-hackrf">` node is the SAME element (not replaced):
   ```javascript
   // evaluate in the page:
   document.querySelector('#viewer-card-hackrf .vc-freq').value = '5750';
   const v0 = document.getElementById('viewer-video-hackrf');
   window.__rerender(); window.__rerender();
   ({ freq: document.querySelector('#viewer-card-hackrf .vc-freq').value,
      sameVideo: document.getElementById('viewer-video-hackrf') === v0 })
   // expect { freq: '5750', sameVideo: true }
   ```
5. **Offline removes a card:** flip the viewer offline via the dev seam and re-render:
   ```javascript
   window.__store.hackrf.online = false; window.__rerender();
   ({ hackrfCard: !!document.getElementById('viewer-card-hackrf'),
      bladerfCard: !!document.getElementById('viewer-card-bladerf') })
   // expect { hackrfCard: false, bladerfCard: true }
   ```
6. **No console errors** during the above.

Record the observed results (screenshot + the two evaluate outputs) in the task report.

- [ ] **Step 5: Commit**

```bash
git add dashboard/public/fixtures.js dashboard/public/app.js dashboard/public/styles.css
git commit -m "feat(viewer): preview fixtures (2nd viewer) + card-grid styles + __store seam

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JLJdXSvLMBojY7w2dcUwuN"
```

---

## Deploy (after merge)

Front-end only. On the server `traefik` (`ssh -i ~/.ssh/fpv_deploy andriy@193.242.163.139`, sudo pass in [[bladerf-viewer-role]]):
```bash
cd ~/fpv-video-stream && git pull
sudo docker compose build dashboard && sudo docker compose up -d --no-deps dashboard
```
wg-easy/mediamtx/mosquitto untouched. Then over-WG visual check of «FPV Viewer»: two player cards, switch channels on each independently, route a detection to a chosen SDR.

## Self-review (spec coverage)
- ✅ Per-viewer players (Task 3 grid + Task 2 Map).
- ✅ Convenient channel switching: ◀▶ = prev/next detection (Task 1 `stepDetectionFreq` + Task 3 steppers) + MHz input.
- ✅ Route detection to a chosen viewer: per-row buttons (Task 3) → `viewerRowClick(freq,band,viewerId)` (Task 2).
- ✅ Per-card mini-spectrum; thumbnail removed (Task 3).
- ✅ Reconcile-safety (Task 3 build-once cards; Task 4 visual gate proves it).
- ✅ 5.8G RX5808 dual-action preserved (Task 2 + Task 3 canvas click).
- ✅ No server/agent/MQTT changes; `npm test` green each task; visual gate (Task 4).
