# Multiband «scan→click→watch» workflow — Design

**Date:** 2026-07-07
**Status:** Approved design, ready to implement (own branch off `main`).
**Target:** dashboard + scanner Pi 5 (`andriy@192.168.1.204`, hackrf unit `SCAN_ID=hackrf`).
**Context:** sub-feature 1 of the improved SDR viewer ([[improve-sdr-view-next]]); builds on the
live SDR view stream ([[sdr-view-stream]], spec `2026-07-05-sdr-view-stream-design.md`).

## Goal
One dashboard panel that collects FPV detections from ALL bands and ALL scanners into a single
list; the operator clicks a row and watches the demodulated video right there, and can switch
between detections in seconds — the «Бачу FPV» workflow on our stack.

## Gaps this closes (verified in code)
1. No merged list — UI is strictly per-scanner (`spectrum.js` one block per scanner, each with its
   own detection table).
2. No routing — the ▶ on a detection row publishes the view command to the scanner that *detected*
   it (`app.js`), but only the hackrf agent has `VIEW_ENABLED`; bladeRF (the prod sweeper of all 4
   bands) detections are effectively not watchable.
3. Switching is slow — view entry waits for the end of a full sweep cycle
   (`main.py` polls `view.pending()` only between cycles), and changing frequency means
   stop → full cycle → start.

## Decisions (from the operator)
- **UX:** new «FPV Viewer» panel above the existing scanner blocks (those stay as diagnostics):
  merged detection list on the left, embedded WHEP player on the right.
- **Routing:** capability is data-driven — a view-enabled agent announces itself with a retained
  view state at startup; the dashboard sends every view command to the announcing scanner.
- **Switching:** retune-in-place (session restart inside `run_view`, no return to sweep) + fast
  entry (pending checked between bands inside the cycle, not only between cycles).
- **List contents:** live detections + recently-gone ones (TTL 5 min, dimmed but clickable);
  seeded from the detection journal on page load.
