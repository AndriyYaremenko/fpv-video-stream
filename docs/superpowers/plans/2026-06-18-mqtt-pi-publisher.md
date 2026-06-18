# Pi Scan Service → MQTT Publisher (SP-B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Pi scan service publish scan data to the MQTT broker (over WireGuard) on the SP-A topic contract, replacing its HTTP telemetry POST — keeping the local state file + local HTTP debug server.

**Architecture:** A new `publisher.py` wraps `paho-mqtt` (background loop, auto-reconnect, LWT presence) with pure, testable payload builders. `run_cycle` publishes a self-describing spectrum frame per band right after that band's sweep (QoS0, retained) and one detection message per cycle (QoS1, retained); `main()` wires the publisher. The dashboard HTTP POST (`post_telemetry`) and the `requests` dep are removed.

**Tech Stack:** Python 3, `paho-mqtt>=2.0`, `numpy`, `pytest`.

Spec: `docs/superpowers/specs/2026-06-18-mqtt-pi-publisher-design.md`

**Test command (this Windows dev box):** run from the repo root —
`agent/scan/.venv/Scripts/python.exe -m pytest agent/scan/tests -q`
(the scan venv has numpy + pytest; `paho-mqtt` is NOT required to run the tests — `publisher.py` imports paho lazily and tests inject a fake client).

---

## File Structure

```
agent/scan/publisher.py              (new: pure builders + MqttPublisher)
agent/scan/config.py                 (change: MQTT fields + env; remove server_url/server_token)
agent/scan/main.py                   (change: run_cycle publishes; main() wires publisher; drop post_telemetry)
agent/scan/reporter.py               (change: remove post_telemetry + `import requests`)
agent/scan/requirements.txt          (change: +paho-mqtt, -requests)
agent/scan/tests/test_config.py      (change: drop SCAN_SERVER_URL; add MQTT env test)
agent/scan/tests/test_publisher.py   (new: builders + client topic/qos/retain + guards)
agent/scan/tests/test_run_cycle.py   (change: inject fake publisher; drop server_url)
agent/scan/tests/test_reporter.py    (change: remove post_telemetry test)
.gitignore                           (change: ignore __pycache__/ + *.pyc)
```

---

## Task 1: Config MQTT fields + env (remove server_*)

**Files:**
- Modify: `agent/scan/config.py`
- Test: `agent/scan/tests/test_config.py`

- [ ] **Step 1: Update the failing tests**

In `agent/scan/tests/test_config.py`, replace `test_env_overrides` (lines 15-26) with the version below (drops `SCAN_SERVER_URL`/`server_url`, adds the MQTT env) and append a new `test_mqtt_config`:

```python
def test_env_overrides():
    env = {
        "SCAN_ID": "scan-09",
        "SCAN_SOURCE": "replay",
        "SCAN_FIXTURES_DIR": "/tmp/fx",
    }
    c = load_config(env)
    assert c.scanner_id == "scan-09"
    assert c.source == "replay"
    assert c.fixtures_dir == "/tmp/fx"


def test_mqtt_config():
    c = load_config({})
    assert c.mqtt_enabled is True
    assert c.mqtt_host == "10.8.0.1"
    assert c.mqtt_port == 1883
    assert c.mqtt_user == "pub"
    assert c.mqtt_pass == ""
    c2 = load_config({
        "SCAN_MQTT_HOST": "10.8.0.9", "SCAN_MQTT_PORT": "1884",
        "MQTT_PUB_USER": "pi", "MQTT_PUB_PASS": "s3cret",
        "SCAN_MQTT_ENABLED": "0",
    })
    assert c2.mqtt_host == "10.8.0.9"
    assert c2.mqtt_port == 1884
    assert c2.mqtt_user == "pi"
    assert c2.mqtt_pass == "s3cret"
    assert c2.mqtt_enabled is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `agent/scan/.venv/Scripts/python.exe -m pytest agent/scan/tests/test_config.py -q`
Expected: FAIL — `c.mqtt_enabled`/`mqtt_host`/etc. don't exist (AttributeError).

- [ ] **Step 3: Implement the config changes**

In `agent/scan/config.py`, in the `Config` dataclass, **remove** these two lines:

```python
    server_url: str = "http://10.8.0.1:8080"
    server_token: str = ""
