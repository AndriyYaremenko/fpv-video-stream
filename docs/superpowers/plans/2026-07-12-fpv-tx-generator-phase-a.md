# FPV TX-генератор Phase-A (dashboard-controlled) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Phase-0 `agent/tx` TX generator controllable from the dashboard — a TX role on the bladeRF node, an MQTT command, a retained state + file-registry, sweep arbitration, and a dedicated «Передавач» screen.

**Architecture:** TX **preempts** the sweep on the same bladeRF (no full-duplex — each role opens the device exclusively), exactly as SDR view mode does today. Operator drops video files into `/var/lib/fpv/tx/` on the Pi; the agent publishes a retained file list; the dashboard picks a file + frequency + params → `{tx:{...}}` on `fpv/<id>/rxcmd` → the agent frees the sweep's bladeRF, renders the clip to a cached SC16_Q11 `.bin`, loop-transmits until stop/deadline, then resumes the sweep. Frequency/gain retune live without re-render.

**Tech Stack:** Python 3 + numpy (agent), vanilla ES modules + `node --test` (dashboard), MQTT (mosquitto), ffmpeg (decode), bladeRF (TX).

## Global Constraints

- **Mirror existing patterns** — this feature is the TX twin of SDR view mode. `TxController` mirrors `agent/scan/view_controller.py:ViewController` (pending/has_pending/announce/run_view→run_tx, injected device fns, `reset` hook, never raises into callers). `txconfig.py` mirrors `agent/video/vconfig.py`. The `{tx}` routing branch mirrors the `{view}`/`{thresholds}` branches in `agent/scan/publisher.py:_on_message` (95-122). `publishTx`/`buildTxCommand`/reduce mirror `publishView`/`buildViewCommand`/the `view` reduce branch in `dashboard/public/mqtt-scan.js`. The «Передавач» screen mirrors the reconcile-based, live-safe structure of `dashboard/public/views/nodes.js`.
- **0 changes to the mosquitto ACL.** New retained topics `fpv/<id>/txstate` and `fpv/<id>/txfiles` fall under `pub`'s existing `topic write fpv/#`; `{tx}` rides the existing `fpv/<id>/rxcmd` (dashboard `topic write fpv/+/rxcmd`). Do NOT edit `mosquitto/acl`.
- **Module-name collision (CRITICAL):** `agent/scan/video_emit.py:9` does `from render import save_full_png` → in the fpv-scan process `render` == `agent/video/render.py`. Our `agent/tx/render.py` (functions `render`/`frame_to_iq`/`to_sc16q11`/`build_ffmpeg_decode_cmd`) can NOT be imported under the name `render` in that process. **Task 1 renames it to `tx_render.py`.** After that, all `agent/tx` module names (`tx_render`, `bladerf_tx`, `registry`, `txconfig`, `tx_controller`) are unique across the agent flat namespace and safe to co-import in `agent/scan/main.py`.
- **Command wire units:** command carries `freq_mhz`, `gain_db`, `deviation_mhz` (MHz, operator-friendly), `standard`, `secs`, `file`. `TxController` converts `deviation_mhz`→Hz for `render`. `secs` = render clip length (Phase-0 `max_secs`), NOT TX duration; TX loops until stop or `tx_max_s`.
- **Safety:** auto-stop deadline `tx_max_s` (default 120s) is enforced inside `run_tx`; the UI Start requires a confirm; `until_ts` drives an on-screen countdown.
- **Test commands (green each task):** agent/tx → `python -m pytest agent/tx/tests -q`; agent/scan → `python -m pytest agent/scan -q`; dashboard → `node --test` (from repo root). NEVER `pytest agent -q` (flat-layout duplicate module names — expected collection failure).
- Commit footer:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01JLJdXSvLMBojY7w2dcUwuN
  ```
- Branch: `feat/fpv-tx-phase-a` (spec committed there, `52a4e7f`).

## File structure
- Rename: `agent/tx/render.py` → `agent/tx/tx_render.py`; `agent/tx/tests/test_render.py` → `test_tx_render.py` (Task 1).
- Create: `agent/tx/txconfig.py`, `agent/tx/registry.py` (Task 2); `agent/tx/tx_controller.py` (Task 3).
- Modify: `agent/tx/bladerf_tx.py` (add `set_gain`, Task 3); `agent/scan/publisher.py` (Task 4); `agent/scan/main.py` (Task 5); `dashboard/public/mqtt-scan.js` (Task 6); `dashboard/public/app.js`, `dashboard/public/index.html`, `dashboard/public/fixtures.js` (Task 7).
- Create: `dashboard/public/views/tx.js` (Task 7).
- Tests: `agent/tx/tests/test_txconfig.py`, `test_registry.py`, `test_tx_controller.py`; add cases to `agent/scan/tests/test_publisher.py`, `test/mqtt-scan.test.js`.

---

### Task 1: Rename `agent/tx/render.py` → `tx_render.py` (unblock co-import)

**Files:**
- Rename: `agent/tx/render.py` → `agent/tx/tx_render.py`; `agent/tx/tests/test_render.py` → `agent/tx/tests/test_tx_render.py`
- Modify: `agent/tx/main.py`, `agent/tx/conftest.py`

**Interfaces:**
- Produces: module `tx_render` exporting `to_sc16q11`, `frame_to_iq`, `build_ffmpeg_decode_cmd`, `render` (unchanged signatures). Consumed by Task 3 (`TxController`) and Task 5 (main.py wiring).

- [ ] **Step 1: Rename the module + test file (preserve history)**

```bash
git mv agent/tx/render.py agent/tx/tx_render.py
git mv agent/tx/tests/test_render.py agent/tx/tests/test_tx_render.py
```
No code change inside `tx_render.py` — the functions keep their names.

- [ ] **Step 2: Update the test imports**

In `agent/tx/tests/test_tx_render.py`, replace every `from render import ...` with `from tx_render import ...` (there are imports at module top and inside the two render() tests). After edit, `grep -n "from render import" agent/tx/tests/test_tx_render.py` must return nothing; `from tx_render import` is present instead. `from bladerf_source import iq_from_sc16q11` stays.

- [ ] **Step 3: Simplify `agent/tx/conftest.py` (collision workaround now moot)**

Replace the whole file with the natural order (the `_HERE`-last hack was only to beat `agent/video/render.py`, which no longer shares our module name):
```python
import os
import sys
# tx (own modules: tx_render, bladerf_tx, ...) + ../video (synth) + ../scan (iq_from_sc16q11 for the round-trip test)
_HERE = os.path.dirname(__file__)
for _p in (_HERE, os.path.join(_HERE, "..", "video"), os.path.join(_HERE, "..", "scan")):
    _ap = os.path.abspath(_p)
    if _ap not in sys.path:
        sys.path.insert(0, _ap)
```

- [ ] **Step 4: Update `agent/tx/main.py` import + sys.path comment**

In `agent/tx/main.py`: change `from render import render` to `from tx_render import render`. Replace the sys.path block's collision comment (the `_HERE`/`_VIDEO` insert/append lines stay — `tx_render` still needs `../video` for `synth`) with:
```python
# tx_render needs ../video for `synth`; keep agent/tx importable for `tx_render`/`bladerf_tx`.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
_VIDEO = os.path.abspath(os.path.join(_HERE, "..", "video"))
if _VIDEO not in sys.path:
    sys.path.append(_VIDEO)
