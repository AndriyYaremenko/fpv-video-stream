# HackRF Scan Service — Design Spec (Sub-project 1)

**Date:** 2026-06-16
**Author:** andriy@netlife.com.ua
**Status:** Approved (pending written-spec review)

## 1. Context & Goal

The FPV video-stream system currently ingests H.264 from Raspberry Pi nodes (analog video via
USB grabber → `ffmpeg` → MediaMTX over WireGuard → WebRTC dashboard). We are adding RF
spectrum awareness and multi-band reception. A HackRF One is already connected to a Pi node.

The full effort is decomposed into sub-projects (built one at a time, each with its own spec):

| # | Sub-project | Hardware | Depends on |
|---|---|---|---|
| **1** | **HackRF scan service** (this spec) | HackRF + Pi (have it) | — |
| 2 | Reception + per-band tuner abstraction (rx5808 5.8G; 1.2G/2.4G RX modules) → CVBS → grabber → existing push pipeline | rx5808 + 1.2/2.4 RX modules + grabbers | — |
| 3 | Auto-tune orchestration (closed loop: detections → assign receivers → start/stop feeds) | both above | 1 + 2 |
| 4 | Dashboard surfacing (spectrum/occupancy view, detection list, manual override) | — | 1 |

**This spec covers Sub-project 1 only.** It is independently valuable (real-time spectrum
situational awareness) and is the foundation every other sub-project consumes.

### Goal

A daemon on the Pi that continuously sweeps the FPV bands, **detects active video carriers**,
**classifies** each as analog / likely-digital / unknown, and emits a structured detection list +
band occupancy. It reports best-effort to the existing dashboard telemetry hook and serves
results locally for later sub-projects.

## 2. Confirmed requirements

| Decision | Value |
|---|---|
| HackRF role | Monitoring **and** auto-tune source (auto-tune itself is sub-project 3) |
| Scan target | **Open search** — detect any active video transmitter, no pre-known channel list |
| Reception reality | rx5808 demodulates **analog** only → analog detections are receivable; **digital** detections are **flagged only**, no picture |
| Bands | **1.2 / 2.4 / 5.8 GHz** (all three scanned by HackRF) |
| Simultaneous feeds (later) | 3–4 per node across different bands (drives SP2/SP3, not SP1) |
| Detection latency | **5–10 s acceptable** — prioritize accuracy/sensitivity over speed |
| Detection approach | **Hybrid (C)**: `hackrf_sweep` for occupancy + short IQ dwell per candidate for classification |
| Pi software stack | **Python** (RF ecosystem, `numpy`; `spidev` reserved for SP2) |
| Server changes | **None** for SP1 — telemetry hook already stores arbitrary JSON per device |

## 3. Architecture & data flow

One Python daemon owns the single HackRF exclusively. The device is half-duplex, so sweep and
dwell run **sequentially**, looping within the 5–10 s budget:

```
                ┌─────────────────────── scan cycle (≈5–10 s) ───────────────────────┐
  hackrf_sweep ──▶ spectrum frames ──▶ detector ──▶ candidate carriers
   (1.2/2.4/5.8)     (freq,power CSV)   (peaks over    (center,bw,power)
                                         noise floor)        │
                                                             ▼  (retune same HackRF)
                          classifier ◀── features ◀── dweller: hackrf_transfer IQ block
                          (analog/digital/unknown,     (short capture per candidate)
                           confidence, refined bw)
                                  │
                                  ▼
                    reporter ──▶ POST /api/telemetry/<scanner-id>  (server, best-effort)
                            └──▶ local state file + JSON endpoint  (for SP2/SP3)
```

- The single HackRF is a shared resource guarded by one owner loop: sweep runs, stops, dwell
  runs on each candidate, then the cycle repeats. Sweep and transfer never run concurrently.
- Server reporting is **best-effort**: the local state file is written first; the scan loop never
  blocks on the network. If the server is down the scanner keeps scanning and serving locally.

