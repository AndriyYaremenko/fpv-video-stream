# Dashboard Audio Alert on Detection — Design Spec

**Date:** 2026-06-17
**Author:** andriy@netlife.com.ua
**Status:** Approved (pending written-spec review)

## 1. Context & Goal

The dashboard's "Spectrum" panel (SP4) renders the HackRF scanner's telemetry — each scanner's
`d.telemetry.detections[]` (every detection has `band`, `center_mhz`, `channel`, `class`
∈ analog|digital|unknown), refreshed over SSE every ~2 s.

**Goal:** alert the operator when a **new possible-video transmitter** appears — an audible beep
plus a visual accent on the new detection row. Purely client-side (browser); no server/scanner
change.

## 2. Confirmed decisions

| Decision | Value |
|---|---|
| Trigger | **Any new detection** (analog / digital / unknown). The detector already requires a ≥5 MHz block above threshold, so any detection is a real wideband transmitter = "possible video". |
| Behaviour | Beep on a **new** detection **+ visual accent** (highlight the new row + ⚠). |
| Sound source | **Web Audio API oscillator** (synthesised beep) — no audio asset, works offline over WG. |
| Audio gating | Beep only when sound is **armed** via a top-bar 🔔 toggle (its click also unlocks browser autoplay). Visual accent shows **always**, regardless of arm state. |
| "New" definition | A detection whose key was **absent in the previous SSE snapshot**. Key = `band:channel` if `channel` present, else `band:<center_mhz rounded to 5 MHz>` (ignores per-cycle freq jitter). |
| First snapshot | The first scanner payload sets the baseline **silently** (no beep storm on page load / scanner connect). |

## 3. Architecture & data flow

```
SSE tick → app.render(devices) → splitByKind → scanners
   gather all scanner detections → keys = detectionKey(d) for each
   newKeys = diffNewKeys(prevScanKeys, detections)      [alert.js, pure]
   if prevScanKeys !== null and newKeys.length and soundArmed: alerter.beep()
   prevScanKeys = currentKeys
   renderSpectrum(panel, scanners, highlightKeys=newKeys)  [spectrum.js]
```

- `prevScanKeys` starts as `null` (uninitialised). The first time scanner detections are seen it is
  populated **without** beeping (baseline). Subsequent ticks diff against it.
- Visual accent is driven by `highlightKeys` passed into `renderSpectrum`; it does not depend on the
  arm state, so muted operators still see new targets.

## 4. Components / files

### 4.1 `dashboard/public/alert.js` (new)
Pure (unit-tested via `node --test`):
- `detectionKey(d)` → `"<band>:<channel>"` when `d.channel` is truthy, else
  `"<band>:<round(center_mhz/5)*5>"`.
- `diffNewKeys(prevKeys, detections)` → `{ keys: Set<string>, newKeys: string[] }`. `newKeys` is
  empty when `prevKeys` is `null` (baseline). Otherwise `newKeys` = keys present now but not in
  `prevKeys`.

DOM/audio (not unit-tested, like `whep.js`):
- `class SoundAlerter` — `arm()` creates/resumes a single `AudioContext` (called from the 🔔 click,
  a user gesture, satisfying autoplay policy); `beep(freq=880, ms=180)` plays a short oscillator
  tone via that context; `armed` getter.

### 4.2 `dashboard/public/app.js`
- Add a top-bar 🔔 toggle handler: flips `soundArmed`, persists to `localStorage` (`soundArmed`),
  on enable calls `alerter.arm()`, updates the button's visual state/label.
- Module state: `let prevScanKeys = null;` and a single `SoundAlerter` instance.
- In `render()` after `splitByKind`: collect all detections across scanners, compute
  `{keys, newKeys}` via `diffNewKeys(prevScanKeys, allDetections)`; if `prevScanKeys !== null &&
  newKeys.length && alerter.armed` → `alerter.beep()`; set `prevScanKeys = keys`; pass the set of
  `newKeys` to `renderSpectrumPanel`/`renderSpectrum` as `highlightKeys`.

### 4.3 `dashboard/public/spectrum.js`
- `renderSpectrum(container, scanners, highlightKeys = new Set())` — thread `highlightKeys` to the
  detection table; a row whose `detectionKey(d)` ∈ `highlightKeys` gets a `is-new` CSS class + a ⚠
  marker. Import `detectionKey` from `alert.js` so the key logic is shared (single source of truth).

### 4.4 `dashboard/public/index.html`
- A 🔔 button in the top bar (next to the existing controls), default muted state.

### 4.5 `dashboard/public/styles.css`
- 🔔 button armed/muted styling; `.scan-table tr.is-new` highlight (e.g. accent background + the ⚠).

## 5. Edge cases

- **Autoplay policy:** audio only after the 🔔 click (user gesture) creates/resumes the
  `AudioContext`. Before arming, no beep (visual accent still works).
- **First payload / scanner reconnect:** `prevScanKeys === null` ⇒ baseline, no beep.
- **Frequency jitter:** key buckets `center_mhz` to 5 MHz (or uses `channel`) so the same
  transmitter doesn't re-alert each cycle.
- **Persisting target:** no re-beep while a detection's key stays present; a beep recurs only if it
  disappears then returns.
- **No scanners / empty detections:** `diffNewKeys` over an empty list ⇒ no new keys, no beep.
- **Multiple scanners:** detections from all scanners are pooled into one key set (a key already
  includes band; collisions across scanners on the same band+channel are treated as the same target,
  acceptable for an alert).

## 6. Testing

- `test/alert.test.js` (new, `node --test`): `detectionKey` (channel vs rounded-freq forms,
  jitter bucketing), `diffNewKeys` (baseline `null` ⇒ no newKeys; new key detected; persisting key
  ⇒ no newKeys; removed key ignored).
- Audio (`SoundAlerter`) and DOM rendering: manual in-browser (no jsdom harness in this repo,
  consistent with `app.js`/`whep.js`); `node --check` guards `alert.js`, `app.js`, `spectrum.js`.

## 7. Deliverables

```
dashboard/public/alert.js      (new: detectionKey, diffNewKeys, SoundAlerter)
dashboard/public/app.js        (change: 🔔 toggle, prevScanKeys, beep + highlight wiring)
dashboard/public/spectrum.js   (change: renderSpectrum highlightKeys param + is-new rows)
dashboard/public/index.html    (change: 🔔 button)
dashboard/public/styles.css    (change: button states + .is-new highlight)
test/alert.test.js             (new)
README.md                      (note: 🔔 audio-alert toggle on the dashboard)
```

## 8. Out of scope (YAGNI)

- Custom/uploaded sound files (Approach B); per-class distinct tones; volume control.
- Server-side alerting / the telemetry `alarm` field (client already has detections).
- Desktop/push notifications; alert history/log.
- Acknowledge/snooze UI (single beep per new target already avoids nagging).

## 9. Assumptions

- `detectionKey` lives in `alert.js` and is imported by both `app.js` and `spectrum.js` so the
  beep trigger and the row highlight use the exact same key. `node --test` can import `alert.js`
  because its pure helpers reference no DOM at module load (AudioContext is created lazily in
  `SoundAlerter.arm()`).
- Deploys as a dashboard change (rebuild the `dashboard` container on the server).