```

- [ ] **Step 5: Verify Phase-0 still green + compiles**

Run: `python -m pytest agent/tx/tests -q` → expect **9 passed**.
Run: `python -m py_compile agent/tx/tx_render.py agent/tx/main.py agent/tx/bladerf_tx.py` → no output.
Run (proves the CLI still resolves the renamed module): `cd agent/tx && python main.py render --help && cd ../..` → argparse help, no ImportError.

- [ ] **Step 6: Commit**

```bash
git add -A agent/tx
git commit -m "refactor(tx): rename render.py -> tx_render.py (avoid render name collision in fpv-scan)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JLJdXSvLMBojY7w2dcUwuN"
```

---

### Task 2: `agent/tx/txconfig.py` + `agent/tx/registry.py`

**Files:**
- Create: `agent/tx/txconfig.py`, `agent/tx/registry.py`
- Test: `agent/tx/tests/test_txconfig.py`, `agent/tx/tests/test_registry.py`

**Interfaces:**
- Produces: `TxConfig` dataclass + `load_tx_config(env=None)->TxConfig`; `scan_video_files(dir_path)->list[dict]` (each `{name, size, mtime}`). Consumed by Task 3 (controller defaults) and Task 5 (wiring + txfiles cadence).

- [ ] **Step 1: Write the failing tests**

Create `agent/tx/tests/test_txconfig.py`:
```python
from txconfig import TxConfig, load_tx_config


def test_defaults_disabled():
    c = load_tx_config({})
    assert c.tx_enabled is False
    assert c.tx_dir == "/var/lib/fpv/tx"
    assert c.tx_cache_bin == "/var/lib/fpv/tx/.cache/current.bin"
    assert c.tx_max_s == 120.0
    assert c.fs_hz == 20_000_000.0 and c.deviation_hz == 4_000_000.0
    assert c.standard == "PAL" and c.width == 640 and c.height == 512
    assert c.fps == 25 and c.secs == 3.0 and c.vbi_lines == 6 and c.gain_db == 30


def test_tx_enabled_truthy_parsing():
    assert load_tx_config({"TX_ENABLED": "1"}).tx_enabled is True
    for v in ("0", "false", "no", "", "  "):
        assert load_tx_config({"TX_ENABLED": v}).tx_enabled is False


def test_env_overrides():
    c = load_tx_config({
        "TX_ENABLED": "yes", "FPV_TX_DIR": "/data/vids", "FPV_TX_MAX_S": "45",
        "FPV_TX_DEVIATION_HZ": "3e6", "FPV_TX_STANDARD": "ntsc", "FPV_TX_GAIN_DB": "20",
        "FPV_TX_FPS": "30", "FPV_TX_SECS": "2",
    })
    assert c.tx_enabled is True and c.tx_dir == "/data/vids" and c.tx_max_s == 45.0
    assert c.deviation_hz == 3_000_000.0 and c.standard == "NTSC"   # upper-cased
    assert c.gain_db == 20 and c.fps == 30 and c.secs == 2.0
```

Create `agent/tx/tests/test_registry.py`:
```python
import os
from registry import scan_video_files


def test_lists_only_videos_sorted(tmp_path):
    (tmp_path / "b.mp4").write_bytes(b"12345")
    (tmp_path / "a.MKV").write_bytes(b"1")          # case-insensitive ext
    (tmp_path / "notes.txt").write_text("x")        # non-video: skipped
    (tmp_path / ".hidden.mp4").write_bytes(b"1")    # hidden: skipped
    (tmp_path / "sub").mkdir()                        # dir: skipped
    out = scan_video_files(str(tmp_path))
    assert [f["name"] for f in out] == ["a.MKV", "b.mp4"]      # sorted by name
    b = next(f for f in out if f["name"] == "b.mp4")
    assert b["size"] == 5 and isinstance(b["mtime"], int)


def test_missing_dir_returns_empty():
    assert scan_video_files("/no/such/dir/xyz") == []
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest agent/tx/tests/test_txconfig.py agent/tx/tests/test_registry.py -q`
Expected: FAIL — `No module named 'txconfig'` / `'registry'`.

- [ ] **Step 3: Implement `agent/tx/txconfig.py`**

```python
"""TX-generator config (env-driven), mirror of agent/video/vconfig.py. Role-gated by TX_ENABLED."""
import os
from dataclasses import dataclass


@dataclass
class TxConfig:
    tx_enabled: bool = False
    tx_dir: str = "/var/lib/fpv/tx"
    tx_cache_bin: str = "/var/lib/fpv/tx/.cache/current.bin"
    tx_max_s: float = 120.0            # auto-stop deadline (safety)
    fs_hz: float = 20_000_000.0
    deviation_hz: float = 4_000_000.0
    standard: str = "PAL"
    width: int = 640
    height: int = 512
    fps: int = 25
    secs: float = 3.0                  # render clip length (Phase-0 max_secs), NOT TX duration
    vbi_lines: int = 6
    gain_db: int = 30


def load_tx_config(env=None):
    env = os.environ if env is None else env
    c = TxConfig()
    if "TX_ENABLED" in env:
        c.tx_enabled = env["TX_ENABLED"].strip().lower() not in ("0", "false", "no", "")
    c.tx_dir = env.get("FPV_TX_DIR", c.tx_dir)
    c.tx_cache_bin = env.get("FPV_TX_CACHE_BIN", c.tx_cache_bin)
    if "FPV_TX_MAX_S" in env: c.tx_max_s = float(env["FPV_TX_MAX_S"])
    if "FPV_TX_FS_HZ" in env: c.fs_hz = float(env["FPV_TX_FS_HZ"])
    if "FPV_TX_DEVIATION_HZ" in env: c.deviation_hz = float(env["FPV_TX_DEVIATION_HZ"])
    if "FPV_TX_STANDARD" in env: c.standard = env["FPV_TX_STANDARD"].strip().upper()
    if "FPV_TX_WIDTH" in env: c.width = int(env["FPV_TX_WIDTH"])
    if "FPV_TX_HEIGHT" in env: c.height = int(env["FPV_TX_HEIGHT"])
    if "FPV_TX_FPS" in env: c.fps = int(env["FPV_TX_FPS"])
    if "FPV_TX_SECS" in env: c.secs = float(env["FPV_TX_SECS"])
    if "FPV_TX_VBI_LINES" in env: c.vbi_lines = int(env["FPV_TX_VBI_LINES"])
    if "FPV_TX_GAIN_DB" in env: c.gain_db = int(env["FPV_TX_GAIN_DB"])
    return c
```

- [ ] **Step 4: Implement `agent/tx/registry.py`**

```python
"""Video-file registry for the TX generator: list decodable clips in the TX dir."""
import os

VIDEO_EXTS = (".mp4", ".mkv", ".avi", ".mov", ".ts", ".m4v", ".webm")


