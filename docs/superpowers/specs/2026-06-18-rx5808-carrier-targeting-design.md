# RX5808 Carrier Targeting (tune to any strong 5.8 carrier) — Design Spec

**Date:** 2026-06-18
**Author:** andriy@netlife.com.ua
**Status:** Approved (pending written-spec review)

## 1. Context & Goal

The RX5808 auto-tune path is proven end-to-end: a manual `set_frequency(A1/5865)` made the grabber
stream show live FPV video — wiring, bit-bang SPI, and the grabber all work.

But the RX5808 controller only locks onto a channel when `run_cycle` feeds it a 5.8 detection with
`signal_class == "analog"`, and the HackRF detector never flags real FPV carriers: they are **narrow
(~1–2 MHz)**, while `find_candidates` requires a **≥5 MHz** contiguous run above noise+20 dB and
`classify` requires an occupied bandwidth of **10–30 MHz** for "analog". So the controller stays in
scan mode and never settles on the signal.

**Goal:** for RX5808 targeting, treat **any strong carrier on the 5.8 band** as a tune target —
regardless of bandwidth or the wide-analog-video classification. The RX5808 demodulates whatever is
there; the human judges. This is a narrow change: feed the controller from `find_candidates` run with
**looser, RX5808-specific thresholds**, leaving the main detection/classification and the dashboard
untouched.

## 2. Confirmed decisions

| Decision | Value |
|---|---|
| Targeting source | Reuse `find_candidates(spec, snr, min_bw)` on the **5.8 spectrum** with looser thresholds; feed the candidate centers to `controller.update_targets`. |
| Thresholds | `RX5808_CARRIER_SNR_DB` = **15.0** dB, `RX5808_CARRIER_MIN_BW_MHZ` = **0.5** MHz (the observed carrier was ~47 dB SNR / ~1.2–1.7 MHz; single-bin noise ≈0.1 MHz is rejected). Configurable via env. |
| Replaces | The previous `signal_class == "analog"` feed. The carrier finder is a **superset** (catches narrow carriers AND wide analog video), so the analog-only feed is dropped. |
| Untouched | Main detection (`find_candidates` with `snr_threshold_db`/`min_bandwidth_mhz`), `classify`, the `detections` payload, and the dashboard RF classification — all unchanged. The carrier finder is RX5808-only. |
| Bands | 5.8 only (RX5808 is 5.8). |
| Prerequisite (hardware, user) | The HackRF must stop wedging for ANY auto-targeting to work (no spectrum → no carriers). Almost certainly power: RX5808 + grabber must move to a separate 5V/powered hub (user will do this later). Not a code change. |

## 3. Architecture & data flow

```
run_cycle, 5.8 band iteration:
  spec = _get_spectrum(cfg, "5.8G", ...)              (full-res spectrum, already computed)
  ... (main detection unchanged: find_candidates(spec, snr_threshold_db, min_bandwidth_mhz) -> dwells -> classify) ...
  rx_carriers = find_candidates(spec, cfg.rx5808_carrier_snr_db, cfg.rx5808_carrier_min_bw_mhz)   # looser
  rx_carrier_centers = [c.center_mhz for c in rx_carriers]
        │
after the band loop:
  controller.update_targets(rx_carrier_centers)       # was: analog-classified 5.8 centers
        │
  Rx5808Controller maps each to nearest RX5808 channel, dedups, and (detected mode) round-robins them;
  one carrier -> locks; several -> cycles. None -> scan mode (all 40), unchanged.
```

`find_candidates` is already imported in `main.py`; the second call is a cheap mask + run-length scan
over the in-memory 5.8 spectrum (no extra dwell/capture). The controller, MQTT `rxtune`, and dashboard
are unchanged.

## 4. Components / deliverables

```
agent/scan/config.py             (change: + rx5808_carrier_snr_db, + rx5808_carrier_min_bw_mhz + env)
agent/scan/main.py               (change: run_cycle computes 5.8 carriers, feeds controller from them)
agent/scan/tests/test_config.py  (change: + carrier-threshold defaults/env)
agent/scan/tests/test_run_cycle.py (change: controller fed 5.8 carriers regardless of signal class)
```

### 4.1 `config.py`
Add: `rx5808_carrier_snr_db: float = 15.0` (`RX5808_CARRIER_SNR_DB`), `rx5808_carrier_min_bw_mhz:
float = 0.5` (`RX5808_CARRIER_MIN_BW_MHZ`).

### 4.2 `main.py` `run_cycle`
- Before the band loop: `rx_carrier_centers = []`.
- Inside the loop, when `band == "5.8G"` (after computing `spec` and the main candidates):
  `rx_carrier_centers = [c.center_mhz for c in find_candidates(spec, cfg.rx5808_carrier_snr_db, cfg.rx5808_carrier_min_bw_mhz)]`.
- Replace the existing controller feed (the analog-class filter) with:
  `controller.update_targets(rx_carrier_centers)` (still guarded).

## 5. Testing / verification

- **`test_config.py`:** carrier-threshold defaults (15.0 / 0.5) + env overrides.
- **`test_run_cycle.py`:** replace the two RX5808 feed tests:
  - the controller is fed the 5.8 carrier center(s) from the fixture's strong 5.8 signal (~5800);
  - the feed is **independent of `classify`** — with `classify` monkeypatched to `"digital"`, the
    controller is **still** fed the carrier (proving decoupling from the analog classification).
- Run: `cd agent/scan && python -m pytest tests ../video/tests -q` (all green).
- **Live (after deploy + HackRF power fixed):** with a 5.8 TX present, `fpv/hackrf/rxtune` shows
  `mode=detected` on the TX's channel (not scan), the RX5808 locks, the grabber stream stays on the
  channel, and the dashboard marker settles on the carrier frequency.

## 6. Out of scope

- HackRF power / flaky-USB fix (hardware; user moves RX5808+grabber to separate 5V).
- Any change to the main detection thresholds, `classify`, the `detections`/dashboard RF view, the
  `Rx5808Controller`, the MQTT `rxtune` contract, or the IQ-frame emitter.
