import json

import pytest

from models import Detection
import publisher


def _det():
    return Detection(
        ts=1, band="5.8G", center_mhz=5800.0, bandwidth_mhz=22.0, power_dbm=-47.0,
        snr_db=28.0, signal_class="analog", confidence=0.8, channel="F4",
    )


def test_build_spectrum_frame_is_self_describing():
    f = publisher.build_spectrum_frame("hackrf", 100, "5.8G", 5645.0, 5945.0, [-90.0, -50.0])
    assert f["scanner_id"] == "hackrf"
    assert f["ts"] == 100
    assert len(f["bands"]) == 1
    b = f["bands"][0]
    assert b["id"] == "5.8G"
    assert b["low_mhz"] == 5645.0
    assert b["high_mhz"] == 5945.0
    assert b["psd"] == [-90.0, -50.0]


def test_build_detection_payload_shape():
    p = publisher.build_detection_payload("hackrf", 100, [_det()], {"5.8G": 0.5})
    assert p["scanner_id"] == "hackrf"
    assert p["ts"] == 100
    assert p["detections"][0]["class"] == "analog"     # to_dict() emits "class"
    assert p["occupancy"] == {"5.8G": 0.5}