```

and add (place them where `server_url`/`server_token` were, before `source`):

```python
    mqtt_enabled: bool = True
    mqtt_host: str = "10.8.0.1"
    mqtt_port: int = 1883
    mqtt_user: str = "pub"
    mqtt_pass: str = ""
    mqtt_keepalive: int = 60
```

In `load_config`, **remove** these two lines:

```python
    c.server_url = env.get("SCAN_SERVER_URL", c.server_url)
    c.server_token = env.get("SCAN_SERVER_TOKEN", c.server_token)
```

and add (right after the `c.source = ...` line):

```python
    c.mqtt_host = env.get("SCAN_MQTT_HOST", c.mqtt_host)
    if "SCAN_MQTT_PORT" in env:
        c.mqtt_port = int(env["SCAN_MQTT_PORT"])
    c.mqtt_user = env.get("MQTT_PUB_USER", c.mqtt_user)
    c.mqtt_pass = env.get("MQTT_PUB_PASS", c.mqtt_pass)
    if "SCAN_MQTT_KEEPALIVE" in env:
        c.mqtt_keepalive = int(env["SCAN_MQTT_KEEPALIVE"])
    if "SCAN_MQTT_ENABLED" in env:
        c.mqtt_enabled = env["SCAN_MQTT_ENABLED"].strip().lower() not in ("0", "false", "no", "")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `agent/scan/.venv/Scripts/python.exe -m pytest agent/scan/tests/test_config.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/scan/config.py agent/scan/tests/test_config.py
git commit -m "feat(scan): MQTT broker config (host/port/user/pass), drop server_url" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Pure payload builders in publisher.py

**Files:**
- Create: `agent/scan/publisher.py`
- Test: `agent/scan/tests/test_publisher.py`

- [ ] **Step 1: Write the failing tests**

Create `agent/scan/tests/test_publisher.py`:

```python
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `agent/scan/.venv/Scripts/python.exe -m pytest agent/scan/tests/test_publisher.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'publisher'`.

- [ ] **Step 3: Create `agent/scan/publisher.py` with the builders**

```python
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
```

- [ ] **Step 4: Run to verify they pass**

Run: `agent/scan/.venv/Scripts/python.exe -m pytest agent/scan/tests/test_publisher.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add agent/scan/publisher.py agent/scan/tests/test_publisher.py
git commit -m "feat(scan): MQTT payload builders (self-describing spectrum + detection)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: MqttPublisher (connect/LWT/status + guarded publish)

**Files:**
- Modify: `agent/scan/publisher.py`
- Test: `agent/scan/tests/test_publisher.py`

- [ ] **Step 1: Append the failing tests**

Append to `agent/scan/tests/test_publisher.py`:

```python
class FakeClient:
    def __init__(self):
        self.published = []          # list of (topic, payload, qos, retain)
        self.will = None             # (topic, payload, qos, retain)
        self.creds = None            # (user, pass)
        self.connected_to = None     # (host, port, keepalive)
        self.on_connect = None
        self.loop_started = False

    def username_pw_set(self, user, password):
        self.creds = (user, password)

    def will_set(self, topic, payload, qos=0, retain=False):
        self.will = (topic, payload, qos, retain)

    def connect(self, host, port, keepalive=60):
        self.connected_to = (host, port, keepalive)

    def loop_start(self):
        self.loop_started = True

    def loop_stop(self):
        self.loop_started = False

    def disconnect(self):
        pass

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))


def _pub(fake):
    return publisher.MqttPublisher(
        "10.8.0.1", 1883, "pub", "pw", "hackrf", client_factory=lambda cid: fake
    )


def test_connect_sets_lwt_and_creds_and_starts_loop():
    fake = FakeClient()
    p = _pub(fake)
    p.connect(ts=100)
    topic, payload, qos, retain = fake.will
    assert topic == "fpv/hackrf/status"
    assert json.loads(payload) == {"online": False, "ts": 100}
    assert qos == 1 and retain is True
    assert fake.creds == ("pub", "pw")
    assert fake.connected_to == ("10.8.0.1", 1883, 60)
    assert fake.loop_started is True


