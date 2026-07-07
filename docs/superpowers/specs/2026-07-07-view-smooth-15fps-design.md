# Smooth 15 fps SDR view stream (no air gaps) — Design

**Date:** 2026-07-07
**Status:** Approved design, ready to implement (own branch off `main`).
**Target:** scanner Pi 5, hackrf view agent (`fpv-scan-hackrf.service`).
**Context:** sub-feature 2 of the improved SDR viewer ([[improve-sdr-view-next]]); baseline is the
live view stream ([[sdr-view-stream]]) as deployed by [[multiband-view-workflow]]
(6 MS/s, 360×288 PAL, VIEW_FPS 15).

## Problem (verified in code)
`stream_demod.run_stream` demodulates a 0.5 s chunk in ~0.32 s (the benchmarked "0.64× realtime"),
but the SAME loop then spends ~0.47 s pacing the chunk's frames into ffmpeg (`FramePacer.tick`
sleeps between frames). Total ≈0.79 s per 0.5 s of air → the capture mailbox (single-slot,
drop-backlog) discards ~40% of chunks in steady state. The stream shows bursts of video separated
by gaps — the operator sees a jerky picture even though nominal fps is 15.

## Goal & non-goals
- **Goal:** continuous 15 fps output with zero steady-state chunk drops; graceful degradation
  (drop oldest queued frames, not whole chunks of air) under transient CPU spikes.
- **Non-goals (documented follow-up, sub-feature 2b):** 25 fps field-rate output, DSP hot-path
  optimisation (float32 path, subsampled median, early decimation before `slice_lines`),
  lower latency. Chosen scope is the minimal architectural fix at 15 fps.

## Design (approach A — writer thread + bounded frame queue)
All changes in `agent/video/stream_demod.py`; the public contract
`run_stream(vcfg, freq_mhz, stop_event, max_s, ...) -> error|None` is unchanged, so the
view controller / retune loop / main are untouched.

- **Demod loop (existing thread):** mailbox → `iq_from_int8` → demod → `select_frames` →
  **push frames to a bounded queue** (no pacing, no ffmpeg writes). Returns to the mailbox in
  ~0.32 s per chunk → keeps up with the air.
- **Writer thread (new `_writer`):** owns the `FramePacer` (unchanged class) and ffmpeg stdin;
  pops frames from the queue in order and writes them at `VIEW_FPS`. Started right after the
  encoder subprocess is spawned (standard detection publishes the first chunk as today).
- **Queue:** `collections.deque(maxlen=int(VIEW_FPS * 1.0))` (~1 s of frames) guarded by a
  `threading.Condition`. Overflow drops the OLDEST frame (live tail beats stale frames);
  a drop counter is kept for the stats log.
- **Errors:** a write failure (`BrokenPipeError`/`OSError`) or dead encoder detected in the
  writer sets a shared error slot and wakes the loop; the demod loop exits exactly like today's
  error path (`finally` kills both subprocesses; retune/stop semantics unchanged).
- **Shutdown:** `stop_event` (stop / retune / timeout) wakes the writer via the condition;
  the writer exits before the `finally` cleanup. No frame is written after stop.
- **Observability:** every ~10 s the writer logs
  `view stream: <avg fps> fps, queue=<depth>, dropped_frames=<n>, dropped_chunks=<k>`
  (chunk drops counted in the demod loop when the mailbox overwrote an unconsumed chunk —
  requires the reader to bump a counter instead of silently replacing). This log line IS the
  acceptance metric.

## Testing
- pytest (fake clock, fake pipes, no hardware — extend `agent/video/tests/test_stream_demod.py`
  patterns): continuous chunks → all selected frames written in order, paced, zero drops;
  blocked/slow writer → queue drops oldest, demod loop never blocks; stop mid-write → clean
  exit, nothing written after stop; ffmpeg death in writer → `run_stream` returns the error
  string as today.
- `agent/video/bench_stream.py` gains a `--pipeline` mode: feeds chunks at the real-time rate
  through the restructured loop with a fake sink and reports `dropped_chunks` + achieved fps.
  Gate on the Pi 5: dropped_chunks=0 at 6 MS/s.
- Live acceptance: view a real signal ≥60 s → stats log shows ~15.0 fps and dropped_chunks=0;
  the in-panel player shows continuous motion (no periodic freezes); retune and stop behave
  exactly as before.

## Risks / notes
- Steady-state latency becomes chunk (0.5 s) + queue (≤1 s) + encoder — comparable to today;
  latency was explicitly not the target.
- If the Pi is CPU-starved by the concurrent bladeRF sweep + x264 (view runs alongside
  `fpv-scan`), the design degrades by dropping oldest frames — visible as mild judder, not
  gaps; the stats log makes it diagnosable.
- Deploy = Pi `git pull` + restart `fpv-scan-hackrf` only (bladerf unit unaffected).