- **5.8G dual action:** clicking a 5.8G row also tunes the RX5808 to the nearest channel
  (same behaviour as today's 5.8 canvas click).
- **Retune mechanism:** session restart (capture+ffmpeg restart, WHEP player auto-reconnects,
  ~3–5 s) — chosen over hot capture swap because PAL/NTSC (and thus frame height) must be
  re-detected per signal; hot swap remains a possible later optimisation.

## Architecture / data flow
```
bladeRF sweep ──┐                                       ┌─▶ «FPV Viewer» panel
                ├─▶ fpv/+/detection ─▶ browser (merge) ─┤   list: live + recent (TTL 5 min)
hackrf sweep ───┘                                       └─▶ click ─▶ fpv/hackrf/rxcmd {view:start, freq}
                                                                      │
hackrf agent: view_controller ◀───────────────────────────────────────┘
  │  (fast entry: pending checked between bands in run_cycle)
  ├─▶ fpv/hackrf/view (retained: active, freq_mhz, until_ts, stream:"hackrf-view")
  └─▶ RTSP push ─▶ MediaMTX ─▶ WHEP player inside the panel
```
No broker/ACL changes (same `rxcmd` + `view` topics), no server changes (`GET /api/detections`
and the `hackrf-view` pseudo-camera already exist).

## Agent (Pi, hackrf unit)

### `agent/scan/view_controller.py` — retune
- `set_command({view:'start', freq_mhz})`: stores `_pending` as today AND **also sets `_stop`** —
  an active `run_stream` returns promptly; when idle this is harmless (`run_view` already clears a
  stale stop on entry).
- `run_view` becomes a loop: publish `active(freq, until_ts=now+max_s)` → `run_stream(...)` →
  on return check pending: new freq → clear stop, publish the new active state with a **fresh
  `until_ts`** (every retune restarts the 10-min timer), loop; none → publish inactive +
  `reset_hackrf()` → back to sweep. If `run_stream` returned an error while a retune is pending,
  reset the device before restarting — the retune is not lost.
- New non-consuming `has_pending()` for the sweep abort hook.

### `agent/scan/main.py` — fast entry
- `run_cycle(...)` gains an optional `abort=` callable, checked at the top of the per-band loop
  (`main.py:101`). The main loop passes `view.has_pending`. When it fires, the cycle returns early
  without publishing the aggregate payload (per-band publishes already sent stand). Worst-case
  entry latency = one band sweep (seconds).

### `agent/scan/publisher.py` + wiring — capability announce
- The `fpv/<id>/view` payload gains a `stream` field: the WHEP stream name derived from
  `VIEW_PUSH_URL` (last path segment → `"hackrf-view"`). `ViewController` holds it and includes it
  in every state publish.
- On agent startup with view enabled: immediately publish retained
  `{active:false, stream:"hackrf-view"}` — announces capability AND clears a stale retained
  `active:true` left by a crash.

RX5808 command branch untouched; command/topic schema unchanged.

## Dashboard

### `dashboard/public/viewer.js` (new) — merged list
- State: `Map` keyed by `band + rounded MHz` (same identity idea as the journal's `detectionKey`)
  → `{band, center_mhz, class, snr_db, power_dbm, channel, scanners:{id:last_ts}, last_seen, live}`.
- Feed: every `fpv/+/detection` upserts entries for that scanner; an entry is **live** while at
  least one scanner reported it in its latest cycle, then **recent** (dimmed, with age
  «2 хв тому») until TTL 5 min, then dropped. A signal seen by both scanners is ONE row with both
  scanner badges.
- Seed on page load from `GET /api/detections` (journal events): reconstructs «recent» entries so
  they survive a reload; live entries re-confirm via retained detection messages anyway.
- Sort: live (by power desc) → recent (by last_seen desc). Row: freq MHz, band chip, class,
  SNR, scanner badges, age; highlighted when its frequency matches the active view.
- Click a row → `publishView(viewerId, {view:'start', freq_mhz})` where `viewerId` = the scanner
  whose `store[id].view` exists (retained announce). If several are view-capable, prefer an idle
  one. 5.8G dual action: also `publishCommand({mode:'manual', channel: nearestRxChannel(freq)})`.
  No view-capable scanner online → rows render without ▶ + hint «SDR view недоступний».

### Player (in-panel)
- Existing `whep.js` on the stream named by the view state's `stream` field. Connects when
  `active:true`; on retune the RTSP path is recreated → the player auto-retries (~1 s interval)
  until the stream is back. Badge «▶ NNNN МГц до HH:MM» (from `until_ts`), ■ stop button
  (`{view:'stop'}`).

### Minor
- `mqtt-scan.js`: the `stream` field flows through the existing `fpv/+/view` reduce unchanged.
- `index.html`/`style.css`: panel markup — list left, player right; stacks vertically on narrow
  screens.
- `app.js`: render the panel in the existing render cycle, click delegation, journal seed fetch.

## Server — zero changes.

## Error handling
- Pipeline error during retune → device reset + restart at the new freq (retune not lost).
- View state `error` surfaces in the panel.
- A detection disappearing while being watched just moves its row to «recent»; the view session
  is not interrupted.

## Testing
- pytest (no hardware): retune state machine (start during active → restart without sweep return;
  stop → exit; fresh `until_ts`); `has_pending` + `abort` in `run_cycle` (fake cycle); startup
  retained announce; `stream` derivation from push URL.
- Node: list merge/TTL/dedupe/sort reducer; routing to the view-capable scanner; 5.8G dual action.
- Live acceptance: a bladeRF detection (e.g. TX at ~4240) → click in the panel → video in the
  in-panel player within seconds; click another detection → switch ≤5 s; stop/timeout → sweep
  resumes (spectrum updates return).

## Deploy
- Pi: git pull + restart `fpv-scan` hackrf unit (do NOT overwrite the hand-diverged unit file).
- Server: surgical dashboard update ([[deployment-target]] — WG-only, don't break wg-easy).

## Risks / notes
- Switch latency depends on WHEP reconnect behaviour; if >5 s in practice, tighten the player
  retry, and the hot-capture-swap optimisation remains available later.
- The hackrf agent's own sweep pauses during view (by design, 10-min cap per retune); bladeRF
  keeps sweeping all bands, so the list stays fresh while watching.
- Journal seeding is best-effort: if `/api/detections` is unavailable the list still works
  live-only.
