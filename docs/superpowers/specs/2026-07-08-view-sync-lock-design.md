# View sync lock (sub-feature 3) — Design

**Date:** 2026-07-08
**Status:** Approved design, ready to implement (own branch off `main`).
**Target:** scanner Pi 5, hackrf view agent (`fpv-scan-hackrf.service`).
**Context:** third sub-feature of the improved SDR viewer ([[improve-sdr-view-next]]); builds on the
fast demod ([[sdr-view-stream]], specs `2026-07-07-view-smooth-15fps` + `2026-07-08-view-demod-fast`).

## Problem
The operator sees two picture defects vs the reference tool «Бачу FPV» (which shows a locked
"Синхро авто H105 V65"):
- **Horizontal shear/drift** — lines don't stack; the picture leans. Root cause: `slice_lines`
  slices at the NOMINAL line rate (`fs/LINE_HZ[standard]`, e.g. 15625 Hz for PAL). A real FPV
  camera's crystal is off by tens–hundreds of Hz, so the sync tip walks across rows → shear.
- **Vertical roll/jumps** — the picture scrolls or jumps between frames. Root cause: `_align_vsync`
  picks the darkest consecutive-row window per chunk independently, so (a) it can mis-lock onto a
  dark scene region instead of the true vertical-blanking interval (VBI), and (b) the field-boundary
  phase is not carried across chunks.

## Goal & non-goals
- **Goal:** a horizontally-stable (no shear) and vertically-stable (no roll/jump) picture on a real
  analog signal, achieved by locking to the ACTUAL line rate and a robust VBI, with the lock status
  surfaced in the stats log. Preserve the 2b reshape fast path and the 6 MS/s realtime budget.
- **Non-goals:** a full per-line PLL / sub-sample resampling (approach B — overkill for
  "smoothness > sharpness"); on-video OSD (drawtext) of the sync readout; colour; any change to the
  scan-side snapshot path (`video_emit`/`pipeline.py` call `reconstruct_frames` WITHOUT a tracker →
  bit-for-bit unchanged).

## Design (approach A — stateful SyncTracker, fast-path-preserving)

### `agent/video/sync_tracker.py` (new)
`SyncTracker` holds one view session's sync state; created per session in `run_stream`, reset on
retune (a fresh session = fresh tracker).
- State: `line_hz` (actual line rate, seeded once per session; init = nominal for the detected
  standard), `vsync_row` (last locked field-boundary row, for cross-chunk bias; init None),
  `locked` (bool).
- `seed(baseband, fs)`: one-time actual-line-rate estimate from an rfft magnitude peak
  (parabolic-interpolated for sub-bin precision), called once per view session — a real FPV
  camera's crystal is stable within a session, and a per-chunk rfft would blow the Pi realtime
  budget. Clamp the refined value to ±2% of nominal (every real FPV crystal, with margin) and
  search the peak within ±1% of nominal; a candidate must ALSO have a prominent 2nd harmonic at
  exactly 2x its frequency to confirm it's a real line rate (rejects in-window CVBS artifacts
  that aren't the line fundamental). Out-of-range or unconfirmed → keep nominal, `locked=False`.
  Lock is guaranteed within ±2% of nominal; just outside that band the tracker fails safe to
  nominal; beyond that it's best-effort (out of contract — no real crystal drifts that far).
- `predict_vsync(n_field_rows) -> int|None`: given the carried `vsync_row` and field period,
  the expected boundary row for this chunk (None until first lock).
- Accessors for the stats readout: `line_hz`, `h_drift_samples_per_row`, `vsync_row`, `locked`.

### `agent/video/frame.py` — line-rate override + shear correction
- `slice_lines(baseband, fs, standard, line_hz=None)`: `line_hz` overrides `LINE_HZ[standard]` for
  the `spl` computation. **Slicing still uses the nearest integer `spl` reshape fast path** (never
  reverts to `np.interp` for the refined rate — that would undo 2b). `line_hz=None` = today's
  nominal behavior exactly.
- New `deshear(rows, drift_per_row)`: shift row `r` left by `round(r * drift_per_row)` samples via
  a vectorized per-row circular gather (`np.take_along_axis` on a broadcast index grid), where
  `drift_per_row = spl_actual - spl_nominal_int` (fractional samples the sync tip walks per line).
  Cheap (one gather over the field), preserves dtype. `drift_per_row == 0` → identity.