def test_on_connect_publishes_online_status():
    fake = FakeClient()
    p = _pub(fake)
    p.connect(ts=100)
    p._on_connect(fake, None, None, 0)          # simulate the broker connack
    status = [m for m in fake.published if m[0] == "fpv/hackrf/status"]
    assert status, "expected an online status publish"
    topic, payload, qos, retain = status[-1]
    assert json.loads(payload)["online"] is True
    assert qos == 1 and retain is True


def test_publish_spectrum_topic_qos_retain():
    fake = FakeClient()
    p = _pub(fake); p.connect(ts=1)
    p.publish_spectrum(200, "2.4G", 2370.0, 2510.0, [-80.0])
    msg = [m for m in fake.published if m[0] == "fpv/hackrf/spectrum"][-1]
    topic, payload, qos, retain = msg
    assert qos == 0 and retain is True
    body = json.loads(payload)
    assert body["ts"] == 200 and body["bands"][0]["id"] == "2.4G"


def test_publish_detection_topic_qos_retain():
    fake = FakeClient()
    p = _pub(fake); p.connect(ts=1)
    p.publish_detection(300, [_det()], {"5.8G": 0.4})
    msg = [m for m in fake.published if m[0] == "fpv/hackrf/detection"][-1]
    topic, payload, qos, retain = msg
    assert qos == 1 and retain is True
    assert json.loads(payload)["detections"][0]["class"] == "analog"


def test_publish_is_noop_when_not_connected():
    p = publisher.MqttPublisher("h", 1, "u", "p", "hackrf")     # never connect()
    p.publish_spectrum(1, "5.8G", 5645.0, 5945.0, [-90.0])      # must not raise
    p.publish_detection(1, [], {})


def test_publish_swallows_client_errors():
    class BoomClient(FakeClient):
        def publish(self, *a, **k):
            raise RuntimeError("broker down")
    fake = BoomClient()
    p = _pub(fake); p.connect(ts=1)
    p.publish_spectrum(1, "5.8G", 5645.0, 5945.0, [-90.0])      # guarded -> no raise
```

- [ ] **Step 2: Run to verify they fail**

Run: `agent/scan/.venv/Scripts/python.exe -m pytest agent/scan/tests/test_publisher.py -q`
Expected: FAIL — `MqttPublisher` doesn't exist yet (AttributeError).

- [ ] **Step 3: Implement `MqttPublisher`**

Append to `agent/scan/publisher.py`:

```python
def _default_client_factory(client_id):
    # Lazy import: paho is only needed for a real connection, not for tests/builders.
    import paho.mqtt.client as mqtt
    return mqtt.Client(client_id=client_id)


class MqttPublisher:
    QOS_SPECTRUM = 0
    QOS_DETECTION = 1
    QOS_STATUS = 1

    def __init__(self, host, port, user, password, scanner_id, keepalive=60, client_factory=None):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.scanner_id = scanner_id
        self.keepalive = keepalive
        self._factory = client_factory or _default_client_factory
        self._t_spectrum = f"fpv/{scanner_id}/spectrum"
        self._t_detection = f"fpv/{scanner_id}/detection"
        self._t_status = f"fpv/{scanner_id}/status"
        self._client = None

    def connect(self, ts):
        client = self._factory(f"scan-{self.scanner_id}")
        if self.user:
            client.username_pw_set(self.user, self.password)
        client.will_set(
            self._t_status, json.dumps({"online": False, "ts": ts}),
            qos=self.QOS_STATUS, retain=True,
        )
        client.on_connect = self._on_connect
        client.connect(self.host, self.port, keepalive=self.keepalive)
        client.loop_start()
        self._client = client

    def _on_connect(self, client, userdata, flags, rc, *args):
        # (Re)connect -> republish presence so a reconnect restores "online".
        try:
            client.publish(
                self._t_status, json.dumps({"online": True, "ts": int(time.time())}),
                qos=self.QOS_STATUS, retain=True,
            )
        except Exception:
            LOG.exception("status publish failed")

    def publish_spectrum(self, ts, band_id, low_mhz, high_mhz, psd):
        self._publish(
            self._t_spectrum,
            build_spectrum_frame(self.scanner_id, ts, band_id, low_mhz, high_mhz, psd),
            self.QOS_SPECTRUM,
        )

    def publish_detection(self, ts, detections, occupancy):
        self._publish(
            self._t_detection,
            build_detection_payload(self.scanner_id, ts, detections, occupancy),
            self.QOS_DETECTION,
        )

    def _publish(self, topic, payload, qos):
        if self._client is None:
            return
        try:
            self._client.publish(topic, json.dumps(payload), qos=qos, retain=True)
        except Exception:
            LOG.exception("publish to %s failed", topic)

    def close(self, ts):
        if self._client is None:
            return
        try:
            self._client.publish(
                self._t_status, json.dumps({"online": False, "ts": ts}),
                qos=self.QOS_STATUS, retain=True,
            )
            self._client.loop_stop()
            self._client.disconnect()
        except Exception:
            LOG.exception("close failed")
