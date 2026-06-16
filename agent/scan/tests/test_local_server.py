import json
import threading
import urllib.request

import reporter


def test_local_server_serves_latest_payload():
    holder = reporter.Holder()
    holder.payload = {"scanner_id": "scan-01", "detections": []}
    srv = reporter.make_local_server("127.0.0.1", 0, holder)   # port 0 -> ephemeral
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/scan.json", timeout=3) as r:
            data = json.loads(r.read().decode("utf-8"))
        assert data["scanner_id"] == "scan-01"
        # serves the LATEST payload, not a one-time snapshot
        holder.payload = {"scanner_id": "scan-02", "detections": []}
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/scan.json", timeout=3) as r:
            data2 = json.loads(r.read().decode("utf-8"))
        assert data2["scanner_id"] == "scan-02"
    finally:
        srv.shutdown()
        srv.server_close()
