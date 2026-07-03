# bladeRF Scanner (Phase 1) — Design

**Date:** 2026-07-03
**Status:** Draft for review
**Supersedes on this node:** the HackRF SDR role in `agent/scan/` (HackRF kept as cold reserve, not the active SDR).

## Goal

Replace HackRF with a **bladeRF 2.0 micro** as the wideband scanner on the Pi 5 node: sweep 1.2 / 2.4 / 5.8 GHz, detect carriers, classify them (analog-video / digital / control), and publish to the **existing** MQTT topics (`fpv/<id>/spectrum`, `fpv/<id>/detection`). No transmit. No video demodulation yet — that is Phase 2.

This is a **backend swap**: the acquisition layer changes from `hackrf_sweep` to bladeRF; the detector, classifier, publisher, reporter, and RX5808 controller stay as-is (thresholds may be retuned).

## Why bladeRF replaces HackRF

- **Reaches all bands cleanly:** 47 MHz–6 GHz, up to 56 MHz usable RF bandwidth, full-duplex — confirmed `bladeRF 2.0 micro` (USB 3, `2cf0:5250`) on the Pi 5.
- **No USB wedge:** the HackRF's chronic `hackrf_sweep` hangs are gone; bladeRF streams over USB 3 reliably.
- **One acquisition layer for both phases:** capturing IQ directly (not just power) means Phase 2 (frame demod) reuses the same captures — see [[Phase 2]] below.
- **Power:** removing the HackRF as an active consumer eases the node's tight 5 V budget (see the under-voltage history in the deploy notes).

## Scope

**In scope (Phase 1):**
- bladeRF acquisition module producing a power spectrum per tuning window.
- Window plan covering 1.2 / 2.4 / 5.8 GHz.
- Selectable SDR backend so HackRF code stays intact as reserve.
- Reuse of detection → classification → publish → RX5808 auto-tune.
- systemd + host-package changes on the Pi 5 node.

**Out of scope (later phases, noted for context only):**
- **Phase 2:** wideband-FM + CVBS demodulation of one mono frame per N seconds → `fpv/<id>/video`.
- **Phase 3 (optional):** cross-band relay — receive 1.2/2.4 GHz video, retransmit into 5.8 A1 (5865) for the RX5808, for continuous live H.264 of a non-5.8 target. Only if software frames prove insufficient. Needs TX antenna + filtering + likely a 27 W Pi PSU.

## Architecture

```
bladeRF 2.0 micro (USB3)
  └─ bladerf_source.py            NEW  — libbladeRF: tune per window, capture IQ, FFT → power rows
        │  (freq_hz, power_db) rows, same shape the pipeline already consumes
        ▼
  sweeper / detector / classifier  REUSED — occupancy, carriers, analog-video vs digital vs control
        ▼
  publisher.py                     REUSED — MQTT: fpv/<id>/spectrum + fpv/<id>/detection
        ▼
  ┌─ dashboard (spectrum, detection, journal)
  └─ Rx5808Controller              REUSED — auto-tunes RX5808 to detected 5.8 carriers (unchanged)
```

The only new code is `bladerf_source.py` plus a thin selection seam. Everything downstream is untouched.

### Components

| File | Change | Responsibility |
|---|---|---|
| `agent/scan/bladerf_source.py` | **new** | Own the bladeRF device: configure sample rate / bandwidth / gains; iterate the window plan; per window capture an IQ block and compute a power spectrum (Welch/FFT); yield `(freq_hz, power_db)` rows. Expose `capture_iq(center, span, nsamps)` for Phase 2. Never raise into callers; degrade to "no data" on device error. |
| `agent/scan/config.py` | modify | Add `SCAN_SDR` (`hackrf`\|`bladerf`, default `bladerf`), bladeRF gains, sample rate, per-band window plan, per-window dwell. |
| `agent/scan/main.py` | modify | Select the acquisition source by `SCAN_SDR`; wire it into the existing sweeper/detector; RX5808 wiring unchanged. |
| `agent/scan/device.py` | modify | Skip HackRF-specific USB reset when `SCAN_SDR=bladerf`. |
| `agent/scan/sweeper.py` / `dweller.py` | modify (small) | Consume power rows / dwell IQ from the selected source behind a minimal interface (`iter_power_rows()`, `dwell_iq()`), so HackRF and bladeRF are interchangeable. |
| `systemd/fpv-scan.service` (+ Pi unit) | modify | `SCAN_ID=bladerf`, `SCAN_SDR=bladerf`. Install `libbladeRF` + Python bindings + FPGA bitstream on the Pi. |