def scan_video_files(dir_path):
    """[{name, size, mtime}] for video files directly in dir_path, sorted by name.
    Missing/unreadable dir -> []. Hidden files, non-video extensions, and subdirs skipped."""
    out = []
    try:
        entries = os.listdir(dir_path)
    except OSError:
        return out
    for name in entries:
        if name.startswith("."):
            continue
        if not name.lower().endswith(VIDEO_EXTS):
            continue
        path = os.path.join(dir_path, name)
        try:
            st = os.stat(path)
        except OSError:
            continue
        if not os.path.isfile(path):
            continue
        out.append({"name": name, "size": int(st.st_size), "mtime": int(st.st_mtime)})
    out.sort(key=lambda f: f["name"])
    return out
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest agent/tx/tests -q` → expect **13 passed** (9 Phase-0 + 4 new).

- [ ] **Step 6: Commit**

```bash
git add agent/tx/txconfig.py agent/tx/registry.py agent/tx/tests/test_txconfig.py agent/tx/tests/test_registry.py
git commit -m "feat(tx): TX config (TX_ENABLED role) + video-file registry

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JLJdXSvLMBojY7w2dcUwuN"
```

---

### Task 3: `agent/tx/tx_controller.py` + `BladeRfTxRadio.set_gain`

**Files:**
- Create: `agent/tx/tx_controller.py`
- Modify: `agent/tx/bladerf_tx.py` (add `set_gain`)
- Test: `agent/tx/tests/test_tx_controller.py`

**Interfaces:**
- Consumes: `tx_render.render` (Task 1), `bladerf_tx.open_bladerf_tx_radio`/`transmit_loop`, `TxConfig` (Task 2).
- Produces: `TxController(cfg, publisher, render_fn, open_tx_fn, transmit_fn, reset=None, clock=time.time, exists_fn=os.path.exists)` with `set_command(data)`, `pending()`, `has_pending()`, `run_tx(req)`, `announce()`. Mirrors `ViewController`. Consumed by Task 5 (main.py wiring). `BladeRfTxRadio.set_gain(db)` used by live retune.

- [ ] **Step 1: Add `set_gain` to `BladeRfTxRadio` (bladerf_tx.py)**

In `agent/tx/bladerf_tx.py`, add this method to `BladeRfTxRadio`, right after `set_frequency`:
```python
    def set_gain(self, db):
        self._radio.set_gain(self._ch, int(db))
```

- [ ] **Step 2: Write the failing tests**

Create `agent/tx/tests/test_tx_controller.py`:
```python
import os
from txconfig import TxConfig
from tx_controller import TxController


class _FakePublisher:
    def __init__(self): self.states = []
    def publish_txstate(self, ts, state): self.states.append(dict(state))


class _FakeRadio:
    def __init__(self): self.freqs = []; self.gains = []; self.closed = False
    def set_frequency(self, hz): self.freqs.append(hz)
    def set_gain(self, db): self.gains.append(db)
    def close(self): self.closed = True


def _cfg(tmp): return TxConfig(tx_enabled=True, tx_dir=str(tmp),
                               tx_cache_bin=str(tmp / "current.bin"), tx_max_s=100.0)


def _mk(tmp, **kw):
    renders = []; radios = []
    def render_fn(path, out_bin, **k): renders.append((path, out_bin, k))
    def open_tx_fn(freq_hz, fs_hz, gain_db, bw_hz, **k):
        r = _FakeRadio(); r.freqs.append(freq_hz); r.gains.append(gain_db); radios.append(r); return r
    transmit_calls = []
    def transmit_fn(radio, path, block_bytes, stop_check):
        transmit_calls.append(path)
        if kw.get("on_transmit"): kw["on_transmit"](len(transmit_calls))
        # returns immediately (as if stop_check tripped) unless a fake clock forces the deadline path
    pub = _FakePublisher()
    ctl = TxController(_cfg(tmp), pub, render_fn=render_fn, open_tx_fn=open_tx_fn,
                       transmit_fn=transmit_fn, reset=kw.get("reset", lambda: None),
                       clock=kw.get("clock", lambda: 1000), exists_fn=lambda p: True)
    return ctl, pub, renders, radios, transmit_calls


def test_start_renders_opens_transmits_then_idle(tmp_path):
    ctl, pub, renders, radios, tx = _mk(tmp_path)
    ctl.set_command({"tx": {"action": "start", "file": "clip.mp4", "freq_mhz": 5800, "gain_db": 25}})
    req = ctl.pending()
    assert req is not None
    ctl.run_tx(req)
    assert len(renders) == 1                              # rendered once
    assert renders[0][0] == os.path.join(str(tmp_path), "clip.mp4")   # file_path from tx_dir
    assert renders[0][1] == str(tmp_path / "current.bin")             # -> cache bin
    assert len(radios) == 1 and radios[0].freqs[0] == int(5800e6) and radios[0].gains[0] == 25
    assert radios[0].closed is True
    statuses = [s["status"] for s in pub.states]
    assert "rendering" in statuses and "transmitting" in statuses
    assert pub.states[-1]["active"] is False and pub.states[-1]["status"] == "idle"


def test_render_reused_when_key_unchanged(tmp_path):
    ctl, pub, renders, radios, tx = _mk(tmp_path)
    for _ in range(2):
        ctl.set_command({"tx": {"action": "start", "file": "clip.mp4", "freq_mhz": 5800}})
        ctl.run_tx(ctl.pending())
    assert len(renders) == 1                              # second start reused the cached .bin


def test_retune_freq_no_rerender(tmp_path):
    def inject(n):
        if n == 1:
            ctl_ref[0].set_command({"tx": {"action": "retune", "freq_mhz": 5760}})
    ctl_ref = [None]
    ctl, pub, renders, radios, tx = _mk(tmp_path, on_transmit=inject)
    ctl_ref[0] = ctl
    ctl.set_command({"tx": {"action": "start", "file": "clip.mp4", "freq_mhz": 5800}})
    ctl.run_tx(ctl.pending())
    assert len(renders) == 1                              # retune did NOT re-render
    assert len(tx) == 2                                   # transmit re-entered once after retune
    assert int(5760e6) in radios[0].freqs                 # live set_frequency applied


def test_deadline_auto_stops(tmp_path):
    now = [1000]
    def clk(): now[0] += 60; return now[0]               # each call advances 60s
    ctl, pub, renders, radios, tx = _mk(tmp_path, clock=clk)
    ctl.set_command({"tx": {"action": "start", "file": "clip.mp4", "freq_mhz": 5800}})
    ctl.run_tx(ctl.pending())
    # until_ts must be since_ts + tx_max_s(100); after the clock passes it, run_tx exits to idle
    tstate = next(s for s in pub.states if s["status"] == "transmitting")
    assert tstate["until_ts"] == tstate["since_ts"] + 100
    assert pub.states[-1]["status"] == "idle"


def test_bad_commands_never_throw_and_set_nothing(tmp_path):
    ctl, pub, renders, radios, tx = _mk(tmp_path)
    ctl.set_command({"tx": {"action": "start", "file": "clip.mp4", "freq_mhz": 99999}})  # bad freq
    ctl.set_command({"tx": {"action": "start", "freq_mhz": 5800}})                        # no file
    ctl.set_command({"tx": {}})                                                           # no action
    assert ctl.pending() is None
    # stop while idle just clears; start then stop -> nothing pending
    ctl.set_command({"tx": {"action": "start", "file": "clip.mp4", "freq_mhz": 5800}})
    ctl.set_command({"tx": {"action": "stop"}})
    assert ctl.pending() is None


def test_missing_file_rejected(tmp_path):
    ctl, pub, renders, radios, tx = _mk(tmp_path)
    ctl._exists = lambda p: False                          # simulate file gone
    ctl.set_command({"tx": {"action": "start", "file": "nope.mp4", "freq_mhz": 5800}})
    assert ctl.pending() is None
```

Note for the implementer: the fake `transmit_fn` returns immediately, so `run_tx`'s loop relies on there being no pending retune to break — after each `transmit_fn` returns, `run_tx` consumes a retune (if any) or exits. `test_deadline_auto_stops` uses a clock that advances past `until_ts`; ensure `run_tx` reads the clock for `since_ts`/`until_ts` and the transmit-return path checks no retune → exits (the deadline is enforced by `stop_check` inside a real `transmit_loop`; the fake returns immediately, and with no retune pending `run_tx` exits — the test asserts the `until_ts` math and the idle exit, not the loop's stop_check).

- [ ] **Step 3: Run to verify failure**

Run: `python -m pytest agent/tx/tests/test_tx_controller.py -q`
Expected: FAIL — `No module named 'tx_controller'`.

- [ ] **Step 4: Implement `agent/tx/tx_controller.py`**

```python
"""Dashboard-controlled TX generator mode. Twin of agent/scan/view_controller.py:ViewController.

The MQTT thread calls set_command(); the scan loop polls pending() between cycles and calls
run_tx(), which frees the sweep's bladeRF (reset), renders the clip once (cached by baked-param
key), opens the TX radio, and loop-transmits until stop / the tx_max_s deadline — then the sweep
resumes. Frequency/gain retune live without re-render. Never raises into callers."""
import logging
import os
import threading
import time

