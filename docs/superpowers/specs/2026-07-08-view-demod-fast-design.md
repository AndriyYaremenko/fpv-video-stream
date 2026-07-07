# Fast view demod (sub-feature 2b) — Design

**Date:** 2026-07-08
**Status:** Approved design, ready to implement (own branch off `main`).
**Target:** scanner Pi 5, hackrf view agent (`fpv-scan-hackrf.service`).
**Context:** follow-up to the smooth-15fps stream ([[sdr-view-stream]], spec
`2026-07-07-view-smooth-15fps-design.md`). That feature removed the pacing overrun, but the Pi
under production load (bladeRF sweep + grabber x264) demodulates 6 MS/s at **1.65× realtime**
(the "0.64×" baseline predates the second agent), so chunks still drop. The deployed workaround
is 4 MS/s + Nice=-10 (soft picture, ~5–17% gaps).

## Goal & non-goals
- **Goal:** demod a 0.5 s chunk at 6 MS/s comfortably under realtime on the LOADED Pi
  (target ≤ ~0.75×), with pixel-equivalent output — restores 6 MS/s sharpness AND, together with
  a 2-deep chunk mailbox absorbing transient CPU spikes, achieves `dropped_chunks=0`.
- **Non-goals:** new dependencies (numba/scipy/C ext — pure numpy only), 25 fps output, resolution
  changes, any change to the SCAN path's DSP (`dweller.iq_from_int8` feeds calibrated dBm features
  and keeps its /128 scale).

## Measured stage profile (dev box, 6 MS/s, 0.5 s chunk = 112.6 ms total; Pi ≈ ×7 under load)
| stage | ms | lever |
|---|---|---|
| `np.median` over all 3 M samples (fm_demod DC removal) | 13.9 | median of `[::64]` subsample = 0.23 ms, statistically identical |
| `np.interp` in `slice_lines` | ~23 | 6e6/15625 = **exactly 384** → integer-spl fast path = pure `reshape` |
| `iq_from_int8` extra `/128` pass | 18.3 (part) | FM angle is scale-invariant → drop the divide; `astype(float32).view(complex64)` avoids copies |
| float64 throughout reconstruct/lowpass | ~24 | float32 end-to-end halves memory traffic |
| ALL 12.5 fields built, `select_frames` keeps 8 | ~35% of reconstruct | build only the fps-budget fields (select BEFORE `build_frame`) |
| `angle` (atan2) + complex multiply | 24.8 | stays — the honest floor |

Forecast: ~112 → ~50 ms/chunk on the dev box ⇒ ≈0.73× realtime at 6 MS/s on the loaded Pi.

## Design (all pure numpy)

### `agent/video/demod.py`
- `fm_demod(iq, median_stride=64)`: DC estimate = `np.median(inst[::median_stride])`
  (stride 1 = old exact behavior, used by tests to prove equivalence). Keep float32 math
  (complex64 in → float32 out; no float64 promotion).
- `lowpass`: compute in float32 (`asarray(x, float32)`); the cumsum accumulator stays float64
  internally for numeric safety, output float32. Length/edge behavior unchanged.

### `agent/video/frame.py`
- `slice_lines`: when `fs/LINE_HZ` is integer within 1e-9 (PAL at 4/6/8 MS/s), slice by
  `reshape` — no `pos` arrays, no `np.interp`; else the existing interp path (NTSC line rate is
  non-integer). Sync-roll logic unchanged. Output dtype follows input (float32 stays float32).
- `reconstruct_frames(baseband, fs, standard, width, blank_frac, budget=None)`: with a `budget`
  (max frames to build), pick that many fields EVENLY across the chunk (same spacing rule as
  `select_frames`) and run `_align_vsync`/`build_frame` only on those. `budget=None` = old
  behavior. `build_frame` computes in float32.

### `agent/video/stream_demod.py`
- New `iq_from_int8_fast(raw)`: `frombuffer(int8) → astype(float32) → view(complex64)` — no
  `/128` scale (FM demod, standard detection and per-frame `normalize_luma` are all
  scale-invariant). Used ONLY by the view stream; `dweller.iq_from_int8` untouched (scan dBm
  calibration depends on its scale).
- `chunk_to_frames(..., budget=None)` passes the budget to `reconstruct_frames`; `run_stream`
  passes `int(round(CHUNK_S * vcfg.view_fps))` so `select_frames` becomes a no-op guard.
- `ChunkMailbox(depth=2)`: bounded 2-deep FIFO (was 1) — `put` beyond depth drops the OLDEST
  chunk (counted as today); `take` pops FIFO. Absorbs an isolated CPU spike: the demod catches
  back up because the average is < 1× realtime. Worst-case added latency during a spike = 0.5 s,
  recovers immediately after.

### Deploy
Pi `git pull` + edit the tune drop-in (`fpv-scan-hackrf.service.d/tune.conf`):
`VIEW_SAMPLE_RATE_HZ=6000000` (back to sharp), keep `Nice=-10`; restart `fpv-scan-hackrf` only.

## Testing
- **Equivalence (golden) tests:** integer-spl `slice_lines` reshape path == interp path exactly
  (np.interp at integer grid positions returns grid values); `fm_demod(median_stride=64)` vs
  `stride=1` on synthetic CVBS — resulting FRAMES equal within tight tolerance;
  float32 pipeline vs old float64 on synthetic CVBS — frames within uint8 ±1 after
  `normalize_luma`; `iq_from_int8_fast` vs `dweller.iq_from_int8 * 128` exact.
- **Budget tests:** `reconstruct_frames(budget=k)` returns k frames, evenly spaced, each equal to
  the corresponding unbudgeted frame; `chunk_to_frames` respects the budget.
- **Mailbox depth:** FIFO order, drop-oldest beyond 2, counter semantics (existing test extended).
- **Perf gate:** `bench_stream.py` unchanged CLI; on the Pi `--fs 6e6` demod-only must print
  ≤ ~0.9× realtime and `--pipeline --fs 6e6 --rounds 40` (with `nice -10`, other units running,
  hackrf unit stopped for the bench) → `dropped_chunks=0` on ≥2 consecutive runs.
- Live acceptance (after HackRF hub replug): view a real signal ≥60 s at 6 MS/s → stats log
  ~15.0 fps, dropped_chunks=0, sharper picture than the 4 MS/s interim.

## Risks / notes
- The reshape fast path only fires at integer samples-per-line: PAL@6MS/s=384 ✓; NTSC keeps
  interp (its line rate isn't integer-divisible) — NTSC stays ~20% slower; acceptable (targets
  are PAL, and the budget/median/float32/no-scale wins still apply).
- Removing the /128 scale changes absolute baseband amplitude; everything downstream in the VIEW
  path is relative (verified: detect_standard uses SNR ratios, normalize_luma per-frame) — the
  golden tests pin this.
- If the Pi gate still shows sporadic drops after all this, the remaining lever is the mailbox
  depth (3) — latency trade, decided at deploy time, not silently.
