import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List

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


class Holder:
    """Mutable container for the latest payload, shared with the local HTTP server."""

    def __init__(self) -> None:
        self.payload: dict = {}


def make_local_server(host: str, port: int, holder: Holder) -> ThreadingHTTPServer:
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(holder.payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass  # silence default stderr access logging

    return ThreadingHTTPServer((host, port), _Handler)