## 4. Components

Each is a small, independently testable module.

| Module | Responsibility | Depends on |
|---|---|---|
| `config` | Band plan (segments per band), thresholds, sweep params, cadence, server URL/token, scanner-id, source mode (live/replay) | — |
| `sweeper` | Run `hackrf_sweep` over configured segments; assemble power-spectrum frames. Replayable from recorded CSV. | hackrf_sweep |
| `detector` | Spectrum frame → candidate carriers (threshold above estimated noise floor + min bandwidth) | numpy |
| `dweller` | Per candidate: short IQ capture via `hackrf_transfer`; compute PSD + features. Replayable from recorded IQ `.bin`. | hackrf_transfer, numpy |
| `classifier` | Features → `{class, confidence, refined center/bw}` (heuristic thresholds, no ML) | numpy |
| `channel_map` | Map center freq → nearest known FPV channel label (1.2/2.4/5.8 tables); informational only | — |
| `reporter` | Build payload; write local state file; serve local JSON; POST to telemetry hook (best-effort) | requests |
| `main` | Orchestrate the cycle, own the HackRF, backoff/restart on failure | all |

## 5. Detection & classification

### 5.1 Sweep + detection

- Three targeted `hackrf_sweep` invocations (one per band range in §6), ~100 kHz FFT bins, a few
  sweeps averaged for stability.
- Noise floor estimated per band (e.g. median of bins). A **candidate carrier** = a contiguous run
  of bins above `noise_floor + snr_threshold_db` whose width ≥ `min_bandwidth_mhz`.
- Each candidate: `center_mhz`, rough `bandwidth_mhz`, peak `power_dbm`, `snr_db`.

### 5.2 Dwell + classification

- For each candidate (strongest-first), tune HackRF and capture a short IQ block (~0.1 s at
  20 MS/s) via `hackrf_transfer`. Compute a Welch PSD and these features:

| Feature | Analog FPV (FM video) | Digital (DJI/HDZero/Walksnail) |
|---|---|---|
| Occupied bandwidth | ~16–27 MHz | ~20 / 40 MHz, flat "table-top" |
| Spectral flatness (Wiener entropy) | **low** (peaky) | **high** (noise-like) |
| Central carrier spike (max-bin ÷ in-band median) | **high** (dominant carrier) | low |

- Decision: flat + wide + no spike → **digital**; peaky + central spike + BW in analog range →
  **analog**; otherwise → **unknown**. `confidence` derives from how cleanly the features clear
  the thresholds. All thresholds in `config`, tuned against real Pi captures.

### 5.3 Budget handling

If a cycle has more candidates than fit the 5–10 s budget, process **strongest-first** and `log`
what was deferred to the next cycle — **no silent truncation**.

## 6. Band plan (config defaults, all overridable)

| Band | Sweep range | Notes |
|---|---|---|
| 1.2G | 1080–1360 MHz | analog video |
| 2.4G | 2370–2510 MHz | analog video; overlaps Wi-Fi → expect digital/"unknown" clutter |
| 5.8G | 5645–5945 MHz | all FPV channels incl. Raceband |

## 7. Output schema

Single detection:

```json
{
  "ts": 1718530000,
  "band": "5.8G",            // 1.2G | 2.4G | 5.8G
  "center_mhz": 5800,
  "bandwidth_mhz": 22,
  "power_dbm": -47,
  "snr_db": 28,
  "class": "analog",         // analog | digital | unknown
  "confidence": 0.82,
  "channel": "F4"            // nearest known channel label, optional
}
```

Full payload (local state file and POST body):

```json
{
  "scanner_id": "scan-01",
  "ts": 1718530000,
  "detections": [ /* … */ ],
  "occupancy": { "1.2G": 0.0, "2.4G": 0.3, "5.8G": 0.6 },   // busy fraction per band
  "spectrum": { "5.8G": [ /* downsampled PSD for a future waterfall */ ] }
}
```

