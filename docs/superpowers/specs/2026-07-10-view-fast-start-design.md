# View fast start — click → picture latency — Design

**Date:** 2026-07-10
**Status:** Approved design, ready to implement (own branch off `main`: `feat/view-fast-start`).
**Target:** scanner Pi 5, hackrf view agent (`fpv-scan-hackrf.service`) + dashboard.
**Context:** follows the multiband view workflow (spec `2026-07-07-multiband-view-workflow`) and the
smooth/fast/sync view pipeline (specs `2026-07-07-view-smooth-15fps`, `2026-07-08-view-demod-fast`,
`2026-07-08-view-sync-lock`). Detection works, streaming works, the picture is there — but the
operator waits too long between clicking a frequency and seeing video.

## Problem — where the latency actually is

**Session start** (click a frequency in the FPV Viewer panel):
1. MQTT command → sweep aborts via the existing `has_pending` hooks — fast, ~1 s.
2. `run_stream` spawns `hackrf_transfer`, reads the first 0.5 s chunk, detects the standard, and
   only THEN spawns ffmpeg — the RTSP path appears ~2–3 s after the command.
3. The dashboard sees the new view state over SSE and tries a WHEP POST; if the path isn't up yet
   it retries every 1.5 s (`app.js startViewerWhep`).
4. **Dominant term:** `build_encode_cmd` sets no GOP, so libx264 defaults to `keyint=250` —
   at 15 fps that is a **~16.7 s GOP**. A WHEP viewer joining mid-GOP shows black until the next
   IDR: up to 16 s, ~8 s on average. This is the "довго очікуємо на стрім".

**Channel switch (retune)** is worse because it restarts everything:
- `run_view` sets the stop event; `run_stream` kills `hackrf_transfer` AND ffmpeg, then respawns
  both at the new frequency (full pipeline restart, ~7 s observed).
- `playerKey = stream|freq_mhz|until_ts` (`viewer.js`) deliberately changes on every retune, so the
  dashboard tears down the WHEP session and reconnects with the same 1.5 s retry quantisation and
  the same IDR lottery.

## Decisions taken (with the operator)

- Scope: **all three tiers** (quick wins + persistent stream + in-process retune).
- The agent keeps its **sweep + view dual role**: in production there is ONE SDR device (either a
  HackRF or a bladeRF) doing both. View capture must therefore go through a device abstraction.
- **Capture abstraction now, HackRF implementation now; the bladeRF view backend is a later PR**
  (it needs live testing and the production bladeRF is busy sweeping).
- **Always-alive stream**: the RTSP push and the dashboard WHEP session live 24/7 (placeholder
  frames while idle/sweeping), so the player is already connected when the operator clicks.

## Goal & non-goals

| Scenario | Today | Target |
|---|---|---|
| Session start (click → picture) | 5–20 s (IDR lottery) | **≤ 3 s** |
| Channel switch within a session | ~7 s | **≤ 1.5 s** |
| Black screen during retune | yes | no (freeze/placeholder) |

- **Goal:** cut click→picture latency to the table above without regressing the 6 MS/s realtime
  budget, the 15 fps smoothness gate (`dropped_chunks=0`, stats log), or the sync lock.
- **Non-goals:** the bladeRF `CaptureSource` implementation (later PR — the interface ships now);
  colour; audio; changing the sweep/detection path (`dweller.py`, `sweeper.py` untouched); OSD text
  rendered into the placeholder frames (the dashboard badge already shows state); multi-viewer
  fan-out beyond what MediaMTX already provides.

## Design

### 2a. Persistent encoder (agent) — PR-B

One ffmpeg process lives for the whole agent lifetime and pushes RTSP to MediaMTX 24/7:

- **Fixed canvas.** Height is always **288** (the PAL field height); NTSC fields (240 rows) are
  resampled to 288 by the existing `resize_rows`. The rawvideo geometry no longer depends on the
  detected standard, so the encoder never needs a restart. Width stays `view_width`.
- **Short GOP.** `-g <fps>` (IDR every ~1 s) added to `build_encode_cmd` — this alone is PR-A and
  deploys first, since it fixes the dominant join-latency term on the current architecture too.
- **Idle placeholder.** Between view sessions and while the device is opening, the writer emits
  black frames at the same `view_fps` (x264 of a static black frame is ~free CPU-wise;
  ~50–150 kbit/s over the WG link). The ffmpeg rawvideo timeline never stops.
- The encoder becomes a supervised component of the agent main loop (spawned at startup when
  `view_enabled`), no longer owned by `run_stream`.

### 2b. Capture abstraction (agent) — PR-C

New `CaptureSource` interface (agent/video or agent/scan, mirroring `bladerf_source.py` placement):

```
open(sample_rate_hz)      # claim the device, start streaming
tune(freq_hz)             # retune WITHOUT stopping the stream; flush transient samples
read_chunk() -> bytes|None  # one CHUNK_S of int8 IQ; None on timeout (watchdog input)
close()                   # release the device (sweep needs it back)
```

