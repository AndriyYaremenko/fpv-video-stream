import json
import os
from typing import Dict, List

import requests

from models import Detection


def build_payload(
    scanner_id: str,
    ts: int,
    detections: List[Detection],
    occupancy: Dict[str, float],
    spectrum: Dict[str, list],
) -> dict:
    return {
        "scanner_id": scanner_id,
        "ts": ts,
        "detections": [d.to_dict() for d in detections],
        "occupancy": occupancy,
        "spectrum": spectrum,
    }


def write_state(path: str, payload: dict) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    os.replace(tmp, path)


def post_telemetry(url: str, token: str, scanner_id: str, payload: dict, timeout: float = 3.0) -> bool:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    endpoint = f"{url.rstrip('/')}/api/telemetry/{scanner_id}"
    try:
        requests.post(endpoint, json=payload, headers=headers, timeout=timeout)
        return True
    except Exception:
        return False
