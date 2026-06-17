import json
import logging
import time

LOG = logging.getLogger("scan.publisher")


def build_spectrum_frame(scanner_id, ts, band_id, low_mhz, high_mhz, psd):
    """Self-describing single-band spectrum frame (SP-A contract)."""
    return {
        "scanner_id": scanner_id,
        "ts": ts,
        "bands": [{"id": band_id, "low_mhz": low_mhz, "high_mhz": high_mhz, "psd": psd}],
    }


def build_detection_payload(scanner_id, ts, detections, occupancy):
    """Detection event payload (SP-A contract)."""
    return {
        "scanner_id": scanner_id,
        "ts": ts,
        "detections": [d.to_dict() for d in detections],
        "occupancy": occupancy,
    }