## 8. Reporting & integration

- **No server code change for SP1.** The dashboard telemetry hook (`POST
  /api/telemetry/<id>`) already stores arbitrary JSON per device and exposes it; the scanner POSTs
  the payload above under its `scanner_id`. Rendering (waterfall, detection list) is sub-project 4.
- The scanner also writes the same payload to a **local state file** and exposes a small **local
  JSON endpoint**, so sub-projects 2/3 (reception, auto-tune) can consume detections on-Pi without
  the server.

## 9. Error handling

- **HackRF absent/busy** → catch nonzero exit, mark status `scanner_offline`, exponential backoff
  (cap ~30 s).
- **Subprocess hang** → per-process timeout, kill, restart.
- **Single-owner mutual exclusion** → never run sweep + transfer concurrently; confirm the prior
  process exited before starting the next.
- **Server unreachable** → write local state file first, then POST in try/except with a short
  timeout; the scan loop never blocks on the network.
- **Backstop** → `systemd Restart=always`.

## 10. Testing

Dev box is Windows with no HackRF, so a **replay mode** is built in from day one: `sweeper` and
`dweller` read recorded fixtures (saved `hackrf_sweep` CSV; saved IQ `.bin`) instead of spawning
hardware, selected by `config`. The full pipeline is TDD-able on the dev machine / CI with zero
hardware.

- **Unit:** parser (CSV→spectrum), detector (synthetic peaks→candidates; noise→none), classifier
  (labeled analog/digital/empty fixtures→class + threshold boundaries), channel_map (freq→label),
  reporter (server-down path still writes local state, never throws).
- **Integration:** replay end-to-end → expected detections JSON.
- **Live smoke (on the Pi):** `hackrf_info` OK; one real cycle prints detections; a known 5.8 GHz
  VTX is detected and classified `analog`.
- **Fixtures:** sweep CSV + a few IQ grabs (analog VTX, a digital system if available, empty band)
  recorded once on the Pi and committed as test data.

## 11. Deliverables (file list)

```
agent/scan/
  config.py            # band plan, thresholds, sweep params, cadence, scanner-id, source mode
  sweeper.py           # hackrf_sweep driver + CSV parser (live + replay)
  detector.py          # spectrum → candidate carriers
  dweller.py           # hackrf_transfer IQ capture + PSD/features (live + replay)
  classifier.py        # features → class/confidence
  channel_map.py       # freq → nearest FPV channel label
  reporter.py          # local state file + local JSON endpoint + best-effort POST
  main.py              # orchestration loop, HackRF ownership, backoff
  requirements.txt
  tests/
    fixtures/          # recorded sweep CSV + IQ .bin (committed)
    test_*.py
systemd/
  fpv-scan.service     # Restart=always
README (scan section)  # install hackrf tools, run, record fixtures, tune thresholds
```

## 12. Out of scope (YAGNI for SP1)

- Any reception / rx5808 / grabber / encoding (sub-project 2).
- Auto-tune closed loop (sub-project 3).
- Dashboard rendering of spectrum/detections (sub-project 4).
- ML-based classification (heuristic thresholds are sufficient to "flag").
- Multi-HackRF / multiple SDRs per node.

## 13. Assumptions / open items

- HackRF tools (`hackrf_sweep`, `hackrf_transfer`, `hackrf_info`) installable on the Pi.
- One HackRF per scan node; sweep and dwell are time-shared on it.
- 20 MS/s dwell window (~20 MHz) is enough to classify ~16–27 MHz analog signals (captures the
  central carrier + most energy); revisit if classification accuracy is insufficient.
- Classification thresholds are seeded from literature and **tuned against real Pi captures**
  during the live-smoke phase.
- `scanner_id` is configured per node; the server accepts it via the existing telemetry hook.
```