- **`HackRFSource`** — cffi binding over `libhackrf` (same pattern as the bladeRF cffi source):
  the rx callback fills a bounded ring buffer (drop-oldest, counted — feeds the existing
  `dropped_chunks` stat); `read_chunk()` assembles 0.5 s chunks from the ring; `tune()` calls
  `hackrf_set_freq()` live (milliseconds) and flushes the ring so the tune transient never reaches
  the demod.
- The subprocess `hackrf_transfer` path remains available as the `legacy` engine (see Rollback).

### 2c. Retune without restart (agent) — PR-C

`run_view` / `run_stream` rework:

- A view session **opens the capture source once** and holds it; sweep is paused for the whole
  session (already true today — `run_view` blocks the scan loop). On session end the source is
  closed so the sweep's one-shot `hackrf_transfer`/`hackrf_sweep` subprocesses don't hit
  "Resource busy".
- **Retune = `source.tune()` + fresh standard detection on the next chunk + fresh `SyncTracker`.**
  No process kill/spawn anywhere. The fixed canvas absorbs a PAL↔NTSC change.
- The demod loop, mailbox, frame queue, pacer, writer thread and stats log all survive the retune
  unchanged.

### 2d. Stable player (dashboard) — PR-B

- `playerKey` becomes just `stream` while the scanner exposes a view stream — the WHEP session is
  established once when the panel appears and survives start/stop/retune. Frequency and TTL are
  already shown by the badge (`viewCaption`), not by the player identity.
- WHEP first-connect retry backoff: 0.3 s initial, exponential up to 1.5 s (today: flat 1.5 s).

### Placeholder state machine (writer thread)

The writer keeps its pacer and stats log; only the frame source changes:

| State | Frames written |
|---|---|
| `IDLE` / sweeping | black frame at `view_fps` |
| `TUNING` (device opening or retune transient) | freeze of the last live frame; black if none yet |
| `LIVE` | demod frames from the queue |

Implementation: when the frame queue stays empty longer than ~0.5 s, the writer emits the
placeholder/last frame at fps pace. No separate feeder thread; the timeline is self-healing.

### Failure handling

- **Capture watchdog** (replaces the "free" recovery the per-session subprocess restart used to
  give): if the rx callback delivers no data for > 3 s, close + reopen the device; after 3
  consecutive failures publish the existing `view error` state and return to sweep.
  HackRF's chronic USB wedge is the known hazard here; fewer open/close cycles may actually help,
  but the watchdog is the safety net either way.
- **ffmpeg death:** the supervisor respawns the persistent encoder with backoff; the MediaMTX path
  disappears for a few seconds and the dashboard's existing retry loop reconnects.
- **Rollback:** env `VIEW_ENGINE=persistent|legacy` (default `persistent`). `legacy` restores
  today's behaviour exactly (per-session `hackrf_transfer` + per-session ffmpeg) for safe deploys.

## PR split (deploy order)

1. **PR-A — quick wins (deploys immediately):** `-g <fps>` in `build_encode_cmd`; faster WHEP
   retry backoff. Start drops to ~3–5 s on the current architecture.
2. **PR-B — persistent stream:** agent-lifetime ffmpeg + fixed 288 canvas + idle placeholder +
   `playerKey = stream` + the `VIEW_ENGINE=persistent|legacy` toggle (rollback must ship with the
   first behaviour change, not after it). In PR-B a retune still restarts the per-session
   `hackrf_transfer` (~2–3 s), hidden behind the freeze/placeholder.
3. **PR-C — in-process capture:** `CaptureSource` + `HackRFSource` (cffi) + live `tune()` retune +
   watchdog; extends what `VIEW_ENGINE=persistent` means (in-process capture instead of
   per-session subprocess).

The bladeRF `CaptureSource` implementation is explicitly a later, separate PR.

## Testing & acceptance

- **Unit (pure pieces, existing style):** canvas resize (240→288, 288→288); ring-buffer chunk
  assembly incl. drop-oldest counting; `tune()` flush semantics; watchdog with a fake clock
  (silence → reopen → error escalation); writer placeholder switch (queue-empty → placeholder at
  pace, live frames resume); `playerKey` stability across retune; encode cmd contains `-g`.
- **Pipeline gate (authoritative, as before):** `--pipeline` bench at 6 MS/s — `dropped_chunks=0`,
  steady 15.0 fps, `mailbox=0`.
- **Live acceptance on the Pi:** stopwatch click→picture for (a) cold session start ≤ 3 s and
  (b) retune ≤ 1.5 s; no black screen during retune; stats log stays clean across ≥ 3 retunes and
  a session end → sweep → new session cycle; sweep still produces detections between sessions.

## Risks

- `hackrf_set_freq` transient garbage → mitigated by the ring flush; worst case one extra chunk of
  noise (0.5 s).
- Device open after a USB wedge can exceed the 3 s budget → watchdog handles it; the operator sees
  the placeholder + the existing error badge instead of a hang.
- cffi/libhackrf version drift on the Pi → verify against the installed `libhackrf` at deploy;
  the `legacy` engine is the fallback.
- 24/7 RTSP push adds constant (small) WG traffic and a long-lived ffmpeg on the Pi — accepted
  explicitly by the operator.