LOG = logging.getLogger("tx.controller")

FREQ_MIN_MHZ = 100.0
FREQ_MAX_MHZ = 6000.0            # bladeRF tuning range


class TxController:
    def __init__(self, cfg, publisher, render_fn, open_tx_fn, transmit_fn,
                 reset=None, clock=None, exists_fn=None):
        self._cfg = cfg
        self._publisher = publisher
        self._render_fn = render_fn          # tx_render.render(path, out_bin, **kw)
        self._open_tx_fn = open_tx_fn         # bladerf_tx.open_bladerf_tx_radio(freq_hz, fs_hz, gain, bw_hz)
        self._transmit_fn = transmit_fn       # bladerf_tx.transmit_loop(radio, path, block_bytes, stop_check)
        self._reset = reset or (lambda: None)
        self._clock = clock or time.time
        self._exists = exists_fn or os.path.exists
        self._lock = threading.Lock()
        self._pending = None                  # a validated start request dict
        self._retune = None                   # a live {freq_mhz?, gain_db?} to apply mid-session
        self._stop = threading.Event()
        self._last_render_key = None
        self._last = self._idle_state()       # for announce()

    # ---- command intake (MQTT thread) ----
    def set_command(self, data):
        tx = data.get("tx") if isinstance(data, dict) else None
        if not isinstance(tx, dict):
            LOG.warning("tx: ignoring malformed command %r", data)
            return
        action = tx.get("action")
        if action == "stop":
            with self._lock:
                self._pending = None
                self._retune = None
            self._stop.set()
            return
        if action == "retune":
            r = {}
            f = tx.get("freq_mhz")
            if isinstance(f, (int, float)) and FREQ_MIN_MHZ <= float(f) <= FREQ_MAX_MHZ:
                r["freq_mhz"] = float(f)
            g = tx.get("gain_db")
            if isinstance(g, (int, float)):
                r["gain_db"] = int(g)
            if r:
                with self._lock:
                    self._retune = r
                self._stop.set()              # break transmit_loop to apply; active-only (ignored if idle)
            return
        if action != "start":
            LOG.warning("tx: ignoring unknown action %r", action)
            return
        req = self._build_start(tx)
        if req is None:
            return
        with self._lock:
            self._pending = req
        self._stop.set()

    def _build_start(self, tx):
        file = tx.get("file")
        if not isinstance(file, str) or not file:
            LOG.warning("tx: start missing file"); return None
        path = os.path.join(self._cfg.tx_dir, file)
        if not self._exists(path):
            LOG.warning("tx: start file not found %r", path); return None
        f = tx.get("freq_mhz")
        if not isinstance(f, (int, float)) or not (FREQ_MIN_MHZ <= float(f) <= FREQ_MAX_MHZ):
            LOG.warning("tx: start bad freq_mhz %r", f); return None
        c = self._cfg
        g = tx.get("gain_db"); gain = int(g) if isinstance(g, (int, float)) else c.gain_db
        dv = tx.get("deviation_mhz")
        dev_hz = float(dv) * 1e6 if isinstance(dv, (int, float)) and dv > 0 else c.deviation_hz
        std = tx.get("standard"); standard = std.strip().upper() if isinstance(std, str) and std else c.standard
        sc = tx.get("secs"); secs = float(sc) if isinstance(sc, (int, float)) and sc > 0 else c.secs
        return {"file": file, "file_path": path, "freq_mhz": float(f), "gain_db": gain,
                "deviation_hz": dev_hz, "standard": standard, "secs": secs,
                "fs_hz": c.fs_hz, "width": c.width, "height": c.height, "fps": c.fps,
                "vbi_lines": c.vbi_lines}

    # ---- arbitration hooks (scan loop) ----
    def pending(self):
        with self._lock:
            p, self._pending = self._pending, None
        return p

    def has_pending(self):
        with self._lock:
            return self._pending is not None

    def _consume_retune(self):
        with self._lock:
            r, self._retune = self._retune, None
        return r

    # ---- session (scan loop, blocking) ----
    def run_tx(self, req):
        error = None
        radio = None
        freq = req["freq_mhz"]; gain = req["gain_db"]
        try:
            self._reset()                     # free the sweep's bladeRF before opening TX
            key = self._render_key(req)
            if key != self._last_render_key:
                self._pub(self._now(), True, "rendering", req, freq, gain, until_ts=None)
                self._render_fn(req["file_path"], self._cfg.tx_cache_bin,
                                standard=req["standard"], fs=req["fs_hz"],
                                deviation_hz=req["deviation_hz"], width=req["width"],
                                height=req["height"], fps=req["fps"], max_secs=req["secs"],
                                vbi_lines=req["vbi_lines"])
                self._last_render_key = key
            radio = self._open_tx_fn(int(freq * 1e6), int(req["fs_hz"]), int(gain), int(req["fs_hz"]))
            while True:
                self._stop.clear()
                since = self._now()
                until = since + int(self._cfg.tx_max_s)
                self._pub(since, True, "transmitting", req, freq, gain, until_ts=until, since_ts=since)
                deadline = float(until)
                stop_check = lambda: self._stop.is_set() or self._clock() >= deadline
                try:
                    self._transmit_fn(radio, self._cfg.tx_cache_bin, 32768 * 4, stop_check)
                except Exception as e:
                    LOG.exception("tx transmit crashed"); error = str(e); break
                r = self._consume_retune()
                if r is None:
                    break                     # stop / deadline -> back to sweep
                if "freq_mhz" in r:
                    freq = r["freq_mhz"]
                    try: radio.set_frequency(int(freq * 1e6))
                    except Exception: LOG.exception("tx retune set_frequency failed")
                if "gain_db" in r:
                    gain = r["gain_db"]
                    try: radio.set_gain(int(gain))
                    except Exception: LOG.exception("tx retune set_gain failed")
                LOG.info("tx retune -> %.1f MHz gain=%s", freq, gain)
        finally:
            if radio is not None:
                try: radio.close()
                except Exception: LOG.exception("tx radio close failed")
            self._pub(self._now(), False, "idle", req, None, None, until_ts=None, error=error)
            try: self._reset()                # leave the device clean for the next sweep
            except Exception: LOG.exception("tx: device reset failed")
            self._stop.clear()
        return error

    def announce(self):
        """(Re)publish last-known retained txstate — capability announce on (re)connect;
        also clears a stale active:true after a crash."""
        self._publish(self._last)

    # ---- helpers ----
    def _render_key(self, req):
        return (req["file"], req["standard"], req["fs_hz"], req["deviation_hz"],
                req["width"], req["height"], req["fps"], req["secs"], req["vbi_lines"])

    def _now(self):
        return int(self._clock())

    def _idle_state(self):
        return {"active": False, "status": "idle", "file": None, "freq_mhz": None,
                "gain_db": None, "deviation_mhz": None, "standard": None,
                "since_ts": None, "until_ts": None, "error": None}

    def _pub(self, ts, active, status, req, freq, gain, until_ts=None, since_ts=None, error=None):
        state = {"active": bool(active), "status": status,
                 "file": req.get("file"), "freq_mhz": freq, "gain_db": gain,
                 "deviation_mhz": (req.get("deviation_hz") / 1e6) if req.get("deviation_hz") else None,
                 "standard": req.get("standard"), "since_ts": since_ts, "until_ts": until_ts,
                 "error": error}
        self._last = state
        self._publish(state, ts)

    def _publish(self, state, ts=None):
        if self._publisher is None:
            return
        try:
            self._publisher.publish_txstate(ts if ts is not None else self._now(), state)
        except Exception:
            LOG.exception("tx state publish failed")
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest agent/tx/tests -q` → expect **20 passed** (13 + 7 new). Then `python -m py_compile agent/tx/tx_controller.py agent/tx/bladerf_tx.py`.

- [ ] **Step 6: Commit**

```bash
git add agent/tx/tx_controller.py agent/tx/bladerf_tx.py agent/tx/tests/test_tx_controller.py
git commit -m "feat(tx): TxController (render+loop-tx state machine, live retune, deadline) + radio.set_gain

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JLJdXSvLMBojY7w2dcUwuN"
```

---

### Task 4: `agent/scan/publisher.py` — `{tx}` routing + txstate/txfiles publishers

**Files:**
- Modify: `agent/scan/publisher.py`
- Test: `agent/scan/tests/test_publisher.py`

**Interfaces:**
- Consumes: nothing new (duck-typed callback).
- Produces: `publisher.on_tx_command` attribute, `publish_txstate(ts, state)`, `publish_txfiles(ts, files, dir)`, and a `{tx}` dispatch branch. Consumed by Task 5 wiring; `publish_txstate` is called by `TxController._publish` (Task 3).

- [ ] **Step 1: Write the failing tests** (mirror the existing `{view}`/`{thresholds}` routing tests)

In `agent/scan/tests/test_publisher.py`, add (adapt to the file's existing helpers for building a publisher + delivering a message — mirror the pattern already used to test `on_view_command`/`on_thresholds_command`):
```python
def test_on_message_routes_tx_and_not_rx(make_publisher):
    pub = make_publisher()
    seen = {}
    pub.on_tx_command = lambda d: seen.setdefault("tx", d)
    pub.on_command = lambda *a: seen.setdefault("rx", a)     # must NOT fire
    _deliver(pub, {"tx": {"action": "start", "file": "c.mp4", "freq_mhz": 5800}})
    assert seen.get("tx") == {"tx": {"action": "start", "file": "c.mp4", "freq_mhz": 5800}}
    assert "rx" not in seen