Reused unchanged: `detector.py`, `classifier.py`, `publisher.py`, `reporter.py`, `models.py`, `rx5808*.py`, `video_emit.py` (Phase-2 will extend it).

## Data flow & coverage

- **Window plan** (usable ~40–50 MHz per tune to leave filter margin):
  - 1.2G 1080–1360 MHz → ~6 windows
  - 2.4G 2370–2510 MHz → ~3 windows
  - 5.8G 5645–5945 MHz → ~7 windows
  - ≈ 16 windows/cycle. Budget per-window dwell so a full cycle stays well under `SCANNER_FRESH_MS` (60 s) — target a full sweep in a few seconds, matching the current cadence so the dashboard keeps the scanner "online".
- **Classification** reuses the existing bandwidth-based rules (analog video ≈ 10–30 MHz occupied). bladeRF's cleaner spectrum may allow tighter thresholds; retune against recorded captures rather than guessing.
- **RX5808 auto-tune** consumes detected 5.8 carriers exactly as today (no change) — operators still get live H.264 of a chosen 5.8 target via the grabber.

## Acquisition method — RECOMMENDATION (please confirm at review)

**Recommended: direct libbladeRF via its Python bindings (`bladerf` module).** We tune each window, capture an IQ block, and compute the power spectrum in-process (NumPy FFT). Rationale: full control, no external sweep process, and — decisively — **the same IQ capture feeds Phase 2's demod**, so we build the acquisition layer once.

**Alternative considered:** `soapy_power` (SoapySDR + SoapyBladeRF) as a drop-in `hackrf_sweep`-style source. Faster to bootstrap for pure sweeping, but it only yields power (no IQ), so Phase 2 would need a second capture path. Rejected for the duplication.

## Testing

- **Replay/unit (host, no hardware):** reuse the existing `SCAN_SOURCE=replay` fixture harness. Add recorded bladeRF power rows / IQ captures as fixtures; assert the detector/classifier produce the expected detections and classes. Pure functions (FFT→power, window plan) get direct unit tests.
- **On-Pi smoke:** `bladeRF-cli -e info` (device up, FPGA loaded); run one real sweep cycle; confirm `fpv/bladerf/spectrum` + `detection` flow on the broker and the scanner shows online on the dashboard.
- **Threshold tuning:** capture a known analog VTX on each band, verify it classifies as analog-video and the RX5808 auto-tunes to it (5.8).

## Risks

- **Python binding on Debian 13 / Py 3.13:** validate the `bladerf` bindings install (apt `libbladerf` + bindings, or build); confirm FPGA bitstream present. Fallback: SoapySDR path.
- **USB 3 throughput / buffering:** high sample rates over USB 3 on the Pi 5 need careful buffer sizing; decimate where full 56 MHz isn't needed.
- **Gain calibration:** detection thresholds are gain-dependent; retune LNA/VGA against real captures.
- **Power in TX (Phase 3 only):** not a Phase-1 concern.

## Open decisions for review

1. **Scanner id:** register a **new `bladerf`** scanner (recommended — the device genuinely changed; the old `hackrf` detection-journal history stays as history), or keep `SCAN_ID=hackrf` for continuity?
2. **Acquisition method:** confirm direct libbladeRF (recommended) vs soapy_power.
3. **HackRF disposition:** unplug it (frees USB/power) or leave it attached as an idle reserve?
