# Node Telemetry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish real host health (CPU temp, RAM, throttle, load, uptime, disk) from the Pi that carries the SDRs and show it on the dashboard, grouped as one node.

**Architecture:** A new standalone `fpv-telemetry` Python service on the Pi reads `/proc`+`/sys`+`vcgencmd` and publishes `fpv/bladerf/telemetry` (retained, ~15 s) reusing the bladerf MQTT pub creds. The dashboard's MQTT subscriber reduces it into `store["bladerf"].telemetry`; `views/nodes.js` groups the radios (hackrf/hackrf-view/bladerf) under one node header showing the health, and `views/dashboard.js`'s node-strip shows each node once.

**Tech Stack:** Python 3 (stdlib + paho-mqtt) on the Pi; systemd; vanilla ES-module dashboard (`node --test`); Docker Compose deploy.

**Spec:** `docs/superpowers/specs/2026-07-11-node-telemetry-design.md`

## Global Constraints
- **node-id = `bladerf`** (reuse the existing scanner id; no new MQTT identity, no mosquitto ACL/restart).
- Publish topic **`fpv/bladerf/telemetry`**, QoS 1, **retained**, interval **15 s**.
- The telemetry publisher **sets NO will/LWT and NEVER writes `fpv/bladerf/status`** (must not clobber the scan agent's retained presence on the shared id).
- Payload fields (a failed source → `null`, never omitted): `node_id, ts, cpu_temp_c, cpu_load_pct, mem_used_mb, mem_total_mb, mem_used_pct, disk_used_pct, uptime_s, throttled, throttled_ever, throttle_flags`.
- Dashboard **staleness = 45 s**: telemetry older than 45 s renders as `—`.
- Dashboard rendering stays **reconcile-safe** (v2): never `innerHTML=''` a container holding a live `<video>` or a typed `.view-freq` / open RX5808 `<select>`.
- **Do NOT** modify the Pi's `fpv-scan` / `fpv-scan-hackrf` units or the scan agent's behavior; **do NOT** touch mediamtx / wg-easy / traefik.
- `npm test` stays green (extend, don't break, exports). Python: flat imports mirroring `agent/scan/` (`from collector import ...`), `conftest.py` adds its own dir to `sys.path`; run telemetry tests in isolation: `python -m pytest agent/telemetry/tests/ -v`.

## File Structure
**Pi (new package `agent/telemetry/`):**
- `agent/telemetry/collector.py` — pure metric parsers + fail-soft readers + `build_payload`.
- `agent/telemetry/publisher.py` — `TelemetryPublisher` (telemetry-only, no LWT/status).
- `agent/telemetry/main.py` — env config + publish loop.
- `agent/telemetry/requirements.txt` — `paho-mqtt`.
- `agent/telemetry/conftest.py` — `sys.path` shim for flat imports in tests.
- `agent/telemetry/tests/test_collector.py`, `test_publisher.py`, `test_main.py`.
- `systemd/fpv-telemetry.service` — deployment unit template.

**Dashboard:**
- `lib/status.js` — MODIFY: pass `node` through in `mergeStatus`.
- `dashboard/public/mqtt-scan.js` — MODIFY: reduce + subscribe `telemetry`.
- `dashboard/public/telemetry-format.js` — CREATE: pure formatters + staleness (unit-tested).
- `dashboard/public/views/components.js` — MODIFY: `nodeHealth()` DOM atom.
- `dashboard/public/views/nodes.js` — MODIFY: group by node.
- `dashboard/public/views/dashboard.js` — MODIFY: node-strip shows nodes.
- `dashboard/public/fixtures.js` — MODIFY: `node:` keys + `telemetry` sample.
- `dashboard/public/styles.css` — MODIFY: `.node-group` / `.node-health` styles.
- Tests: `test/status.test.js`, `test/mqtt-scan.test.js`, `test/telemetry-format.test.js`.

---

### Task 1: Pi collector — pure metric parsers

**Files:**
- Create: `agent/telemetry/collector.py`
- Create: `agent/telemetry/conftest.py`
- Test: `agent/telemetry/tests/test_collector.py`

**Interfaces:**
- Produces: `parse_throttled(hex_str) -> dict|None` = `{throttled: bool, throttled_ever: bool, throttle_flags: str}`; `parse_meminfo(text) -> dict|None` = `{mem_total_mb, mem_used_mb, mem_used_pct}`; `parse_loadavg(text, ncpu) -> int|None` (percent); `parse_uptime(text) -> int|None` (seconds); `millideg_to_c(text) -> float|None` (°C, 1 decimal).

- [ ] **Step 1: Write the failing test**

Create `agent/telemetry/conftest.py` (at the package root, so `os.path.dirname(__file__)` is `agent/telemetry/` — this puts the flat modules on `sys.path` for tests, mirroring `agent/scan/conftest.py`):
```python
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
```

Create `agent/telemetry/tests/test_collector.py`:
```python
from collector import parse_throttled, parse_meminfo, parse_loadavg, parse_uptime, millideg_to_c


def test_parse_throttled_flags():
    # bit0 undervolt-now, bit2 throttled-now, bit16 undervolt-ever, bit18 throttled-ever
    assert parse_throttled("throttled=0x0") == {"throttled": False, "throttled_ever": False, "throttle_flags": "0x0"}
    now = parse_throttled("0x5")            # bits 0 and 2 set -> throttled now
    assert now["throttled"] is True and now["throttled_ever"] is False
    ever = parse_throttled("0x50000")       # bits 16 and 18 set -> happened before
    assert ever["throttled"] is False and ever["throttled_ever"] is True
    assert parse_throttled("garbage") is None
    assert parse_throttled("") is None


def test_parse_meminfo():
    text = "MemTotal:        4096000 kB\nMemFree: 100000 kB\nMemAvailable:    3096000 kB\n"
    out = parse_meminfo(text)
    assert out["mem_total_mb"] == 4000
    assert out["mem_used_mb"] == 976          # (4096000-3096000)/1024 rounded
    assert out["mem_used_pct"] == 24
    assert parse_meminfo("nonsense") is None


def test_parse_loadavg_normalized():
    assert parse_loadavg("2.00 1.5 1.0 1/200 1234", 4) == 50    # 2.0/4 cores = 50%
    assert parse_loadavg("4.0 0 0", 4) == 100
    assert parse_loadavg("", 4) is None


def test_parse_uptime():
    assert parse_uptime("123456.78 1000.0") == 123456
    assert parse_uptime("bad") is None


def test_millideg_to_c():
    assert millideg_to_c("62400\n") == 62.4
    assert millideg_to_c("bad") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest agent/telemetry/tests/test_collector.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'collector'`.

- [ ] **Step 3: Write minimal implementation**

Create `agent/telemetry/collector.py`:
```python
"""Host telemetry collection. Pure parsers (take raw text) + fail-soft readers + payload builder.
Flat-import module (run from agent/telemetry/), mirroring agent/scan/."""


def parse_throttled(hex_str):
    """`vcgencmd get_throttled` -> {throttled, throttled_ever, throttle_flags}. None on garbage."""
    if not hex_str:
        return None
    s = hex_str.strip()
    if "=" in s:
        s = s.split("=", 1)[1].strip()
    try:
        bits = int(s, 16)
    except (ValueError, TypeError):
        return None
    UNDERVOLT_NOW, THROTTLED_NOW = 1 << 0, 1 << 2
    UNDERVOLT_EVER, THROTTLED_EVER = 1 << 16, 1 << 18
    return {
        "throttled": bool(bits & (UNDERVOLT_NOW | THROTTLED_NOW)),
        "throttled_ever": bool(bits & (UNDERVOLT_EVER | THROTTLED_EVER)),
        "throttle_flags": hex(bits),
    }


def parse_meminfo(text):
    """/proc/meminfo -> {mem_total_mb, mem_used_mb, mem_used_pct}. None if unparseable."""
    vals = {}
    for line in (text or "").splitlines():
        if ":" not in line:
            continue
        k, rest = line.split(":", 1)
        num = rest.strip().split()
        if num:
            try:
                vals[k.strip()] = int(num[0])   # kB
            except ValueError:
                pass
    total = vals.get("MemTotal")
    avail = vals.get("MemAvailable")
    if not total or avail is None:
        return None
    used_kb = max(0, total - avail)
    return {
        "mem_total_mb": round(total / 1024),
        "mem_used_mb": round(used_kb / 1024),
        "mem_used_pct": round(100 * used_kb / total),
    }


def parse_loadavg(text, ncpu):
    """/proc/loadavg 1-min field normalized to % of ncpu cores. None if unparseable."""
    try:
        one = float((text or "").split()[0])
    except (ValueError, IndexError):
        return None
    n = ncpu if ncpu and ncpu > 0 else 1
    return round(100 * one / n)


def parse_uptime(text):
    """/proc/uptime first field (seconds, int). None if unparseable."""
    try:
        return int(float((text or "").split()[0]))
    except (ValueError, IndexError):
        return None


def millideg_to_c(text):
    """/sys/.../temp millidegrees -> float °C (1 decimal). None if bad."""
    try:
        return round(int((text or "").strip()) / 1000.0, 1)
    except (ValueError, TypeError):
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest agent/telemetry/tests/test_collector.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add agent/telemetry/collector.py agent/telemetry/conftest.py agent/telemetry/tests/test_collector.py
git commit -m "feat(telemetry): pure host-metric parsers (throttled/meminfo/loadavg/uptime/temp)"
```

---

### Task 2: Pi collector — fail-soft readers + build_payload

**Files:**
- Modify: `agent/telemetry/collector.py`
- Test: `agent/telemetry/tests/test_collector.py` (add cases)

**Interfaces:**
- Consumes: the Task 1 parsers.
- Produces: `read_disk(path="/") -> int|None`; `build_payload(node_id, ts=None, *, reader=None) -> dict`. `build_payload` always returns all 12 payload keys (nulls where a source failed). `reader` is an injectable object exposing `cpu_temp()/mem()/load()/uptime()/disk()/throttled()` for tests; default reads the real host.

- [ ] **Step 1: Write the failing test**

Add to `agent/telemetry/tests/test_collector.py`:
```python
from collector import build_payload


class FakeReader:
    def cpu_temp(self): return 62.4
    def mem(self): return {"mem_total_mb": 4000, "mem_used_mb": 976, "mem_used_pct": 24}
    def load(self): return 38
    def uptime(self): return 123456
    def disk(self): return 47
    def throttled(self): return {"throttled": False, "throttled_ever": True, "throttle_flags": "0x50000"}


class EmptyReader:
    def cpu_temp(self): return None
    def mem(self): return None
    def load(self): return None
    def uptime(self): return None
    def disk(self): return None
    def throttled(self): return None


PAYLOAD_KEYS = {"node_id", "ts", "cpu_temp_c", "cpu_load_pct", "mem_used_mb", "mem_total_mb",
                "mem_used_pct", "disk_used_pct", "uptime_s", "throttled", "throttled_ever", "throttle_flags"}


def test_build_payload_full():
    p = build_payload("bladerf", ts=1000, reader=FakeReader())
    assert set(p) == PAYLOAD_KEYS
    assert p["node_id"] == "bladerf" and p["ts"] == 1000
    assert p["cpu_temp_c"] == 62.4 and p["mem_used_pct"] == 24
    assert p["throttled_ever"] is True and p["throttle_flags"] == "0x50000"


def test_build_payload_all_nulls_stable_shape():
    p = build_payload("bladerf", ts=1000, reader=EmptyReader())
    assert set(p) == PAYLOAD_KEYS
    assert p["cpu_temp_c"] is None and p["mem_used_mb"] is None and p["throttled"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest agent/telemetry/tests/test_collector.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_payload'`.

- [ ] **Step 3: Write minimal implementation**

Append to `agent/telemetry/collector.py`:
```python
import os
import subprocess
import time


def _read(path):
    try:
        with open(path, "r") as f:
            return f.read()
    except OSError:
        return None


def read_disk(path="/"):
    try:
        st = os.statvfs(path)
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        if total <= 0:
            return None
        return round(100 * (total - free) / total)
    except OSError:
        return None


class HostReader:
    """Fail-soft reads of the real host. Each method returns None on failure."""

    def cpu_temp(self):
        return millideg_to_c(_read("/sys/class/thermal/thermal_zone0/temp"))

    def mem(self):
        return parse_meminfo(_read("/proc/meminfo"))

    def load(self):
        return parse_loadavg(_read("/proc/loadavg"), os.cpu_count())

    def uptime(self):
        return parse_uptime(_read("/proc/uptime"))

    def disk(self):
        return read_disk("/")

    def throttled(self):
        try:
            out = subprocess.run(["vcgencmd", "get_throttled"],
                                 capture_output=True, text=True, timeout=3)
            if out.returncode != 0:
                return None
            return parse_throttled(out.stdout)
        except (OSError, subprocess.SubprocessError):
            return None


def build_payload(node_id, ts=None, *, reader=None):
    """Assemble the fpv/<node>/telemetry payload. Every key is always present (null on failure)."""
    r = reader or HostReader()
    ts = int(ts if ts is not None else time.time())
    mem = r.mem() or {}
    thr = r.throttled() or {}
    return {
        "node_id": node_id,
        "ts": ts,
        "cpu_temp_c": r.cpu_temp(),
        "cpu_load_pct": r.load(),
        "mem_used_mb": mem.get("mem_used_mb"),
        "mem_total_mb": mem.get("mem_total_mb"),
        "mem_used_pct": mem.get("mem_used_pct"),
        "disk_used_pct": r.disk(),
        "uptime_s": r.uptime(),
        "throttled": thr.get("throttled"),
        "throttled_ever": thr.get("throttled_ever"),
        "throttle_flags": thr.get("throttle_flags"),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest agent/telemetry/tests/test_collector.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add agent/telemetry/collector.py agent/telemetry/tests/test_collector.py
git commit -m "feat(telemetry): fail-soft host readers + build_payload (stable 12-key shape)"
```

---

### Task 3: Pi publisher + loop + systemd unit

**Files:**
- Create: `agent/telemetry/publisher.py`, `agent/telemetry/main.py`, `agent/telemetry/requirements.txt`, `systemd/fpv-telemetry.service`
- Test: `agent/telemetry/tests/test_publisher.py`, `agent/telemetry/tests/test_main.py`

**Interfaces:**
- Consumes: `build_payload` (Task 2).
- Produces: `TelemetryPublisher(host, port, user, password, node_id, keepalive=60, client_factory=None)` with `.connect()`, `.publish(payload)`, `.close()`, publishing ONLY `fpv/<node>/telemetry` (QoS1, retained, no LWT); `load_env(env=None) -> dict` with keys `node_id, interval_s, host, port, user, password`.

- [ ] **Step 1: Write the failing test**

Create `agent/telemetry/tests/test_publisher.py`:
```python
from publisher import TelemetryPublisher


class FakeClient:
    def __init__(self):
        self.published = []
        self.will = None
    def username_pw_set(self, u, p): self.creds = (u, p)
    def will_set(self, *a, **k): self.will = (a, k)
    def connect(self, *a, **k): pass
    def loop_start(self): pass
    def loop_stop(self): pass
    def disconnect(self): pass
    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))


def test_publishes_only_telemetry_retained_no_lwt_no_status():
    fake = FakeClient()
    pub = TelemetryPublisher("h", 1883, "u", "p", "bladerf", client_factory=lambda cid: fake)
    pub.connect()
    pub.publish({"node_id": "bladerf", "ts": 1})
    assert fake.will is None                                    # NO LWT
    assert len(fake.published) == 1
    topic, payload, qos, retain = fake.published[0]
    assert topic == "fpv/bladerf/telemetry"
    assert qos == 1 and retain is True
    assert '"node_id": "bladerf"' in payload
    assert all("status" not in t for t, *_ in fake.published)   # NEVER writes status
```

Create `agent/telemetry/tests/test_main.py`:
```python
from main import load_env


def test_load_env_defaults_and_overrides():
    d = load_env({})
    assert d["node_id"] == "bladerf" and d["interval_s"] == 15 and d["host"] == "10.8.0.1"
    d2 = load_env({"TELEM_NODE_ID": "pi5", "TELEM_INTERVAL_S": "30", "MQTT_PUB_USER": "bladerf"})
    assert d2["node_id"] == "pi5" and d2["interval_s"] == 30 and d2["user"] == "bladerf"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest agent/telemetry/tests/test_publisher.py agent/telemetry/tests/test_main.py -v`
Expected: FAIL — `No module named 'publisher'` / `'main'`.

- [ ] **Step 3: Write minimal implementation**

Create `agent/telemetry/publisher.py`:
```python
import json
import logging

LOG = logging.getLogger("telemetry.publisher")


def _default_client_factory(client_id):
    import paho.mqtt.client as mqtt
    return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)


class TelemetryPublisher:
    """Publishes ONLY fpv/<node>/telemetry (retained, QoS1). Sets NO will/LWT and never writes
    fpv/<node>/status — must not clobber the scan agent's retained presence on the shared node id."""

    QOS = 1

    def __init__(self, host, port, user, password, node_id, keepalive=60, client_factory=None):
        self.host, self.port, self.user, self.password = host, port, user, password
        self.node_id, self.keepalive = node_id, keepalive
        self._factory = client_factory or _default_client_factory
        self._topic = f"fpv/{node_id}/telemetry"
        self._client = None

    def connect(self):
        client = self._factory(f"telem-{self.node_id}")
        if self.user:
            client.username_pw_set(self.user, self.password)
        # NOTE: intentionally NO will_set() — telemetry must not affect scan presence.
        client.connect(self.host, self.port, keepalive=self.keepalive)
        client.loop_start()
        self._client = client

    def publish(self, payload):
        if self._client is None:
            return
        try:
            self._client.publish(self._topic, json.dumps(payload), qos=self.QOS, retain=True)
        except Exception:
            LOG.exception("telemetry publish failed")

    def close(self):
        if self._client is None:
            return
        try:
            self._client.loop_stop()
            self._client.disconnect()
        except Exception:
            LOG.exception("close failed")
```

Create `agent/telemetry/main.py`:
```python
import logging
import os
import time

from collector import build_payload
from publisher import TelemetryPublisher

LOG = logging.getLogger("telemetry.main")


def load_env(env=None):
    env = os.environ if env is None else env
    return {
        "node_id": env.get("TELEM_NODE_ID", "bladerf"),
        "interval_s": int(env.get("TELEM_INTERVAL_S", "15")),
        "host": env.get("TELEM_MQTT_HOST", "10.8.0.1"),
        "port": int(env.get("TELEM_MQTT_PORT", "1883")),
        "user": env.get("MQTT_PUB_USER", "pub"),
        "password": env.get("MQTT_PUB_PASS", ""),
    }


def main():
    logging.basicConfig(level=logging.INFO)
    cfg = load_env()
    pub = TelemetryPublisher(cfg["host"], cfg["port"], cfg["user"], cfg["password"], cfg["node_id"])
    pub.connect()
    LOG.info("fpv-telemetry -> fpv/%s/telemetry every %ss", cfg["node_id"], cfg["interval_s"])
    try:
        while True:
            pub.publish(build_payload(cfg["node_id"]))
            time.sleep(cfg["interval_s"])
    except KeyboardInterrupt:
        pass
    finally:
        pub.close()


if __name__ == "__main__":
    main()
```

Create `agent/telemetry/requirements.txt`:
```
paho-mqtt>=2.0
```

Create `systemd/fpv-telemetry.service` (template; installed as `/etc/systemd/system/fpv-telemetry.service` on the Pi):
```ini
[Unit]
Description=FPV node telemetry publisher (CPU temp / RAM / host health -> MQTT)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/fpv-video-stream/agent/telemetry
Environment=TELEM_NODE_ID=bladerf
Environment=TELEM_MQTT_HOST=10.8.0.1
# Publish creds (MQTT_PUB_USER=bladerf, MQTT_PUB_PASS=<bladerf publish pass>) are secret —
# put them in /etc/fpv-telemetry.env (not committed). The '-' lets the unit start if absent.
EnvironmentFile=-/etc/fpv-telemetry.env
ExecStart=/opt/fpv-video-stream/agent/telemetry/.venv/bin/python -u main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest agent/telemetry/tests/ -v`
Expected: PASS (all collector + publisher + main tests).

- [ ] **Step 5: Commit**

```bash
git add agent/telemetry/publisher.py agent/telemetry/main.py agent/telemetry/requirements.txt agent/telemetry/tests/test_publisher.py agent/telemetry/tests/test_main.py systemd/fpv-telemetry.service
git commit -m "feat(telemetry): telemetry-only MQTT publisher + loop + systemd unit"
```

---

### Task 4: Dashboard — `lib/status.js` node passthrough

**Files:**
- Modify: `lib/status.js:13-22` (the returned object in `mergeStatus`)
- Test: `test/status.test.js` (add a case)

**Interfaces:**
- Produces: each device object from `mergeStatus` gains `node: <string>|null` from the registry entry's `node` field.

- [ ] **Step 1: Write the failing test**

Add to `test/status.test.js`:
```javascript
test('mergeStatus passes through node grouping id, null when absent', () => {
  const r = { devices: [
    { id: 'bladerf', name: 'B', location: 'z', kind: 'scanner', node: 'bladerf' },
    { id: 'pi-01', name: 'A', location: 'x' },
  ] };
  const out = mergeStatus(r, pathsList, now);
  assert.equal(out.find((d) => d.id === 'bladerf').node, 'bladerf');
  assert.equal(out.find((d) => d.id === 'pi-01').node, null);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test test/status.test.js`
Expected: FAIL — `node` is `undefined`, not `'bladerf'`/`null`.

- [ ] **Step 3: Write minimal implementation**

In `lib/status.js`, add one line to the returned object in `mergeStatus` (after `kind:`):
```javascript
      kind: d.kind || 'camera',
      node: d.node || null,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test test/status.test.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/status.js test/status.test.js
git commit -m "feat(telemetry): mergeStatus passes registry node grouping id through"
```

---

### Task 5: Dashboard — `mqtt-scan.js` telemetry reduce + subscribe

**Files:**
- Modify: `dashboard/public/mqtt-scan.js` (`ensure()` store init line ~24; `reduce()` regex line ~33 + a new branch; subscribe list line ~99)
- Test: `test/mqtt-scan.test.js` (add a case)

**Interfaces:**
- Consumes: the `fpv/<id>/telemetry` payload contract.
- Produces: `store[id].telemetry = { ts, cpu_temp_c, cpu_load_pct, mem_used_mb, mem_total_mb, mem_used_pct, disk_used_pct, uptime_s, throttled, throttled_ever, throttle_flags }` (or `null` initially).

- [ ] **Step 1: Write the failing test**

Add to `test/mqtt-scan.test.js` (match the file's existing import of `reduce`):
```javascript
test('telemetry message reduces into store[id].telemetry', () => {
  const store = {};
  reduce(store, 'fpv/bladerf/telemetry', JSON.stringify({
    node_id: 'bladerf', ts: 1752200000, cpu_temp_c: 62.4, cpu_load_pct: 38,
    mem_used_mb: 1200, mem_total_mb: 4096, mem_used_pct: 29, disk_used_pct: 47,
    uptime_s: 123456, throttled: false, throttled_ever: true, throttle_flags: '0x50000',
  }));
  const t = store.bladerf.telemetry;
  assert.equal(t.cpu_temp_c, 62.4);
  assert.equal(t.mem_used_pct, 29);
  assert.equal(t.throttled_ever, true);
  assert.equal(t.throttle_flags, '0x50000');
});

test('telemetry reduce ignores malformed payload', () => {
  const store = {};
  reduce(store, 'fpv/bladerf/telemetry', 'not json');
  assert.equal(store.bladerf === undefined || store.bladerf.telemetry === null, true);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test test/mqtt-scan.test.js`
Expected: FAIL — the regex doesn't match `telemetry`, so `store.bladerf` is undefined.

- [ ] **Step 3: Write minimal implementation**

In `dashboard/public/mqtt-scan.js`:

(a) `ensure()` — add `telemetry: null` to the initial store object:
```javascript
    store[id] = { online: false, status_ts: 0, detection: null, video: null, rxtune: null, view: null, telemetry: null, bands: {}, latestPsd: {}, waterfalls: {} };
```

(b) `reduce()` — add `telemetry` to the topic regex:
```javascript
  const m = /^fpv\/([^/]+)\/(spectrum|detection|status|video|rxtune|view|telemetry)$/.exec(topic || '');
```

(c) add a branch (e.g., after the `view` branch, before `spectrum`):
```javascript
  } else if (kind === 'telemetry') {
    s.telemetry = {
      ts: data.ts || 0,
      cpu_temp_c: data.cpu_temp_c ?? null,
      cpu_load_pct: data.cpu_load_pct ?? null,
      mem_used_mb: data.mem_used_mb ?? null,
      mem_total_mb: data.mem_total_mb ?? null,
      mem_used_pct: data.mem_used_pct ?? null,
      disk_used_pct: data.disk_used_pct ?? null,
      uptime_s: data.uptime_s ?? null,
      throttled: data.throttled ?? null,
      throttled_ever: data.throttled_ever ?? null,
      throttle_flags: data.throttle_flags ?? null,
    };
```

(d) subscribe list — add `fpv/+/telemetry`:
```javascript
    client.on('connect', () => client.subscribe(['fpv/+/spectrum', 'fpv/+/detection', 'fpv/+/status', 'fpv/+/video', 'fpv/+/rxtune', 'fpv/+/view', 'fpv/+/telemetry']));
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test test/mqtt-scan.test.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/public/mqtt-scan.js test/mqtt-scan.test.js
git commit -m "feat(telemetry): mqtt-scan reduces + subscribes fpv/+/telemetry"
```

---

### Task 6: Dashboard — pure telemetry formatters

**Files:**
- Create: `dashboard/public/telemetry-format.js`
- Test: `test/telemetry-format.test.js`

**Interfaces:**
- Produces: `TELEM_STALE_S = 45`; `isFresh(ts, nowS) -> bool`; `fmtTemp(c) -> string`; `fmtMem(t) -> string`; `fmtPctVal(pct) -> string`; `fmtUptimeShort(s) -> string`; `throttleState(t) -> {text, warn}|null`.

- [ ] **Step 1: Write the failing test**

Create `test/telemetry-format.test.js`:
```javascript
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isFresh, fmtTemp, fmtMem, fmtPctVal, fmtUptimeShort, throttleState, TELEM_STALE_S } from '../dashboard/public/telemetry-format.js';

test('isFresh uses the 45s window', () => {
  assert.equal(TELEM_STALE_S, 45);
  assert.equal(isFresh(1000, 1040), true);
  assert.equal(isFresh(1000, 1046), false);
  assert.equal(isFresh(null, 1000), false);
});

test('fmtTemp / fmtPctVal / fmtMem render dashes for null', () => {
  assert.equal(fmtTemp(62.4), '62.4°C');
  assert.equal(fmtTemp(null), '—');
  assert.equal(fmtPctVal(38), '38%');
  assert.equal(fmtPctVal(null), '—');
  assert.equal(fmtMem({ mem_used_pct: 29, mem_total_mb: 4096 }), '29% (4.0G)');
  assert.equal(fmtMem(null), '—');
});

test('fmtUptimeShort', () => {
  assert.equal(fmtUptimeShort(90061), '1д 1г');
  assert.equal(fmtUptimeShort(3720), '1г 2хв');
  assert.equal(fmtUptimeShort(null), '—');
});

test('throttleState flags now vs ever vs clear', () => {
  assert.deepEqual(throttleState({ throttled: true, throttled_ever: true }), { text: '🔥 THROTTLED', warn: true });
  assert.deepEqual(throttleState({ throttled: false, throttled_ever: true }), { text: '⚠ був throttle', warn: true });
  assert.equal(throttleState({ throttled: false, throttled_ever: false }), null);
  assert.equal(throttleState(null), null);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test test/telemetry-format.test.js`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

Create `dashboard/public/telemetry-format.js`:
```javascript
// dashboard/public/telemetry-format.js — pure telemetry formatters + staleness (no DOM; unit-tested).
export const TELEM_STALE_S = 45;

export function isFresh(ts, nowS) { return ts != null && (nowS - ts) < TELEM_STALE_S; }

export function fmtTemp(c) { return c == null ? '—' : `${Number(c).toFixed(1)}°C`; }

export function fmtPctVal(pct) { return pct == null ? '—' : `${pct}%`; }

export function fmtMem(t) {
  if (!t || t.mem_used_pct == null) return '—';
  const gb = t.mem_total_mb != null ? ` (${(t.mem_total_mb / 1024).toFixed(1)}G)` : '';
  return `${t.mem_used_pct}%${gb}`;
}

export function fmtUptimeShort(s) {
  if (s == null) return '—';
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600);
  return d ? `${d}д ${h}г` : `${h}г ${Math.floor((s % 3600) / 60)}хв`;
}

export function throttleState(t) {
  if (!t) return null;
  if (t.throttled) return { text: '🔥 THROTTLED', warn: true };
  if (t.throttled_ever) return { text: '⚠ був throttle', warn: true };
  return null;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test test/telemetry-format.test.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/public/telemetry-format.js test/telemetry-format.test.js
git commit -m "feat(telemetry): pure telemetry formatters + 45s staleness"
```

---

### Task 7: Dashboard — node grouping on the Вузли screen

**Files:**
- Modify: `dashboard/public/views/components.js` (add `nodeHealth`)
- Modify: `dashboard/public/views/nodes.js` (group by node; drop the per-card TEMP cell)
- Modify: `dashboard/public/fixtures.js` (add `node:` keys + a `telemetry` block)
- Modify: `dashboard/public/styles.css` (`.node-group`, `.node-group-head`, `.node-health`)
- Verify: `node --check`, `npm test`, controller visual gate

**Interfaces:**
- Consumes: `store[nodeId].telemetry` (Task 5), `telemetry-format.js` (Task 6), `d.node` (Task 4).
- Produces: `nodeHealth(telemetry, nowS) -> HTMLElement` (a `.node-health` readout). The Вузли screen renders one `.node-group` per node-id (header health + member radio cards) and a loose section for `node`-less devices.

- [ ] **Step 1: Add fixtures so the screen has data to render**

In `dashboard/public/fixtures.js`: add `node: 'bladerf'` to the `bladerf` device, add a `hackrf` scanner and `hackrf-view` camera both with `node: 'bladerf'`, and add a `telemetry` block to `scanStore.bladerf`:
```javascript
// devices[]: add node to bladerf and add two more radios on the same node
{ id:'bladerf', name:'Сканер bladeRF', location:'Дах', kind:'scanner', node:'bladerf', online:true, uptimeSec:9000 },
{ id:'hackrf', name:'HackRF', location:'Дах', kind:'scanner', node:'bladerf', online:true, uptimeSec:9000 },
{ id:'hackrf-view', name:'SDR (hackrf) Viewer', location:'Дах', kind:'camera', node:'bladerf', online:true, bitrateKbps:800, uptimeSec:9000, readers:1 },
```
and inside `scanStore.bladerf` add:
```javascript
    telemetry:{ ts:NOW, cpu_temp_c:62.4, cpu_load_pct:38, mem_used_mb:1200, mem_total_mb:4096, mem_used_pct:29, disk_used_pct:47, uptime_s:123456, throttled:false, throttled_ever:true, throttle_flags:'0x50000' },
```

- [ ] **Step 2: Add the `nodeHealth` DOM atom**

In `dashboard/public/views/components.js`, add the import and function:
```javascript
import { isFresh, fmtTemp, fmtMem, fmtPctVal, fmtUptimeShort, throttleState } from '/telemetry-format.js';

// Host-health readout for a node header. `tel` = store[nodeId].telemetry (or null). Stale/missing -> dashes.
export function nodeHealth(tel, nowS){
  const t = (tel && isFresh(tel.ts, nowS)) ? tel : null;
  const wrap = el('div','node-health');
  const cell = (k,v) => `<div><span class="k">${k}</span><span class="mono">${escapeHtml(v)}</span></div>`;
  wrap.innerHTML =
    cell('CPU', fmtTemp(t ? t.cpu_temp_c : null)) +
    cell('RAM', fmtMem(t)) +
    cell('LOAD', fmtPctVal(t ? t.cpu_load_pct : null)) +
    cell('UPTIME', fmtUptimeShort(t ? t.uptime_s : null)) +
    cell('DISK', fmtPctVal(t ? t.disk_used_pct : null));
  const thr = throttleState(t);
  if (thr) wrap.insertAdjacentHTML('beforeend', `<span class="pip ${thr.warn?'warn':''}">${escapeHtml(thr.text)}</span>`);
  return wrap;
}
```

- [ ] **Step 3: Restructure `nodes.js` to group by node**

Read the current `dashboard/public/views/nodes.js` first — it already has reconcile-safe `buildCard(d, ctx, store)` and `updateCard(card, d, ctx, store)`. Keep them, but:
- In `buildCard`, DELETE the TEMP grid cell (`<div><span class="k">TEMP</span>${tempSlot(null)}</div>`) — node temp now lives in the group header. Renumber the grid to 3 cells (UPTIME, col3, col4). Remove the now-unused `tempSlot` import.
- Add `import { nodeHealth } from '/views/components.js'` (extend the existing import line).
- Extract the existing per-card reconcile loop body into a helper `reconcileCards(grid, devs, ctx, store)`.
- Replace `render()` with grouping:
```javascript
export function render(container, ctx) {
  container.className = 'screen screen-pad';
  let root = container.querySelector('.node-root');
  if (!root) {
    container.innerHTML = '';
    container.appendChild(el('div', 'label-caps', 'КЕРУВАННЯ ВУЗЛАМИ'));
    root = el('div', 'node-root');
    container.appendChild(root);
  }
  const store = ctx.scanStore();
  const nowS = Math.floor(Date.now() / 1000);

  // Partition devices: node-id -> [devices], plus node-less standalone.
  const groups = new Map();
  const standalone = [];
  for (const d of ctx.devices()) {
    if (d.node) { if (!groups.has(d.node)) groups.set(d.node, []); groups.get(d.node).push(d); }
    else standalone.push(d);
  }

  // Drop containers for nodes/standalone that no longer exist.
  const wantKeys = new Set([...[...groups.keys()].map((n) => `group:${n}`), ...(standalone.length ? ['loose'] : [])]);
  for (const child of [...root.children]) {
    if (child.dataset && child.dataset.key && !wantKeys.has(child.dataset.key)) child.remove();
  }

  // Node groups: header (label + live health) + member radio cards.
  for (const [nodeId, devs] of groups) {
    let group = [...root.children].find((c) => c.dataset && c.dataset.key === `group:${nodeId}`);
    if (!group) {
      group = el('div', 'node-group');
      group.dataset.key = `group:${nodeId}`;
      group.innerHTML = `<div class="node-group-head"><div class="ng-title label-caps"></div><div class="ng-health"></div></div><div class="node-strip"></div>`;
      root.appendChild(group);
    }
    group.querySelector('.ng-title').textContent = `ВУЗОЛ · ${nodeId}`;
    const healthSlot = group.querySelector('.ng-health');
    healthSlot.innerHTML = '';
    healthSlot.appendChild(nodeHealth(store[nodeId] && store[nodeId].telemetry, nowS));
    reconcileCards(group.querySelector('.node-strip'), devs, ctx, store);
  }

  // Standalone (node-less) devices.
  let loose = [...root.children].find((c) => c.dataset && c.dataset.key === 'loose');
  if (standalone.length) {
    if (!loose) { loose = el('div', 'node-strip'); loose.dataset.key = 'loose'; root.appendChild(loose); }
    reconcileCards(loose, standalone, ctx, store);
  }
}

// Reuse-aware card reconcile within one grid (extracted from the old render loop).
function reconcileCards(grid, devices, ctx, store) {
  const existing = new Map();
  for (const child of [...grid.children]) {
    const id = child.dataset && child.dataset.id;
    if (!id) continue;
    if (!devices.some((d) => d.id === id)) child.remove();
    else existing.set(id, child);
  }
  for (const d of devices) {
    let card = existing.get(d.id);
    if (!card || card.dataset.kind !== d.kind) { if (card) card.remove(); card = buildCard(d, ctx, store); grid.appendChild(card); }
    updateCard(card, d, ctx, store);
  }
}
```

- [ ] **Step 4: Add styles**

In `dashboard/public/styles.css` (near the other node styles):
```css
.node-group{border:1px solid var(--line);margin-bottom:var(--gutter);}
.node-group-head{display:flex;flex-wrap:wrap;align-items:center;gap:12px;padding:8px 10px;border-bottom:1px solid var(--line);background:var(--panel-2);}
.node-group-head .ng-title{margin:0;}
.node-health{display:flex;flex-wrap:wrap;gap:14px;align-items:center;}
.node-health > div{display:flex;flex-direction:column;}
.node-health .k{font-size:10px;color:var(--muted);}
```

- [ ] **Step 5: Verify syntax + tests + visual**

Run: `node --check dashboard/public/views/nodes.js && node --check dashboard/public/views/components.js && npm test`
Expected: all clean; `npm test` still green (no reduced count).

Controller visual gate (dev server `node dashboard/dev-serve.mjs`, then Playwright): navigate `about:blank` then `http://127.0.0.1:8081/index.html?preview=1#/nodes`. Expected: a `ВУЗОЛ · bladerf` group header with health cells (CPU 62.4°C, RAM 29% (4.0G), LOAD 38%, UPTIME 1д 10г, DISK 47%) and a `⚠ був throttle` badge; under it the bladerf + hackrf scanner cards (RX5808 + view controls intact, NO per-card TEMP cell) and the hackrf-view camera card; the phone `pi-03` and `pi-01` render in a loose section with no node header. Also re-run the reconcile check: type `4240` into a scanner card's `.view-freq`, call `window.__rerender()` twice → value and cards survive (grouping stays reconcile-safe).

- [ ] **Step 6: Commit**

```bash
git add dashboard/public/views/nodes.js dashboard/public/views/components.js dashboard/public/fixtures.js dashboard/public/styles.css
git commit -m "feat(telemetry): group Вузли by node with a host-health header"
```

---

### Task 8: Dashboard — Панель node-strip shows nodes

**Files:**
- Modify: `dashboard/public/views/dashboard.js` (the node-strip section, ~lines 101-111)
- Verify: `node --check`, `npm test`, controller visual gate

**Interfaces:**
- Consumes: `nodeHealth` (Task 7), `d.node` (Task 4), `store[nodeId].telemetry` (Task 5).
- Produces: the Панель node-strip lists each physical node once (node header health) plus node-less devices, instead of one card per radio device.

- [ ] **Step 1: Rework the node-strip to group by node**

Read the current `dashboard/public/views/dashboard.js` node-strip block (it rebuilds `strip.innerHTML=''` each render — that is fine here; the strip holds no inputs or live video). Add `nodeHealth` to the components import, then replace the strip loop:
```javascript
  // --- node health strip (rebuilt each render; one entry per physical node + node-less devices) ---
  strip.innerHTML = '';
  const nowS = Math.floor(Date.now() / 1000);
  const seenNodes = new Set();
  for (const d of ctx.devices()) {
    if (d.node) {
      if (seenNodes.has(d.node)) continue;   // one card per node
      seenNodes.add(d.node);
      const online = !!(store[d.node] && store[d.node].online);
      const card = cornerCard(`<div class="nc-head"><span class="nc-title">ВУЗОЛ · ${escapeHtml(d.node)}</span>${pip(online)}</div>`);
      card.appendChild(nodeHealth(store[d.node] && store[d.node].telemetry, nowS));
      strip.appendChild(card);
    } else {
      const online = d.online;
      const card = cornerCard(`<div class="nc-head"><span class="nc-title">${escapeHtml(d.name)}</span>${pip(online)}</div>
        <div class="nc-grid"><div><span class="k">UPTIME</span><span class="mono">${fmtUptime(d.uptimeSec)}</span></div></div>`);
      strip.appendChild(card);
    }
  }
```
Remove the now-unused `tempSlot`/`occupancyStrip` imports from dashboard.js ONLY if they are no longer referenced elsewhere in the file (check first — occupancyStrip may still be used; if so leave it).

- [ ] **Step 2: Verify syntax + tests + visual**

Run: `node --check dashboard/public/views/dashboard.js && npm test`
Expected: clean; `npm test` green.

Controller visual gate: navigate `about:blank` then `http://127.0.0.1:8081/index.html?preview=1#/dashboard`. Expected: the node-strip shows one `ВУЗОЛ · bladerf` card with the health readout (CPU/RAM/LOAD/UPTIME/DISK + throttle badge) — NOT three separate radio cards — plus loose cards for the phone/`pi-01`. Camera feed tiles + active-detections summary unchanged; reconcile check: `window.__rerender()` keeps the live `<video>` tiles (sameVideo) — the strip rebuild must not touch the `.grid` feeds.

- [ ] **Step 3: Commit**

```bash
git add dashboard/public/views/dashboard.js
git commit -m "feat(telemetry): Панель node-strip shows one card per node with host health"
```

---

## Deployment & Acceptance (operational — after review, over WireGuard)

Not a TDD task; performed by the operator/controller once the plan is implemented and reviewed.

**Pi (`ssh andriy@` over WG; repo at `/opt/fpv-video-stream`):**
1. `git -C /opt/fpv-video-stream pull --ff-only`
2. Create the venv + deps (does NOT touch the scan venv):
   `python3 -m venv /opt/fpv-video-stream/agent/telemetry/.venv && /opt/fpv-video-stream/agent/telemetry/.venv/bin/pip install -r /opt/fpv-video-stream/agent/telemetry/requirements.txt`
3. Write `/etc/fpv-telemetry.env` with `MQTT_PUB_USER=bladerf` and `MQTT_PUB_PASS=<bladerf publish pass>` (from devices.yml / the existing scan env).
4. Install + start: `sudo cp systemd/fpv-telemetry.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now fpv-telemetry`
5. Verify: `journalctl -u fpv-telemetry -n 20` shows the publish log; `mosquitto_sub -h 10.8.0.1 -u <read> -P <pass> -t 'fpv/bladerf/telemetry' -C 1` prints a payload; `systemctl is-active fpv-scan fpv-scan-hackrf` still `active` (untouched).

**Dashboard (server over WG; repo at `/home/andriy/fpv-video-stream`):**
1. Add `node: bladerf` to the `hackrf`, `hackrf-view`, and `bladerf` entries in `devices.yml`.
2. `sudo -u andriy git -C /home/andriy/fpv-video-stream pull --ff-only`
3. `cd /home/andriy/fpv-video-stream && docker compose build dashboard && docker compose up -d --no-deps dashboard` (mediamtx / mosquitto / wg-easy untouched).
4. Hard-refresh; verify the Вузли screen shows the `bladerf` node group with live CPU temp/RAM and the Панель node-strip shows one node card. Confirm a throttle badge appears if the Pi has ever throttled.

---

## Self-Review notes
- Spec coverage: publisher (T1-T3), node model passthrough (T4), MQTT consume (T5), formatters (T6), Вузли grouping (T7), Панель strip (T8), deploy (final section). ✓
- Staleness 45 s (T6) matches spec. Payload 12 keys consistent across T2/T5/T7 fixtures. ✓
- `nodeHealth` defined in T7 components.js, consumed in T7 nodes.js and T8 dashboard.js — same signature `(telemetry, nowS)`. ✓
- Reconcile-safety preserved: T7 keeps buildCard/updateCard + per-grid reconcile; T8 strip holds no inputs/video. ✓