def test_on_message_tx_none_handler_is_safe(make_publisher):
    pub = make_publisher()
    pub.on_tx_command = None
    pub.on_command = lambda *a: (_ for _ in ()).throw(AssertionError("rx must not fire"))
    _deliver(pub, {"tx": {"action": "stop"}})               # no throw, no rx dispatch
```
> Implementer: use whatever `make_publisher`/`_deliver` (or inline `_on_message(client, userdata, msg)`) helper the existing view/threshold routing tests in this file already use — do not invent a new harness. If the file constructs a `msg` with `.payload = json.dumps(...)`, do the same.

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest agent/scan/tests/test_publisher.py -q -k tx`
Expected: FAIL — `on_tx_command` attribute missing / branch absent.

- [ ] **Step 3: Implement — `__init__` additions**

In `agent/scan/publisher.py` `__init__` (after `self._t_scancfg = ...`, line 55, and after the `self.on_thresholds_command = None` line 58):
```python
        self._t_txstate = f"fpv/{scanner_id}/txstate"
        self._t_txfiles = f"fpv/{scanner_id}/txfiles"
```
and:
```python
        self.on_tx_command = None       # set by the caller: fn(dict) — TX generator start/stop/retune
```

- [ ] **Step 4: Implement — `_on_message` branch**

In `_on_message`, insert BEFORE the `if self.on_command is None:` fallthrough (currently line 117), after the `thresholds` branch (line 116):
```python
        if "tx" in data:                # TX generator command — not routed to the RX5808 handler
            if self.on_tx_command is not None:
                try:
                    self.on_tx_command(data)
                except Exception:
                    LOG.exception("on_tx_command handler failed")
            return
```

- [ ] **Step 5: Implement — publishers**

After `publish_scancfg` (line 169), add:
```python
    def publish_txstate(self, ts, state):
        self._publish(
            self._t_txstate,
            {"scanner_id": self.scanner_id, "ts": ts, **state},
            self.QOS_DETECTION,
        )

    def publish_txfiles(self, ts, files, dir):
        self._publish(
            self._t_txfiles,
            {"scanner_id": self.scanner_id, "ts": ts, "files": files, "dir": dir},
            self.QOS_DETECTION,
        )
```

- [ ] **Step 6: Run tests**

Run: `python -m pytest agent/scan/tests/test_publisher.py -q` → all pass (existing + 2 new). Then the full package: `python -m pytest agent/scan -q` → no regressions.

- [ ] **Step 7: Commit**

```bash
git add agent/scan/publisher.py agent/scan/tests/test_publisher.py
git commit -m "feat(tx): publisher routes {tx} commands + publishes txstate/txfiles (0 ACL change)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JLJdXSvLMBojY7w2dcUwuN"
```

---

### Task 5: `agent/scan/main.py` — wire TxController + arbitration + txfiles cadence

**Files:**
- Modify: `agent/scan/main.py`

**Interfaces:**
- Consumes: `load_tx_config`, `scan_video_files` (Task 2), `TxController` (Task 3), `tx_render.render`, `bladerf_tx.open_bladerf_tx_radio`/`transmit_loop` (Task 1/Phase-0), `publisher.on_tx_command`/`publish_txfiles` (Task 4), the existing `_reset_bladerf_backend` (main.py:55-64).
- Produces: nothing consumed downstream (top-level wiring).

This is integration wiring — verified by `python -m py_compile`, the agent/scan suite staying green, and the manual over-WG gate. Mirror the view/threshold wiring exactly.

- [ ] **Step 1: Construct the controller (after the threshold_ctl block)**

In `agent/scan/main.py`, immediately AFTER the threshold controller block (after line 346 `publisher.on_thresholds_command = threshold_ctl.apply`), add:
```python
    tx_ctl = None
    txcfg = None
    try:
        if publisher is not None:
            _txdir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tx"))
            if _txdir not in sys.path:
                sys.path.append(_txdir)      # tx module names are unique; append so scan/video win any tie
            from txconfig import load_tx_config
            txcfg = load_tx_config()
            if txcfg.tx_enabled:
                from registry import scan_video_files          # noqa: F401 (used below + in loop)
                from tx_render import render as tx_render_fn
                from bladerf_tx import open_bladerf_tx_radio, transmit_loop
                from tx_controller import TxController
                tx_ctl = TxController(
                    txcfg, publisher, render_fn=tx_render_fn,
                    open_tx_fn=open_bladerf_tx_radio, transmit_fn=transmit_loop,
                    reset=_reset_bladerf_backend,               # free the sweep's bladeRF before/after TX
                )
                publisher.on_tx_command = tx_ctl.set_command
                LOG.info("TX generator enabled (dir=%s max=%.0fs)", txcfg.tx_dir, txcfg.tx_max_s)
    except Exception:
        LOG.exception("TX generator init failed; continuing without it")
```
> `os` and `sys` are already imported at the top of main.py (they are used by `_reset_bladerf_backend` and elsewhere) — confirm and do not re-import. `tx_render`'s `from synth import ...` resolves because `../video` is already on `sys.path` (the video-emitter/view blocks imported `video_emit` above).

- [ ] **Step 2: Chain the announce into on_connected (after the threshold composition)**

