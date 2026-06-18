# agent/video — analog FPV IQ → luma frame → MQTT

One-shot CLI: take a HackRF int8 IQ capture of a suspected analog FPV video carrier,
confirm it is PAL/NTSC, reconstruct a monochrome (luma) frame, and publish a PNG
thumbnail to `fpv/<scanner_id>/video` over MQTT (WireGuard). Only a PNG (tens of KB)
crosses the tunnel — never raw IQ.

Digital FPV (DJI/HDZero/Walksnail OFDM) and color decode are out of scope.

## Run

    # from the pipeline, right after hackrf_transfer wrote cap.iq:
    cd agent/scan && .venv/bin/python ../video/iq_video.py \
        --iq cap.iq --fs 16e6 --center 5800e6 --std auto

- `--fs` defaults to `FPV_DEFAULT_FS` (16e6). `--center` is metadata only.
- `--std auto` (default) picks PAL/NTSC by the measured line rate; force with `pal`/`ntsc`.
- Degraded mode (Pi Zero 2): `--fs 10e6` and one short capture.

### Exit codes
- `0` — frame published to MQTT.
- `2` — `not_video` (no line-sync gate): nothing published, no PNG written.
- `1` — error, **or** broker unreachable after the frame was built (the full-res PNG
  is still saved to `FPV_FRAMES_DIR`, a warning is logged).

## MQTT contract

Topic `fpv/<scanner_id>/video`, QoS 1, retained:

    { "scanner_id": "scan-01", "ts": 1718700000.0, "center_mhz": 5800.0,
      "standard": "PAL", "line_hz": 15625, "sync_snr_db": 18.3,
      "frame_png_b64": "<base64 PNG thumbnail, <=320 px>" }

The one-shot publisher never writes `fpv/<id>/status`, so it does not disturb the
scan service's presence for the same `scanner_id`.

## Config

MQTT host/port/creds + `scanner_id` are reused from the scan service
(`agent/scan/config.py`: `SCAN_ID`, `SCAN_MQTT_HOST`, `MQTT_PUB_USER`, `MQTT_PUB_PASS`).
Video DSP/IO knobs via env: `FPV_FRAMES_DIR` (default `/var/lib/fpv/frames`),
`FPV_FRAME_WIDTH`, `FPV_THUMB_MAX_WIDTH`, `FPV_LPF_CUTOFF_HZ`, `FPV_LINE_SNR_DB`,
`FPV_HARM_SNR_DB`, `FPV_DEFAULT_FS`.

## Install

    pip install -r agent/scan/requirements.txt   # adds Pillow to the shared scan venv

Ensure `FPV_FRAMES_DIR` is writable by the service user.

## Test (no hardware)

    cd agent/scan && python -m pytest ../video/tests -q

`synth.py` generates synthetic PAL/NTSC CVBS → FM → int8 IQ to drive the whole
pipeline end-to-end.

## Pipeline integration (out of scope here)

Wiring this CLI into `main.py`'s scan loop (writing `cap.iq` per candidate and shelling
out to `iq_video.py`), and dashboard consumption of `fpv/<id>/video`, are separate tasks.