- `reconstruct_frames(..., tracker=None)`: with a tracker, uses `tracker.line_hz` for slicing,
  applies `deshear`, and passes the tracker to `_align_vsync` for the predicted-phase bias;
  `tracker=None` = unchanged (nominal, no deshear, independent vsync search — scan path).

### `agent/video/frame.py` — robust VBI lock (V)
- `_align_vsync(rows, tracker=None, win=6)`: score each candidate row window by **low mean AND
  low variance** (broad vsync pulses sit near sync level with little variation, unlike a merely
  dark scene) instead of mean alone; require the winning window to beat the field median on both.
  With a tracker: bias the search to a small band around `tracker.predict_vsync(...)` when a
  prediction exists, and fall back to a full search (re-acquire) when the best local candidate is
  weak — so a dropped chunk between sessions just re-locks, no harm. Update `tracker.vsync_row`.
  `tracker=None` = the current darkest-window heuristic (unchanged).

### `agent/video/stream_demod.py` — wiring
- `run_stream` constructs a `SyncTracker` per session; after `pick_standard` picks the standard it
  reuses the same rfft (via `detect_standard`/a thin helper) to seed the tracker, then passes the
  tracker into `chunk_to_frames(..., tracker=tracker)` each chunk.
- The writer-loop stats line gains a sync field:
  `view stream: <fps> fps, queue=N, mailbox=M, dropped_frames=F, dropped_chunks=C, sync=H<h> V<v> line=<hz>Hz`
  (from the tracker; `H`/`V` are the current drift-samples-per-row-scaled offset and vsync row).

## Testing
- pytest (synthetic CVBS via `agent/video/synth.py`, no hardware):
  - `seed`: on a clean synthetic signal generated at a KNOWN off-nominal line rate (e.g. 15705 Hz),
    the refined estimate converges within a few Hz; on pure noise (or a lone tone with no 2nd
    harmonic) the ±2% clamp / harmonic gate rejects and `locked` stays False; just outside ±2%,
    the tracker still fails safe to nominal rather than false-locking.
  - `deshear`: a synthetically sheared field (known linear drift) is straightened — columns of a
    vertical-bar test pattern become vertical (per-column variance across rows drops sharply).
  - vsync robustness: a field with a dark SCENE band plus a true VBI → the low-mean+low-variance
    detector locks the VBI, not the scene; cross-chunk bias keeps the boundary row stable across
    consecutive synthetic chunks (no jump), and re-acquires after an injected gap.
  - `tracker=None` regression: `slice_lines`/`reconstruct_frames`/`_align_vsync` outputs are
    bit-for-bit identical to the pre-branch versions (scan path untouched) — golden oracle.
- Perf: `bench_stream.py --pipeline --fs 6e6` on the Pi still `dropped_chunks=0` (the per-row
  gather must not blow the budget); a `--sync` bench flag optional if needed to exercise the tracker.
- Live acceptance (HackRF on a real analog signal, 3410/4240): picture no longer shears or rolls;
  the stats log shows a stable `sync=H.. V.. line=..Hz` (line_hz near but not exactly nominal =
  proof the lock is tracking the real crystal); retune re-locks.

## Risks / notes
- The ±2% clamp (with a ±1% search window and a 2nd-harmonic confirmation gate) assumes crystal
  error < 2% (20000 ppm) — generous margin over every real FPV camera crystal; real cams are
  usually < 1000 ppm. Lock is guaranteed within that band, fails safe to nominal just outside it,
  and is best-effort (out of contract) beyond that — no real crystal drifts that far.
- Deshear is integer-sample per row — sub-sample shear remains but is invisible after the downscale
  to 360 px; that's the "smoothness > sharpness" trade.
- Cross-chunk vsync carry only helps within a run of contiguous chunks; a dropped chunk (rare in
  steady state per the 2b live test) forces a clean re-acquire — acceptable.
- If the per-row gather measurably dents the 6 MS/s budget, fall back to applying deshear only to
  the fields actually built (already the fps-budget subset) — it already is, since deshear runs
  inside `reconstruct_frames`'s per-field loop.