AFTER the threshold `on_connected` composition (after line 359, `publisher.on_connected = _on_connected`), add:
```python
    if tx_ctl is not None:
        prev_tx = publisher.on_connected
        def _on_connected_tx():
            if prev_tx is not None:
                prev_tx()
            tx_ctl.announce()                                  # retained capability announce
            try:
                publisher.publish_txfiles(int(time.time()), scan_video_files(txcfg.tx_dir), txcfg.tx_dir)
            except Exception:
                LOG.exception("txfiles announce failed")
        publisher.on_connected = _on_connected_tx
```

- [ ] **Step 3: Announce right after connect**

In the `if publisher is not None:` connect block, after `threshold_ctl.announce()` (line 365), add:
```python
            if tx_ctl is not None:
                tx_ctl.announce()
                try:
                    publisher.publish_txfiles(int(time.time()), scan_video_files(txcfg.tx_dir), txcfg.tx_dir)
                except Exception:
                    LOG.exception("txfiles initial publish failed")
```

- [ ] **Step 4: Main-loop arbitration + periodic txfiles**

Replace the top of the `while True:` body (lines 371-384 region). Add a `last_txfiles = 0.0` initializer next to `backoff`/`blade_fails` (before the loop, ~line 369-370), then inside the `try:` at the loop top, BEFORE the `req = view.pending()` line (373):
```python
            if tx_ctl is not None:
                now = time.time()
                if now - last_txfiles > 60.0:               # cheap dir re-scan so new files appear
                    try:
                        publisher.publish_txfiles(int(now), scan_video_files(txcfg.tx_dir), txcfg.tx_dir)
                    except Exception:
                        LOG.exception("txfiles periodic publish failed")
                    last_txfiles = now
                treq = tx_ctl.pending()
                if treq is not None:
                    LOG.info("entering TX @ %.1f MHz (sweep paused)", treq["freq_mhz"])
                    tx_ctl.run_tx(treq)                     # frees/reopens the bladeRF via reset internally
                    LOG.info("TX ended; sweep resumes")
                    continue
```
And extend the sweep's abort hook (line 384) so a pending TX also preempts a running cycle:
```python
            payload = run_cycle(cfg, now_ts=int(time.time()), publisher=publisher,
                                emitter=emitter, controller=controller,
                                abort=(lambda: (view is not None and view.has_pending())
                                       or (tx_ctl is not None and tx_ctl.has_pending())))
```
And after the cycle (near line 390, the `if view is not None and view.has_pending(): continue`), add a sibling:
```python
            if tx_ctl is not None and tx_ctl.has_pending():
                continue    # completed cycle already published; enter the pending TX now
```

- [ ] **Step 5: Verify compile + no agent/scan regressions**

Run: `python -m py_compile agent/scan/main.py` → no output.
Run: `python -m pytest agent/scan -q` → same pass count as before this task (no regressions; the wiring is runtime-only, not import-time for tests).

- [ ] **Step 6: Commit**

```bash
git add agent/scan/main.py
git commit -m "feat(tx): wire TxController into fpv-scan (arbitration preempts sweep, txfiles cadence)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JLJdXSvLMBojY7w2dcUwuN"
```

---

### Task 6: `dashboard/public/mqtt-scan.js` — buildTxCommand + publishTx + reduce

**Files:**
- Modify: `dashboard/public/mqtt-scan.js`
- Test: `test/mqtt-scan.test.js`

**Interfaces:**
- Produces: `buildTxCommand(action, params)`, `MqttScanClient.publishTx(id, action, params)`, store fields `s.txstate`/`s.txfiles`, subscriptions `fpv/+/txstate`/`fpv/+/txfiles`. Consumed by Task 7 (screen + ctx accessors).

- [ ] **Step 1: Write the failing tests** (add to `test/mqtt-scan.test.js`, mirroring its existing `buildViewCommand`/`reduce view` tests)

```javascript
test('buildTxCommand start/stop/retune', () => {
  assert.deepEqual(buildTxCommand('stop'), { tx: { action: 'stop' } });
  assert.deepEqual(buildTxCommand('retune', { freqMhz: 5760, gainDb: 20 }),
    { tx: { action: 'retune', freq_mhz: 5760, gain_db: 20 } });
  const s = buildTxCommand('start', { file: 'c.mp4', freqMhz: '5800', gainDb: 25, deviationMhz: 4, standard: 'PAL', secs: 2 });
  assert.deepEqual(s, { tx: { action: 'start', file: 'c.mp4', freq_mhz: 5800, gain_db: 25, deviation_mhz: 4, standard: 'PAL', secs: 2 } });
});

test('reduce txstate + txfiles', () => {
  let store = emptyStore();
  store = reduce(store, 'fpv/bladerf/txstate', JSON.stringify({
    ts: 5, active: true, status: 'transmitting', file: 'c.mp4', freq_mhz: 5800, gain_db: 25,
    deviation_mhz: 4, standard: 'PAL', since_ts: 100, until_ts: 220, error: null }));
  assert.equal(store.bladerf.txstate.active, true);
  assert.equal(store.bladerf.txstate.status, 'transmitting');
  assert.equal(store.bladerf.txstate.until_ts, 220);
  store = reduce(store, 'fpv/bladerf/txfiles', JSON.stringify({ ts: 6, dir: '/var/lib/fpv/tx', files: [{ name: 'c.mp4', size: 9, mtime: 1 }] }));
  assert.equal(store.bladerf.txfiles.files.length, 1);
  assert.equal(store.bladerf.txfiles.files[0].name, 'c.mp4');
});
```
Add `buildTxCommand` to the existing import line from `../dashboard/public/mqtt-scan.js` at the top of the test file.

- [ ] **Step 2: Run to verify failure**

Run: `node --test test/mqtt-scan.test.js`
Expected: FAIL — `buildTxCommand` is not exported / `txstate` undefined.

- [ ] **Step 3: Implement — `buildTxCommand`** (after `buildViewCommand`, ~line 23)

```javascript
// Build a TX-generator command for fpv/<id>/rxcmd
// ({tx:{action:'start',file,freq_mhz,gain_db?,deviation_mhz?,standard?,secs?}} | {tx:{action:'stop'}}
//  | {tx:{action:'retune',freq_mhz?,gain_db?}}).
export function buildTxCommand(action, params = {}) {
  if (action === 'stop') return { tx: { action: 'stop' } };
  if (action === 'retune') {
    const tx = { action: 'retune' };
    if (Number.isFinite(Number(params.freqMhz))) tx.freq_mhz = Number(params.freqMhz);
    if (Number.isFinite(Number(params.gainDb))) tx.gain_db = Number(params.gainDb);
    return { tx };
  }
  const tx = { action: 'start', file: params.file, freq_mhz: Number(params.freqMhz) };
  if (Number.isFinite(Number(params.gainDb))) tx.gain_db = Number(params.gainDb);
  if (Number.isFinite(Number(params.deviationMhz))) tx.deviation_mhz = Number(params.deviationMhz);
  if (params.standard) tx.standard = params.standard;
  if (Number.isFinite(Number(params.secs))) tx.secs = Number(params.secs);
  return { tx };
}
```

- [ ] **Step 4: Implement — `ensure()` fields + reduce regex + branches**