```

- [ ] **Step 4: Run to verify they pass**

Run: `agent/scan/.venv/Scripts/python.exe -m pytest agent/scan/tests/test_publisher.py -q`
Expected: PASS (all builder + client tests).

- [ ] **Step 5: Commit**

```bash
git add agent/scan/publisher.py agent/scan/tests/test_publisher.py
git commit -m "feat(scan): MqttPublisher (LWT presence, guarded QoS publish, injectable client)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Wire run_cycle + main; drop the HTTP POST

**Files:**
- Modify: `agent/scan/main.py`
- Test: `agent/scan/tests/test_run_cycle.py`

- [ ] **Step 1: Update the test**

In `agent/scan/tests/test_run_cycle.py`:

(a) In `_config`, **remove** this line (server_url no longer exists):

```python
    c.server_url = "http://127.0.0.1:1"        # unreachable -> POST silently fails
```

(b) Add a fake publisher above `test_run_cycle_end_to_end` and extend that test to inject it and assert the publishes. Replace `test_run_cycle_end_to_end` with:

```python
class _FakePub:
    def __init__(self):
        self.spectra = []        # (ts, band, low, high, psd)
        self.detections = []     # (ts, detections, occupancy)

    def publish_spectrum(self, ts, band_id, low_mhz, high_mhz, psd):
        self.spectra.append((ts, band_id, low_mhz, high_mhz, psd))

    def publish_detection(self, ts, detections, occupancy):
        self.detections.append((ts, detections, occupancy))


def test_run_cycle_end_to_end(tmp_path):
    _write_fixtures(tmp_path)
    cfg = _config(tmp_path)
    pub = _FakePub()

    payload = main.run_cycle(cfg, now_ts=1718530000, publisher=pub)

    assert payload["scanner_id"] == "scan-01"
    assert len(payload["detections"]) == 1
    det = payload["detections"][0]
    assert det["band"] == "5.8G"
    assert abs(det["center_mhz"] - 5800.0) < 2.0
    assert det["class"] in {"analog", "digital", "unknown"}
    assert payload["occupancy"]["5.8G"] > 0.0

    saved = json.loads((tmp_path / "scan.json").read_text(encoding="utf-8"))
    assert saved == payload

    # one self-describing spectrum frame per band, published with the band's range
    assert len(pub.spectra) == len(cfg.bands)
    ts, band, low, high, psd = pub.spectra[0]
    assert band == "5.8G" and low == 5645.0 and high == 5945.0
    assert len(psd) > 0
    # exactly one detection publish per cycle, carrying the occupancy map
    assert len(pub.detections) == 1
    assert pub.detections[0][2]["5.8G"] > 0.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `agent/scan/.venv/Scripts/python.exe -m pytest agent/scan/tests/test_run_cycle.py -q`
Expected: FAIL — `run_cycle` doesn't accept `publisher=` yet (TypeError), and/or no publishes recorded.

- [ ] **Step 3: Implement the main.py changes**

In `agent/scan/main.py`:

(a) Change the reporter import (line 15) to drop `post_telemetry`, and add the publisher import after it:

```python
from reporter import build_payload, write_state, Holder, make_local_server
from publisher import MqttPublisher
```

(b) Replace the `run_cycle` function (current lines ~54-92) with:

```python
def run_cycle(cfg: Config, now_ts: int, publisher=None) -> dict:
    detections: List[Detection] = []
    occupancy = {}
    spectrum_summary = {}

    for band, brange in cfg.bands.items():
        spec = _get_spectrum(cfg, band, brange)
        occupancy[band] = _occupancy(spec, cfg)
        spectrum_summary[band] = _downsample(spec)
        if publisher is not None:
            publisher.publish_spectrum(now_ts, band, brange[0], brange[1], _downsample(spec, 128))

        cands = find_candidates(
            spec, cfg.thresholds.snr_threshold_db, cfg.thresholds.min_bandwidth_mhz
        )
        cands.sort(key=lambda c: c.power_dbm, reverse=True)

        budget = cfg.max_dwells_per_cycle
        for i, c in enumerate(cands):
            if i >= budget:
                LOG.info("deferred %d candidates in %s (budget=%d)", len(cands) - budget, band, budget)
                break
            iq = _get_iq(cfg, c)
            feat = compute_features(iq, cfg.dwell_sample_rate_hz)
            cls, conf = classify(feat, cfg.thresholds)
            detections.append(Detection(
                ts=now_ts,
                band=band,
                center_mhz=c.center_mhz,
                bandwidth_mhz=feat.occupied_bw_mhz if feat.occupied_bw_mhz > 0 else c.bandwidth_mhz,
                power_dbm=c.power_dbm,
                snr_db=c.snr_db,
                signal_class=cls,
                confidence=conf,
                channel=nearest_channel(c.center_mhz),
            ))

    payload = build_payload(cfg.scanner_id, now_ts, detections, occupancy, spectrum_summary)
    write_state(cfg.state_path, payload)
    if publisher is not None:
        publisher.publish_detection(now_ts, detections, occupancy)
    return payload
```

(c) In `main()`, after the local HTTP server block and before `backoff = 1.0`, construct + connect the publisher:

```python
    publisher = None
    if cfg.mqtt_enabled:
        try:
            publisher = MqttPublisher(
                cfg.mqtt_host, cfg.mqtt_port, cfg.mqtt_user, cfg.mqtt_pass,
                cfg.scanner_id, cfg.mqtt_keepalive,
            )
            publisher.connect(int(time.time()))
            LOG.info("MQTT publisher connected to %s:%d", cfg.mqtt_host, cfg.mqtt_port)
        except Exception:
            LOG.exception("MQTT connect failed; continuing without publishing")
            publisher = None
```

(d) In the loop, pass the publisher into `run_cycle`:

```python
            payload = run_cycle(cfg, now_ts=int(time.time()), publisher=publisher)
```

- [ ] **Step 4: Run to verify it passes**

Run: `agent/scan/.venv/Scripts/python.exe -m pytest agent/scan/tests/test_run_cycle.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/scan/main.py agent/scan/tests/test_run_cycle.py
git commit -m "feat(scan): publish per-band spectrum + per-cycle detection over MQTT; drop HTTP POST" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: reporter cleanup + requirements + gitignore

**Files:**
- Modify: `agent/scan/reporter.py`
- Modify: `agent/scan/tests/test_reporter.py`
- Modify: `agent/scan/requirements.txt`
- Modify: `.gitignore`

- [ ] **Step 1: Update the test (remove the post_telemetry test)**

In `agent/scan/tests/test_reporter.py`, **delete** the entire `test_post_telemetry_swallows_errors` function (the last test, which monkeypatches `reporter.requests`). Keep `test_build_payload_shape` and `test_write_state_roundtrip`.

- [ ] **Step 2: Run to confirm the two kept tests pass**

Run: `agent/scan/.venv/Scripts/python.exe -m pytest agent/scan/tests/test_reporter.py -q`
Expected: PASS (2 tests — `test_build_payload_shape`, `test_write_state_roundtrip`). `reporter.py` still has `import requests` at this point (removed in Step 3), so collection succeeds; we've only removed the test that exercised `post_telemetry`.

- [ ] **Step 3: Implement the reporter cleanup**