In `ensure()` (line 36), add `txstate: null, txfiles: null` to the initial object.
In `reduce()` regex (line 45), extend the alternation to include `txstate|txfiles`:
```javascript
  const m = /^fpv\/([^/]+)\/(spectrum|detection|status|video|rxtune|view|telemetry|scancfg|txstate|txfiles)$/.exec(topic || '');
```
Add branches (after the `scancfg` branch, before `spectrum`):
```javascript
  } else if (kind === 'txstate') {
    s.txstate = {
      ts: data.ts || 0,
      active: !!data.active,
      status: data.status || 'idle',
      file: data.file || null,
      freq_mhz: data.freq_mhz == null ? null : Number(data.freq_mhz),
      gain_db: data.gain_db == null ? null : Number(data.gain_db),
      deviation_mhz: data.deviation_mhz == null ? null : Number(data.deviation_mhz),
      standard: data.standard || null,
      since_ts: data.since_ts == null ? null : Number(data.since_ts),
      until_ts: data.until_ts == null ? null : Number(data.until_ts),
      error: data.error || null,
    };
  } else if (kind === 'txfiles') {
    s.txfiles = {
      ts: data.ts || 0,
      dir: data.dir || null,
      files: Array.isArray(data.files) ? data.files : [],
    };
```

- [ ] **Step 5: Implement — subscribe + publishTx**

In `connect()` subscribe list (line 135), append `'fpv/+/txstate', 'fpv/+/txfiles'`.
After `publishThresholds` (line 168), add:
```javascript
  // TX-generator command — same rxcmd topic, NOT retained (a retained start would re-enter TX on every Pi reconnect).
  publishTx(id, action, params) {
    if (!this.client || !id) return;
    if (action === 'start' && !Number.isFinite(Number(params && params.freqMhz))) return;
    this.client.publish(`fpv/${id}/rxcmd`, JSON.stringify(buildTxCommand(action, params)),
      { qos: 1, retain: false });
  }
```

- [ ] **Step 6: Run tests**

Run: `node --test test/mqtt-scan.test.js` → pass (existing + 2 new). Then `node --test` (whole suite) → no regressions.

- [ ] **Step 7: Commit**

```bash
git add dashboard/public/mqtt-scan.js test/mqtt-scan.test.js
git commit -m "feat(tx): dashboard mqtt — buildTxCommand/publishTx + txstate/txfiles subscribe+reduce

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JLJdXSvLMBojY7w2dcUwuN"
```

---

### Task 7: «Передавач» screen (`views/tx.js`) + route/nav/accessors + preview fixtures

**Files:**
- Create: `dashboard/public/views/tx.js`
- Modify: `dashboard/public/app.js`, `dashboard/public/index.html`, `dashboard/public/fixtures.js`
- Visual gate: dev-preview at `?preview=1#/tx` (desktop + mobile)

**Interfaces:**
- Consumes: `ctx.scanStore()` (per-node `txstate`/`txfiles`), `ctx.onTxStart/onTxStop/onTxRetune`, `scanClient.publishTx` (Task 6).

- [ ] **Step 1: Add ctx accessors + route + import (app.js)**

Add the import near the other view imports (after line 17):
```javascript
import { render as renderTx } from '/views/tx.js';
```
Add ctx accessors (in the `ctx` object, after `onViewStop`, line 124):
```javascript
  onTxStart: (id, params) => { if (!PREVIEW) scanClient.publishTx(id, 'start', params); },
  onTxStop: (id) => { if (!PREVIEW) scanClient.publishTx(id, 'stop'); },
  onTxRetune: (id, params) => { if (!PREVIEW) scanClient.publishTx(id, 'retune', params); },
```
Add the route (in `routes`, after the `#/nodes` entry, line 136):
```javascript
  { hash: '#/tx', label: 'Передавач', icon: '📡', section: 'screen-tx', mount: renderTx, live: true },
```

- [ ] **Step 2: Add the screen container (index.html)**

The nav is auto-generated from `routes` by `router.js` (it builds `.nav-item`s from the routes array and toggles `.screen` sections by `id`), so ONLY a section container is needed. In `dashboard/public/index.html`, after the `#screen-frames` section (line 27), add:
```html
      <section id="screen-tx" class="screen hidden"></section>
```
The route's `section: 'screen-tx'` (Step 1) matches this `id`. No nav markup.

- [ ] **Step 3: Implement `dashboard/public/views/tx.js`** (reconcile-based, live-safe — mirror `views/nodes.js`)

```javascript
// dashboard/public/views/tx.js — «Передавач»: dashboard-controlled TX generator, one card per
// TX-capable node (store[id].txstate != null). RECONCILE-BASED (build-once skeleton + in-place live
// updates), like views/nodes.js — route is live:true so a full innerHTML rebuild each tick would wipe
// the operator's typed freq/gain and close the file <select>. Start requires a confirm (RF safety).
import { el, pip, escapeHtml } from '/views/components.js';

const STANDARDS = ['PAL', 'NTSC'];

function fmtCountdown(untilTs, nowS) {
  if (untilTs == null) return '';
  const left = Math.max(0, untilTs - nowS);
  const m = Math.floor(left / 60), s = left % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

// Build one node's TX card once. Live fields carry data-role markers for updateCard().
function buildCard(id, ctx) {
  const card = el('div', 'tx-card');
  card.dataset.id = id;
  card.innerHTML = `<div class="tx-head"><span data-role="pip"></span>
      <span class="tx-title mono">${escapeHtml(id)}</span></div>
    <div class="tx-banner" data-role="banner"></div>
    <div class="tx-form">
      <label>Файл<select class="tx-file" data-role="file"></select></label>
      <label>Частота, МГц<input class="tx-freq" type="number" min="100" max="6000" step="1" placeholder="МГц"></label>
      <label>Gain<input class="tx-gain" type="number" min="0" max="60" step="1"></label>
      <label>Девіація, МГц<input class="tx-dev" type="number" min="0.5" max="10" step="0.5"></label>
      <label>Стандарт<select class="tx-std">${STANDARDS.map((s) => `<option>${s}</option>`).join('')}</select></label>
    </div>
    <div class="tx-actions">
      <button type="button" class="btn tx-start">▶ Старт</button>
      <button type="button" class="btn tx-stop" data-role="stop">■ Стоп</button>
      <span class="tx-err" data-role="err"></span>
    </div>`;

  const files = card.querySelector('[data-role=file]');
  const freq = card.querySelector('.tx-freq');
  const gain = card.querySelector('.tx-gain');
  const dev = card.querySelector('.tx-dev');
  const std = card.querySelector('.tx-std');

  card.querySelector('.tx-start').addEventListener('click', () => {
    const file = files.value;
    const f = Number(freq.value);
    if (!file || !Number.isFinite(f) || f < 100 || f > 6000) return;
    // eslint-disable-next-line no-alert
    if (!(typeof confirm === 'function') || confirm(`Почати передачу «${file}» на ${f} МГц?`)) {
      ctx.onTxStart(id, {
        file, freqMhz: f,
        gainDb: gain.value === '' ? undefined : Number(gain.value),
        deviationMhz: dev.value === '' ? undefined : Number(dev.value),
        standard: std.value,
      });
    }
  });
  card.querySelector('.tx-stop').addEventListener('click', () => ctx.onTxStop(id));
  return card;
}

// Refresh live fields only; never rebuild the <select>/inputs the operator is using. The file
// <select> options are reconciled by value so a new txfiles list doesn't reset the current pick.
function updateCard(card, id, store, nowS) {
  const s = store[id] || {};
  const tx = s.txstate || {};
  card.querySelector('[data-role=pip]').innerHTML = pip(!!s.online);

  const sel = card.querySelector('[data-role=file]');
  const want = (s.txfiles && s.txfiles.files ? s.txfiles.files : []).map((f) => f.name);
  const have = [...sel.options].map((o) => o.value);
  if (want.join('') !== have.join('')) {
    const cur = sel.value;
    sel.innerHTML = want.map((n) => `<option>${escapeHtml(n)}</option>`).join('');
    if (want.includes(cur)) sel.value = cur;               // preserve the operator's pick
  }

  const active = !!tx.active;
  const banner = card.querySelector('[data-role=banner]');
  if (active && tx.status === 'transmitting') {
    banner.className = 'tx-banner on';
    banner.textContent = `📡 TX НА ${tx.freq_mhz} МГц · ${tx.file || ''} · ⏱ ${fmtCountdown(tx.until_ts, nowS)}`;
  } else if (tx.status === 'rendering') {
    banner.className = 'tx-banner rendering';
    banner.textContent = `⚙ Рендер «${tx.file || ''}»…`;
  } else {
    banner.className = 'tx-banner';
    banner.textContent = 'очікування';
  }
  card.querySelector('[data-role=stop]').disabled = !(active || tx.status === 'rendering');
  card.querySelector('[data-role=err]').textContent = tx.error || '';
}

export function render(container, ctx) {
  container.className = 'screen screen-pad';
  let root = container.querySelector('.tx-root');
  if (!root) {
    container.innerHTML = '';
    container.appendChild(el('div', 'label-caps', 'ПЕРЕДАВАЧ'));
    root = el('div', 'tx-root');
    container.appendChild(root);
  }
  const store = ctx.scanStore();
  const nowS = Math.floor(Date.now() / 1000);
  const ids = Object.keys(store).filter((id) => store[id] && store[id].txstate);

  if (!ids.length) {
    root.innerHTML = '<p class="muted">Немає TX-здатних вузлів. Увімкни TX_ENABLED на bladeRF-ноді.</p>';
    return;
  }
  // Drop cards for nodes that vanished.
  const existing = new Map();
  for (const child of [...root.children]) {
    const id = child.dataset && child.dataset.id;
    if (!id) { child.remove(); continue; }
    if (!ids.includes(id)) child.remove(); else existing.set(id, child);
  }
  for (const id of ids) {
    let card = existing.get(id);
    if (!card) { card = buildCard(id, ctx); root.appendChild(card); }
    updateCard(card, id, store, nowS);
  }
}
```