In `agent/scan/reporter.py`:
- **Remove** line 6: `import requests`
- **Remove** the entire `post_telemetry(...)` function (def + body, ~lines 37-46).
- Keep `build_payload`, `write_state`, `Holder`, `make_local_server`.

- [ ] **Step 4: Update requirements**

Replace `agent/scan/requirements.txt` with:

```
numpy>=1.26
paho-mqtt>=2.0
pytest>=8.0
```

- [ ] **Step 5: Ignore Python caches**

Append to `.gitignore` (repo root):

```
__pycache__/
*.pyc
```

- [ ] **Step 6: Run the FULL suite**

Run: `agent/scan/.venv/Scripts/python.exe -m pytest agent/scan/tests -q`
Expected: PASS — all tests green (config, publisher, run_cycle, reporter, and the untouched detector/dweller/sweeper/etc.). No reference to `requests` or `post_telemetry` remains.

Also confirm nothing else imports the removed symbols:
Run: `grep -rn "post_telemetry\|import requests\|server_url\|server_token" agent/scan --include=*.py`
Expected: no matches (outside `.venv`).

- [ ] **Step 7: Commit**

```bash
git add agent/scan/reporter.py agent/scan/tests/test_reporter.py agent/scan/requirements.txt .gitignore
git commit -m "refactor(scan): drop HTTP reporter (post_telemetry/requests); +paho-mqtt; ignore __pycache__" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage** (spec § → task):
- §2 add MQTT publish / remove HTTP POST → Task 4 (run_cycle/main) + Task 5 (reporter). Keep state file + local HTTP → untouched (run_cycle still `write_state`; `main()` still starts the local server). Per-band spectrum + per-cycle detection → Task 4. paho-mqtt + LWT → Task 3. psd 128 for MQTT → Task 4 (`_downsample(spec, 128)`); state file stays 64 → unchanged `_downsample(spec)`.
- §3 topics/QoS/retain → Task 3 (`QOS_*`, retain=True, `fpv/<id>/...`). on-connect online + LWT offline → Task 3.
- §4.1 builders/MqttPublisher → Tasks 2–3. §4.2 config fields+env, remove server_* → Task 1. §4.3 run_cycle/main → Task 4.
- §5 resilience (guarded publish, connect-fail → None, reconnect republish) → Task 3 (`_publish` try/except, `_on_connect` republish) + Task 4 (`main()` try/except → `publisher=None`).
- §6 secrets via env (MQTT_PUB_USER/PASS) → Task 1. §7 automated tests → Tasks 1–5; ops verify → deploy (below). §9 out-of-scope respected (no dashboard/SSE/telemetry changes here).

**Placeholder scan:** no TBD/TODO; every code step shows full content; exact test command given for this box; the only non-test artifacts (requirements, gitignore) are verified by the full-suite run + grep.

**Type/name consistency:** Config fields `mqtt_host/port/user/pass/keepalive/enabled`; env `SCAN_MQTT_HOST/PORT/KEEPALIVE/ENABLED`, `MQTT_PUB_USER/PASS` — identical across Task 1 code + tests. `MqttPublisher.__init__(host, port, user, password, scanner_id, keepalive, client_factory)`; methods `connect(ts)`, `publish_spectrum(ts, band_id, low_mhz, high_mhz, psd)`, `publish_detection(ts, detections, occupancy)`, `_on_connect(...)`, `close(ts)` — used identically in Task 3 tests and Task 4 wiring. Builders `build_spectrum_frame(scanner_id, ts, band_id, low_mhz, high_mhz, psd)` / `build_detection_payload(scanner_id, ts, detections, occupancy)` — same signatures across Tasks 2–3 and the methods that call them. Topics `fpv/<id>/{spectrum,detection,status}` consistent with the SP-A contract.

**Deploy (not a plan task):** on the Pi — `pip install -r agent/scan/requirements.txt` (adds paho-mqtt), set `MQTT_PUB_USER`/`MQTT_PUB_PASS`/`SCAN_MQTT_HOST=10.8.0.1` in the scan unit's env, restart it. Broker (SP-A) must be up first. Verify with `mosquitto_sub -t 'fpv/#' -v` (status/spectrum/detection arrive; stop unit → LWT offline). Dashboard scan panel stays empty until SP-C (accepted).