- [ ] **Step 4: Add preview fixtures (fixtures.js)**

`dashboard/public/fixtures.js` has a `scanStore` with a `bladerf` entry (active — after its `scancfg:` line ~34) and a `hackrf` entry (idle — after its `view:` line ~43). Add `txstate`+`txfiles` to BOTH to exercise both banner states (shape matches `reduce()` from Task 6). To `bladerf` (transmitting):
```javascript
    txstate: { ts:NOW, active:true, status:'transmitting', file:'demo.mp4', freq_mhz:5800, gain_db:30, deviation_mhz:4, standard:'PAL', since_ts:NOW-30, until_ts:NOW+90, error:null },
    txfiles: { ts:NOW, dir:'/var/lib/fpv/tx', files:[{ name:'demo.mp4', size:180000000, mtime:NOW-3600 }, { name:'test-bars.mp4', size:90000000, mtime:NOW-7200 }] },
```
To `hackrf` (idle):
```javascript
    txstate: { ts:NOW, active:false, status:'idle', file:null, freq_mhz:null, gain_db:null, deviation_mhz:null, standard:null, since_ts:null, until_ts:null, error:null },
    txfiles: { ts:NOW, dir:'/var/lib/fpv/tx', files:[{ name:'demo.mp4', size:180000000, mtime:NOW-3600 }] },
```
(`NOW` is the fixture's time base, already defined at the top of the file.)

- [ ] **Step 5: Static checks**

Run: `node --check dashboard/public/views/tx.js dashboard/public/app.js` → no output.
Run: `node --test` (whole JS suite) → no regressions.

- [ ] **Step 6: Visual gate (dev-preview, desktop + mobile)**

Start the dev-preview server (read `dashboard/dev-serve.mjs` for how it's invoked and its port) on a FRESH port, then with playwright:
1. Navigate to `http://localhost:<port>/?preview=1#/tx`.
2. Desktop (1280×800): screenshot. Confirm the «Передавач» screen renders one card per fixture TX node with: file `<select>`, freq/gain/deviation inputs, standard `<select>`, Старт/Стоп; the transmitting fixture shows the red `📡 TX НА … · ⏱ m:ss` banner and an enabled Стоп; the idle node shows «очікування» and a disabled Стоп.
3. Mobile (390×844): resize, screenshot. Confirm no horizontal page scroll and the form/cards stack cleanly (the merged mobile-responsive rules apply). Fix `styles.css` only if the TX card overflows (mirror the `.thresholds-panel`/`.table-scroll` box-sizing rules already there).
4. Sanity: `window.__rerender` (or the harness's re-render hook) still updates the banner countdown without wiping a typed freq value — type into `.tx-freq`, trigger a tick, confirm the value persists (reconcile-safety).

Attach/save the desktop + mobile screenshots. If anything is broken, fix `tx.js`/`styles.css` and re-shoot.

- [ ] **Step 7: Commit**

```bash
git add dashboard/public/views/tx.js dashboard/public/app.js dashboard/public/index.html dashboard/public/fixtures.js dashboard/public/styles.css
git commit -m "feat(tx): «Передавач» screen — per-node TX control, live banner, confirm-on-start

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JLJdXSvLMBojY7w2dcUwuN"
```

---

## Manual acceptance (post-merge, hardware + over-WG)
1. **Deploy** — Pi (bladeRF node): `git pull`; drop-in `TX_ENABLED=1` (+ optional `FPV_TX_*`) on the `fpv-scan` unit; `mkdir -p /var/lib/fpv/tx` and drop a test clip; `systemctl restart fpv-scan`. Server (traefik): `git pull` + build+recreate `dashboard` (`--no-deps`; wg-easy/mediamtx/mosquitto untouched). 0 ACL/MediaMTX change.
2. **Registry** — the bladeRF node appears on «Передавач» with the clip in the file dropdown (retained `txfiles`).
3. **TX** — pick file + a 5.8 channel the RX5808 can tune + gain → confirm Старт → txstate goes rendering→transmitting; RX5808 tuned there → **picture on the grabber**. Retune frequency live → picture follows without a re-render pause. Стоп (or wait for `tx_max_s`) → TX stops, sweep resumes (spectrum returns).
4. If the picture tears/rolls at frame rate → the per-frame phase-reset limitation ([[fpv-tx-generator]] gate note) — a render-internals fix, contracts unchanged.

## Self-review (spec coverage)
- ✅ Role gate `TX_ENABLED` + config (Task 2); TX preempts sweep, frees/reopens bladeRF via `reset` (Task 3 run_tx + Task 5 arbitration).
- ✅ File registry `/var/lib/fpv/tx/` → retained `fpv/<id>/txfiles` (Task 2 registry + Task 5 cadence + Task 6 reduce + Task 7 dropdown).
- ✅ Command `{tx}` on `rxcmd` non-retained + retained `txstate` (Task 4 routing/publishers + Task 3 state machine + Task 6 buildTxCommand/publishTx/reduce). 0 ACL change.
- ✅ On-demand render with cache key; live freq/gain retune without re-render (Task 3 `_render_key` + retune loop + Task 3 `set_gain`).
- ✅ Dedicated «Передавач» screen with file/freq/gain/deviation/standard, red TX banner + countdown, confirm-on-start (Task 7).
- ✅ Safety: `tx_max_s` auto-stop deadline enforced in run_tx; confirm-on-start; single exclusive transmitter (Task 3 + Task 7).
- ✅ Module-collision resolved by renaming render.py→tx_render.py (Task 1).
- ✅ Tests: txconfig/registry/tx_controller (agent/tx), publisher routing (agent/scan), buildTxCommand/reduce (dashboard), visual gate; manual hardware/over-WG gate documented.
