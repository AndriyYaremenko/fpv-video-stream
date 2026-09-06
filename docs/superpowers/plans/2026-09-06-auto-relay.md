# Auto-Relay (свіп → захоплення → TX на 5771) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Коли режим озброєно, агент після кожного свіп-циклу бере найсильніше синхро-локнуте аналогове відео й ретранслює його IQ (RX джерело → AGC → TX ціль 5771) поки джерело живе; оператор озброює/знімає й задає ціль із дашборда.

**Architecture:** Новий пакет `agent/relay` (config / bladeRF full-duplex + AGC / контролер) — структурне дзеркало `agent/tx`. `agent/scan/main.py` збирає синхро-локнуті хіти в `run_cycle` і віддає їх контролеру; головний цикл серіалізує relay після TX/view, як інші ролі. `publisher.py` маршрутизує `{relay:…}` і публікує retained `relaystate`. Дашборд отримує блок «Авто-ретрансляція» на картці ноди екрана «Передавач».

**Tech Stack:** Python 3.12 (dev) / 3.14 (orange_pi6), numpy, libbladeRF python binding (`bladerf`), paho-mqtt 2.x; дашборд — vanilla ES modules, `node --test`.

**Spec:** `docs/superpowers/specs/2026-09-06-auto-relay-design.md`

## Global Constraints

- Нових залежностей НЕ додавати (лише numpy, що вже є). `bladerf` імпортується ТІЛЬКИ всередині `open_bladerf_relay_radio`.
- Тести запускати по-пакетно: `cd agent/relay && python -m pytest tests -q`, `cd agent/scan && python -m pytest tests -q`, `cd agent/tx && python -m pytest tests -q`, `npm test` (з кореня). `pytest agent -q` НЕ працює (flat-layout дублі імен).
- Контролер **ніколи не кидає** в caller; усе залізо інжектоване (тести на фейках без bladeRF/MQTT).
- Контракти (точно): команда `{"relay":{"action":"arm","dst_mhz":5771}}` / `{"relay":{"action":"disarm"}}` на `fpv/<id>/rxcmd` (non-retained); стан retained `fpv/<id>/relaystate` = `{"scanner_id","ts","armed","active","status":"idle"|"relaying","src_mhz","band","dst_mhz","since_ts","until_ts","rx_level_db","error"}`.
- Env-дефолти (точно): `RELAY_ENABLED=0`, `RELAY_ARMED=0`, `RELAY_DST_MHZ=5771`, `RELAY_MAX_S=600`, `RELAY_GUARD_MHZ=25`, `RELAY_LOST_DB=12`, `RELAY_LOST_S=5`, `RELAY_FS_HZ=20000000`, `RELAY_RX_GAIN_DB=40`, `RELAY_TX_GAIN_DB=60`, `RELAY_AGC_TARGET=1600`; `block_samples=32768` (не env).
- AGC (точно): `peak=max|iq|` (0→1.0); `gain = min(gain*0.9 + (target/peak)*0.1, 64.0)`; `out=clip(iq*gain, -2047, 2047).astype(int16)`; `rms` — СИРОГО входу.
- Втрата джерела: опорний RMS = медіана перших 1.0 с; «нижче» = `20*log10(ref/rms) >= lost_db`; `lost` після `lost_s` безперервно нижче.
- Пріоритет ручних команд: relay не стартує і зупиняється, якщо `manual_pending()` True (view/TX pending).
- UI-рядки українською: «⇄ Авто-ретрансляція», «Ціль, МГц», «⚡ Озброїти», «■ Зняти», «озброєно · чекаю синхро-лок», «вимкнено».
- Коміти: `git commit` з trailer'ами `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` і `Claude-Session: https://claude.ai/code/session_017M7uRRgtWJoQjK2rrHR5GS`. Гілка `feat/auto-relay` (вже існує, спека закомічена).

---

## File Structure

| файл | відповідальність |
|---|---|
| `agent/relay/__init__.py`, `agent/relay/tests/__init__.py` | пакетні маркери (порожні) |
| `agent/relay/conftest.py` | sys.path: власна тека (як `agent/tx/conftest.py`) |
| `agent/relay/relayconfig.py` | `RelayConfig` + `load_relay_config(env)` |
| `agent/relay/bladerf_relay.py` | `agc_block`, `relay_loop`, `BladeRfRelayRadio`, `open_bladerf_relay_radio` |
| `agent/relay/relay_controller.py` | `RelayController` (arm/disarm, вибір джерела, сесія, стан) |
| `agent/scan/publisher.py` | роутинг `relay`, `publish_relaystate` |
| `agent/scan/video_emit.py` | `last_sync_snr_db` |
| `agent/scan/main.py` | `run_cycle(..., relay)` → `update_hits`; init + арбітрація в `main()` |
| `dashboard/public/mqtt-scan.js` | reduce `relaystate`, `buildRelayCommand`, `publishRelay`, підписка |
| `dashboard/public/views/tx.js` | блок «Авто-ретрансляція» у картці |
| `dashboard/public/app.js` | `onRelayArm/onRelayDisarm` |
| `dashboard/public/fixtures.js`, `dashboard/public/styles.css` | превʼю-дані, стилі |

---

### Task 1: `agent/relay` пакет + `relayconfig.py`

**Files:**
- Create: `agent/relay/__init__.py` (порожній), `agent/relay/tests/__init__.py` (порожній), `agent/relay/conftest.py`, `agent/relay/relayconfig.py`
- Test: `agent/relay/tests/test_relayconfig.py`

**Interfaces:**
- Produces: `RelayConfig` dataclass (поля: `relay_enabled: bool, armed: bool, dst_mhz: float, max_s: float, guard_mhz: float, lost_db: float, lost_s: float, fs_hz: float, rx_gain_db: int, tx_gain_db: int, agc_target: float, block_samples: int`), `load_relay_config(env=None) -> RelayConfig`.

- [ ] **Step 1: Створити пакет і conftest**

`agent/relay/__init__.py` та `agent/relay/tests/__init__.py` — порожні файли.

`agent/relay/conftest.py`:
```python
import os
import sys
# relay (own flat modules: relayconfig, bladerf_relay, relay_controller) on sys.path for tests
_HERE = os.path.abspath(os.path.dirname(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
```

- [ ] **Step 2: Failing test**

`agent/relay/tests/test_relayconfig.py`:
```python
from relayconfig import RelayConfig, load_relay_config


def test_defaults_disabled():
    c = load_relay_config({})
    assert c.relay_enabled is False and c.armed is False
    assert c.dst_mhz == 5771.0 and c.max_s == 600.0 and c.guard_mhz == 25.0
    assert c.lost_db == 12.0 and c.lost_s == 5.0
    assert c.fs_hz == 20_000_000.0 and c.rx_gain_db == 40 and c.tx_gain_db == 60
    assert c.agc_target == 1600.0 and c.block_samples == 32768


def test_truthy_parsing():
    assert load_relay_config({"RELAY_ENABLED": "1"}).relay_enabled is True
    assert load_relay_config({"RELAY_ARMED": "yes"}).armed is True
    for v in ("0", "false", "no", "", "  "):
        assert load_relay_config({"RELAY_ENABLED": v}).relay_enabled is False


def test_env_overrides():
    c = load_relay_config({
        "RELAY_ENABLED": "1", "RELAY_DST_MHZ": "5865", "RELAY_MAX_S": "30", "RELAY_GUARD_MHZ": "10",
        "RELAY_LOST_DB": "6", "RELAY_LOST_S": "2.5", "RELAY_FS_HZ": "1e7",
        "RELAY_RX_GAIN_DB": "30", "RELAY_TX_GAIN_DB": "50", "RELAY_AGC_TARGET": "1000",
    })
    assert c.dst_mhz == 5865.0 and c.max_s == 30.0 and c.guard_mhz == 10.0
    assert c.lost_db == 6.0 and c.lost_s == 2.5 and c.fs_hz == 10_000_000.0
    assert c.rx_gain_db == 30 and c.tx_gain_db == 50 and c.agc_target == 1000.0
```

- [ ] **Step 3: Run → FAIL**

Run: `cd agent/relay && python -m pytest tests/test_relayconfig.py -q`
Expected: FAIL `ModuleNotFoundError: No module named 'relayconfig'`

- [ ] **Step 4: Implement**

`agent/relay/relayconfig.py`:
```python
"""Auto-relay config (env-driven), mirror of agent/tx/txconfig.py. Role-gated by RELAY_ENABLED."""
import os
from dataclasses import dataclass


def _truthy(v):
    return v.strip().lower() not in ("0", "false", "no", "")


@dataclass
class RelayConfig:
    relay_enabled: bool = False
    armed: bool = False                # arm state at process start (dashboard toggles it live)
    dst_mhz: float = 5771.0            # relay target (B3)
    max_s: float = 600.0               # safety deadline per session; next cycle may re-acquire
    guard_mhz: float = 25.0            # ignore sources within dst±guard (TX would feed back into RX)
    lost_db: float = 12.0              # source "gone": raw RX RMS this far below the session reference...
    lost_s: float = 5.0                # ...for this long
    fs_hz: float = 20_000_000.0        # RX and TX sample rate (bandwidth = fs)
    rx_gain_db: int = 40
    tx_gain_db: int = 60
    agc_target: float = 1600.0         # post-AGC peak (of 2047)
    block_samples: int = 32768


def load_relay_config(env=None):
    env = os.environ if env is None else env
    c = RelayConfig()
    if "RELAY_ENABLED" in env: c.relay_enabled = _truthy(env["RELAY_ENABLED"])
    if "RELAY_ARMED" in env: c.armed = _truthy(env["RELAY_ARMED"])
    if "RELAY_DST_MHZ" in env: c.dst_mhz = float(env["RELAY_DST_MHZ"])
    if "RELAY_MAX_S" in env: c.max_s = float(env["RELAY_MAX_S"])
    if "RELAY_GUARD_MHZ" in env: c.guard_mhz = float(env["RELAY_GUARD_MHZ"])
    if "RELAY_LOST_DB" in env: c.lost_db = float(env["RELAY_LOST_DB"])
    if "RELAY_LOST_S" in env: c.lost_s = float(env["RELAY_LOST_S"])
    if "RELAY_FS_HZ" in env: c.fs_hz = float(env["RELAY_FS_HZ"])
    if "RELAY_RX_GAIN_DB" in env: c.rx_gain_db = int(env["RELAY_RX_GAIN_DB"])
    if "RELAY_TX_GAIN_DB" in env: c.tx_gain_db = int(env["RELAY_TX_GAIN_DB"])
    if "RELAY_AGC_TARGET" in env: c.agc_target = float(env["RELAY_AGC_TARGET"])
    return c
```

- [ ] **Step 5: Run → PASS**

Run: `cd agent/relay && python -m pytest tests -q`
Expected: `3 passed`

- [ ] **Step 6: Commit**

```bash
git add agent/relay
git commit -m "feat(relay): relay package + env config (RelayConfig/load_relay_config)"
```

---

### Task 2: `bladerf_relay.py` — AGC, relay loop, full-duplex radio

**Files:**
- Create: `agent/relay/bladerf_relay.py`
- Test: `agent/relay/tests/test_bladerf_relay.py`

**Interfaces:**
- Produces: `AGC_MAX_GAIN = 64.0`; `agc_block(iq: np.ndarray[int16], gain: float, target: float) -> (out: np.ndarray[int16], gain: float, rms_raw: float)`; `relay_loop(radio, block_samples: int, stop_check: () -> bool, on_level: (float) -> None, agc_target: float) -> None`; `BladeRfRelayRadio.read(n) -> np.ndarray[int16]`, `.write(bytes)`, `.close()`; `open_bladerf_relay_radio(src_hz, dst_hz, fs_hz, rx_gain_db, tx_gain_db, block_samples) -> BladeRfRelayRadio`.

- [ ] **Step 1: Failing tests**

`agent/relay/tests/test_bladerf_relay.py`:
```python
import numpy as np

from bladerf_relay import AGC_MAX_GAIN, agc_block, relay_loop


def test_agc_block_moves_toward_target_and_reports_raw_rms():
    iq = np.full(64, 100, dtype=np.int16)
    out, gain, rms = agc_block(iq, 1.0, 1600.0)
    assert abs(rms - 100.0) < 1e-3                    # RMS of the RAW input, before AGC
    assert 1.0 < gain < 16.0                          # smoothed step toward 1600/100 = 16
    assert int(out.max()) == int(round(100 * gain))
    for _ in range(300):
        out, gain, rms = agc_block(iq, gain, 1600.0)
    assert abs(gain - 16.0) < 0.05                    # converged
    assert abs(int(out.max()) - 1600) <= 5


def test_agc_block_clips_to_sc16_and_caps_gain_without_div_by_zero():
    hot = np.array([3000, -3000] * 8, dtype=np.int16)
    out, gain, _ = agc_block(hot, 1.0, 1600.0)
    assert out.dtype == np.int16 and out.max() <= 2047 and out.min() >= -2047
    zero = np.zeros(16, dtype=np.int16)
    _, g, rms = agc_block(zero, 1.0, 1600.0)          # peak 0 -> no ZeroDivisionError
    assert rms == 0.0 and g <= AGC_MAX_GAIN
    for _ in range(50):
        _, g, _ = agc_block(zero, g, 1600.0)
    assert g == AGC_MAX_GAIN


class _FakeRadio:
    def __init__(self, blocks):
        self.blocks = list(blocks)
        self.writes = []
    def read(self, n):
        return self.blocks.pop(0)
    def write(self, b):
        self.writes.append(bytes(b))


def test_relay_loop_reads_agcs_writes_until_stop_and_reports_level():
    radio = _FakeRadio([np.full(8, 200, dtype=np.int16)] * 5)
    levels = []
    n = {"c": 0}
    def stop():
        n["c"] += 1
        return n["c"] > 3                             # 3 iterations
    relay_loop(radio, 4, stop, levels.append, 1600.0)
    assert len(radio.writes) == 3 and len(levels) == 3
    assert all(abs(l - 200.0) < 1e-3 for l in levels)     # raw RMS, not the AGC'd one
    assert len(radio.writes[0]) == 16                 # 8 int16 -> 16 bytes
    assert len(radio.blocks) == 2                     # exactly 3 reads consumed
```

- [ ] **Step 2: Run → FAIL**

Run: `cd agent/relay && python -m pytest tests/test_bladerf_relay.py -q`
Expected: FAIL `ModuleNotFoundError: No module named 'bladerf_relay'`

- [ ] **Step 3: Implement**

`agent/relay/bladerf_relay.py`:
```python
"""bladeRF full-duplex relay core: RX(src) -> per-block AGC -> TX(dst), no demodulation.

Mirrors agent/tx/bladerf_tx.py: open_bladerf_relay_radio is the ONLY bladeRF-touching code;
agc_block/relay_loop are pure and take an injected radio (read/write/close) so they test on fakes.
Live-verified 2026-09-06 (orange_pi6, FPGA v0.16.0): 20 MS/s full-duplex holds 1.0x realtime with
this numpy AGC; without amplitude normalization the RX5808-class receiver sees no carrier at all."""
import logging

import numpy as np

LOG = logging.getLogger("relay.bladerf")

RELAY_STREAM_TIMEOUT_MS = 3500
AGC_MAX_GAIN = 64.0


def agc_block(iq, gain, target):
    """One block of interleaved int16 I/Q -> (int16 out scaled toward `target` peak, new gain,
    RMS of the RAW input). Slow one-pole AGC: gain = 0.9*gain + 0.1*(target/peak), capped."""
    x = np.asarray(iq, dtype=np.float32)
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    if peak <= 0.0:
        peak = 1.0                                   # silent block: no division by zero
    gain = min(gain * 0.9 + (target / peak) * 0.1, AGC_MAX_GAIN)
    out = np.clip(x * gain, -2047, 2047).astype(np.int16)
    rms = float(np.sqrt(np.mean(x * x))) if x.size else 0.0
    return out, gain, rms


def relay_loop(radio, block_samples, stop_check, on_level, agc_target):
    """read -> agc -> write until stop_check() is True; on_level(rms_raw) after every block."""
    gain = 1.0
    while not stop_check():
        iq = radio.read(block_samples)
        out, gain, rms = agc_block(iq, gain, agc_target)
        radio.write(out.tobytes())
        on_level(rms)


class BladeRfRelayRadio:
    """Open full-duplex handle: read(n)->int16 array (sync_rx), write(bytes) (sync_tx), close().
    Radio/channels injected so this class imports nothing from `bladerf`."""

    def __init__(self, radio, rx_ch, tx_ch, block_samples):
        self._radio = radio
        self._rx = rx_ch
        self._tx = tx_ch
        self._buf = bytearray(block_samples * 4)     # SC16_Q11: 4 bytes per complex sample

    def read(self, n):
        self._radio.sync_rx(self._buf, n)
        return np.frombuffer(self._buf, dtype=np.int16)[: 2 * n].copy()

    def write(self, buf):
        self._radio.sync_tx(bytearray(buf), len(buf) // 4)

    def close(self):
        for ch in (self._rx, self._tx):
            try:
                self._radio.enable_module(ch, False)
            except Exception:
                LOG.exception("bladeRF relay disable failed")
        try:
            self._radio.close()
        except Exception:
            LOG.exception("bladeRF relay close failed")


def open_bladerf_relay_radio(src_hz, dst_hz, fs_hz, rx_gain_db, tx_gain_db, block_samples) -> BladeRfRelayRadio:
    """Open the first bladeRF full-duplex: RX ch0 @ src_hz (manual gain), TX ch0 @ dst_hz,
    both SC16_Q11 at fs_hz. Only bladeRF-touching fn (mirrors open_bladerf_tx_radio)."""
    import bladerf
    from bladerf import _bladerf
    radio = bladerf.BladeRF()
    rx = bladerf.CHANNEL_RX(0)
    tx = bladerf.CHANNEL_TX(0)
    for ch in (rx, tx):
        radio.set_sample_rate(ch, int(fs_hz))
        radio.set_bandwidth(ch, int(fs_hz))
    radio.set_frequency(rx, int(src_hz))
    radio.set_frequency(tx, int(dst_hz))
    radio.set_gain_mode(rx, _bladerf.GainMode.Manual)
    radio.set_gain(rx, int(rx_gain_db))
    radio.set_gain(tx, int(tx_gain_db))
    for layout in (_bladerf.ChannelLayout.RX_X1, _bladerf.ChannelLayout.TX_X1):
        radio.sync_config(
            layout=layout, fmt=_bladerf.Format.SC16_Q11,
            num_buffers=32, buffer_size=16384, num_transfers=16, stream_timeout=RELAY_STREAM_TIMEOUT_MS,
        )
    radio.enable_module(rx, True)
    radio.enable_module(tx, True)
    return BladeRfRelayRadio(radio, rx, tx, block_samples)
```

- [ ] **Step 4: Run → PASS**

Run: `cd agent/relay && python -m pytest tests -q`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add agent/relay/bladerf_relay.py agent/relay/tests/test_bladerf_relay.py
git commit -m "feat(relay): bladeRF full-duplex relay core (agc_block, relay_loop, radio open)"
```

---

### Task 3: `relay_controller.py` — arm/disarm, вибір джерела, сесія, стан

**Files:**
- Create: `agent/relay/relay_controller.py`
- Test: `agent/relay/tests/test_relay_controller.py`

**Interfaces:**
- Consumes: `RelayConfig` (Task 1); `open_fn(src_hz, dst_hz, fs_hz, rx_gain_db, tx_gain_db, block_samples) -> radio` and `loop_fn(radio, block_samples, stop_check, on_level, agc_target)` (Task 2 signatures); `publisher.publish_relaystate(ts, state)` (Task 4).
- Produces: `RelayController(cfg, publisher, open_fn, loop_fn, reset=None, manual_pending=None, clock=None)` with `set_command(data)`, `update_hits(hits: list[(band, center_mhz, sync_snr_db)])`, `pending() -> dict|None` (`{"band","src_mhz","dst_mhz","sync_snr_db"}`), `has_pending()`, `run_relay(req) -> error|None`, `announce()`, attrs `armed: bool`, `dst_mhz: float`.

- [ ] **Step 1: Failing tests**

`agent/relay/tests/test_relay_controller.py`:
```python
from relayconfig import RelayConfig
from relay_controller import RelayController


class _FakePublisher:
    def __init__(self): self.states = []
    def publish_relaystate(self, ts, state): self.states.append((ts, dict(state)))


class _FakeRadio:
    def __init__(self): self.closed = False; self.args = None
    def close(self): self.closed = True


def _cfg(**kw):
    base = dict(relay_enabled=True, armed=True, dst_mhz=5771.0, max_s=100.0, guard_mhz=25.0,
                lost_db=12.0, lost_s=5.0)
    base.update(kw)
    return RelayConfig(**base)


def _mk(cfg=None, levels=None, manual=None, clock=None, open_fail=False):
    """Controller on fakes. loop_fn feeds `levels` (raw RMS per block) through on_level until
    stop_check() trips or the list runs out; `consumed` tells how many blocks were fed."""
    radios, consumed = [], {"n": 0}
    def open_fn(src_hz, dst_hz, fs_hz, rx_gain, tx_gain, block):
        if open_fail: raise RuntimeError("no device")
        r = _FakeRadio(); r.args = (src_hz, dst_hz, fs_hz, rx_gain, tx_gain, block); radios.append(r); return r
    def loop_fn(radio, block, stop_check, on_level, target):
        seq = list(levels if levels is not None else [300.0] * 3)
        while not stop_check():
            if not seq: break
            on_level(seq.pop(0)); consumed["n"] += 1
    pub = _FakePublisher()
    ctl = RelayController(cfg or _cfg(), pub, open_fn=open_fn, loop_fn=loop_fn, reset=lambda: None,
                          manual_pending=manual or (lambda: False), clock=clock or (lambda: 1000.0))
    return ctl, pub, radios, consumed


def _stepper(step):
    t = [1000.0]
    def clk(): t[0] += step; return t[0]
    return clk


# ---- source selection ----
def test_update_hits_picks_strongest_outside_guard_band():
    ctl, pub, _, _ = _mk()
    ctl.update_hits([("2.4G", 2465.0, 20.0), ("3.3G", 3470.0, 34.5), ("5.8G", 5780.0, 60.0)])
    req = ctl.pending()
    assert req["src_mhz"] == 3470.0 and req["band"] == "3.3G" and req["dst_mhz"] == 5771.0
    assert req["sync_snr_db"] == 34.5              # 5780 sits inside 5771±25 -> excluded
    assert ctl.pending() is None                   # consumed once


def test_update_hits_none_when_empty_disarmed_or_manual_pending():
    ctl, _, _, _ = _mk(); ctl.update_hits([]); assert not ctl.has_pending()
    ctl, _, _, _ = _mk(cfg=_cfg(armed=False)); ctl.update_hits([("3.3G", 3470.0, 30.0)]); assert not ctl.has_pending()
    ctl, _, _, _ = _mk(manual=lambda: True); ctl.update_hits([("3.3G", 3470.0, 30.0)]); assert not ctl.has_pending()
    ctl, _, _, _ = _mk(); ctl.update_hits([("x", None, 1.0), ("y", "bad", 2.0)]); assert not ctl.has_pending()


# ---- commands ----
def test_arm_sets_dst_and_publishes_disarm_clears_pending():
    ctl, pub, _, _ = _mk(cfg=_cfg(armed=False))
    ctl.set_command({"relay": {"action": "arm", "dst_mhz": 5865}})
    assert ctl.armed is True and ctl.dst_mhz == 5865.0
    assert pub.states[-1][1]["armed"] is True and pub.states[-1][1]["dst_mhz"] == 5865.0
    ctl.set_command({"relay": {"action": "arm", "dst_mhz": 99999}})   # out of range -> keep 5865
    assert ctl.dst_mhz == 5865.0
    ctl.update_hits([("3.3G", 3470.0, 30.0)]); assert ctl.has_pending()
    ctl.set_command({"relay": {"action": "disarm"}})
    assert ctl.armed is False and not ctl.has_pending()
    assert pub.states[-1][1]["armed"] is False and pub.states[-1][1]["status"] == "idle"
    for bad in (None, {}, {"relay": "arm"}, {"relay": {"action": "fly"}}):
        ctl.set_command(bad)                        # never raises


# ---- session ----
def test_run_relay_opens_full_duplex_publishes_relaying_then_idle():
    ctl, pub, radios, consumed = _mk()
    ctl.update_hits([("3.3G", 3470.0, 30.0)])
    err = ctl.run_relay(ctl.pending())
    assert err is None and len(radios) == 1 and radios[0].closed is True
    assert radios[0].args == (int(3470e6), int(5771e6), 20_000_000, 40, 60, 32768)
    on = next(s for _, s in pub.states if s["status"] == "relaying")
    assert on["active"] is True and on["src_mhz"] == 3470.0 and on["band"] == "3.3G"
    assert on["dst_mhz"] == 5771.0 and on["until_ts"] == on["since_ts"] + 100
    assert pub.states[-1][1]["active"] is False and pub.states[-1][1]["status"] == "idle"
    assert pub.states[-1][1]["error"] is None and pub.states[-1][1]["armed"] is True


def test_run_relay_stops_when_source_lost():
    # clock +1s per call: reference window (1s) closes on the first feeds; then RMS 20 dB down
    # (300 -> 30) for well over lost_s=5s -> loop exits long before the 60 levels run out.
    ctl, pub, _, consumed = _mk(levels=[300.0] * 3 + [30.0] * 57, clock=_stepper(1.0))
    ctl.update_hits([("3.3G", 3470.0, 30.0)])
    ctl.run_relay(ctl.pending())
    assert 0 < consumed["n"] < 60
    assert pub.states[-1][1]["status"] == "idle" and pub.states[-1][1]["error"] is None


def test_run_relay_keeps_going_while_level_holds_then_deadline():
    # constant level, clock +30s per call -> the max_s=100 deadline trips within a few checks
    ctl, pub, _, consumed = _mk(levels=[300.0] * 100, clock=_stepper(30.0))
    ctl.update_hits([("3.3G", 3470.0, 30.0)])
    ctl.run_relay(ctl.pending())
    assert consumed["n"] < 100
    assert pub.states[-1][1]["status"] == "idle"


def test_run_relay_stops_on_manual_command_and_on_disarm():
    flag = {"manual": False}
    ctl, pub, _, consumed = _mk(levels=[300.0] * 50, manual=lambda: flag["manual"])
    ctl.update_hits([("3.3G", 3470.0, 30.0)]); req = ctl.pending()
    # flip "manual pending" after the first block via a wrapped publisher hook
    orig = ctl._loop_fn
    def loop(radio, block, stop_check, on_level, target):
        def lvl(rms): on_level(rms); flag["manual"] = True
        orig(radio, block, stop_check, lvl, target)
    ctl._loop_fn = loop
    ctl.run_relay(req)
    assert consumed["n"] == 1
    # disarm mid-session
    ctl2, pub2, _, consumed2 = _mk(levels=[300.0] * 50)
    ctl2.update_hits([("3.3G", 3470.0, 30.0)]); req2 = ctl2.pending()
    orig2 = ctl2._loop_fn
    def loop2(radio, block, stop_check, on_level, target):
        def lvl(rms): on_level(rms); ctl2.set_command({"relay": {"action": "disarm"}})
        orig2(radio, block, stop_check, lvl, target)
    ctl2._loop_fn = loop2
    ctl2.run_relay(req2)
    assert consumed2["n"] == 1 and ctl2.armed is False
    assert pub2.states[-1][1]["status"] == "idle" and pub2.states[-1][1]["armed"] is False


def test_run_relay_publishes_rx_level_periodically():
    ctl, pub, _, _ = _mk(levels=[200.0] * 20, clock=_stepper(1.0))
    ctl.update_hits([("3.3G", 3470.0, 30.0)])
    ctl.run_relay(ctl.pending())
    lv = [s["rx_level_db"] for _, s in pub.states if s["status"] == "relaying" and s["rx_level_db"] is not None]
    assert lv and all(abs(v - round(20 * __import__("math").log10(200 / 2047.0), 1)) < 0.2 for v in lv)


def test_open_failure_is_captured_not_raised():
    ctl, pub, radios, _ = _mk(open_fail=True)
    ctl.update_hits([("3.3G", 3470.0, 30.0)])
    err = ctl.run_relay(ctl.pending())
    assert err == "no device" and radios == []
    assert pub.states[-1][1]["status"] == "idle" and pub.states[-1][1]["error"] == "no device"


def test_announce_republishes_last_state_and_survives_no_publisher():
    ctl, pub, _, _ = _mk()
    ctl.announce()
    assert pub.states[-1][1]["status"] == "idle" and pub.states[-1][1]["armed"] is True
    ctl2 = RelayController(_cfg(), None, open_fn=lambda *a: None, loop_fn=lambda *a: None)
    ctl2.announce(); ctl2.set_command({"relay": {"action": "disarm"}})    # no publisher: no raise
```

- [ ] **Step 2: Run → FAIL**

Run: `cd agent/relay && python -m pytest tests/test_relay_controller.py -q`
Expected: FAIL `ModuleNotFoundError: No module named 'relay_controller'`

- [ ] **Step 3: Implement**

`agent/relay/relay_controller.py`:
```python
"""Auto-relay mode: sync-locked analog-video hits from the sweep -> RX(src)/TX(dst) passthrough.

Twin of agent/tx/tx_controller.py:TxController. The MQTT thread calls set_command() (arm/disarm);
the scan loop calls update_hits() at the end of every sweep cycle, polls pending() between cycles
and calls run_relay(), which frees the sweep's bladeRF (reset), opens full-duplex, loops until
disarm / a manual (view/TX) command / source lost / max_s / error, then hands the device back.
Manual commands always win: no start while one is pending, and an active session yields to one.
Never raises into callers."""
import logging
import math
import threading
import time

LOG = logging.getLogger("relay.controller")

FREQ_MIN_MHZ = 100.0
FREQ_MAX_MHZ = 6000.0            # bladeRF tuning range
REF_WINDOW_S = 1.0               # raw-RMS reference = median of the first second of a session
LEVEL_PUBLISH_S = 2.0            # rx_level_db refresh cadence in relaystate


class _LevelWatch:
    """Source-alive tracker on RAW input RMS: 'lost' once RMS sits lost_db below the session
    reference for lost_s continuously (recovers if it comes back before that)."""

    def __init__(self, lost_db, lost_s, clock):
        self._lost_db = lost_db
        self._lost_s = lost_s
        self._clock = clock
        self._t0 = clock()
        self._ref_samples = []
        self.ref = None
        self._below_since = None
        self.lost = False

    def feed(self, rms):
        now = self._clock()
        if self.ref is None:
            self._ref_samples.append(float(rms))
            if now - self._t0 >= REF_WINDOW_S:
                s = sorted(self._ref_samples)
                self.ref = s[len(s) // 2] or 1.0
            return
        drop_db = 20.0 * math.log10(self.ref / max(float(rms), 1e-6))
        if drop_db >= self._lost_db:
            if self._below_since is None:
                self._below_since = now
            elif now - self._below_since >= self._lost_s:
                self.lost = True
        else:
            self._below_since = None

    @staticmethod
    def level_db(rms):
        return round(20.0 * math.log10(max(float(rms), 1e-6) / 2047.0), 1)


class RelayController:
    def __init__(self, cfg, publisher, open_fn, loop_fn, reset=None, manual_pending=None, clock=None):
        self._cfg = cfg
        self._publisher = publisher
        self._open_fn = open_fn               # bladerf_relay.open_bladerf_relay_radio(src_hz, dst_hz, fs, rxg, txg, block)
        self._loop_fn = loop_fn               # bladerf_relay.relay_loop(radio, block, stop_check, on_level, target)
        self._reset = reset or (lambda: None)
        self._manual_pending = manual_pending or (lambda: False)
        self._clock = clock or time.time
        self._lock = threading.Lock()
        self.armed = bool(cfg.armed)
        self.dst_mhz = float(cfg.dst_mhz)
        self._pending = None                  # chosen source dict for the next session
        self._stop = threading.Event()
        self._last = self._idle_state()       # for announce()

    # ---- command intake (MQTT thread) ----
    def set_command(self, data):
        r = data.get("relay") if isinstance(data, dict) else None
        if not isinstance(r, dict):
            LOG.warning("relay: ignoring malformed command %r", data)
            return
        action = r.get("action")
        if action == "arm":
            d = r.get("dst_mhz")
            if isinstance(d, (int, float)) and FREQ_MIN_MHZ <= float(d) <= FREQ_MAX_MHZ:
                self.dst_mhz = float(d)
            self.armed = True
            LOG.info("relay armed (dst=%.1f MHz)", self.dst_mhz)
            self._pub(self._last, self._now())      # re-publish with the new armed/dst
            return
        if action == "disarm":
            self.armed = False
            with self._lock:
                self._pending = None
            self._stop.set()                        # active session exits on its next block
            LOG.info("relay disarmed")
            self._pub(self._last, self._now())
            return
        LOG.warning("relay: ignoring unknown action %r", action)

    # ---- sweep feed (scan thread, end of cycle) ----
    def update_hits(self, hits):
        """hits: iterable of (band, center_mhz, sync_snr_db) for sync-locked analog video seen this
        cycle. Picks the strongest outside dst±guard; only when armed and nothing manual pends."""
        best = None
        if self.armed and not self._manual_pending():
            for band, center, snr in hits:
                try:
                    center = float(center)
                    snr = float(snr) if snr is not None else 0.0
                except (TypeError, ValueError):
                    continue
                if abs(center - self.dst_mhz) <= float(self._cfg.guard_mhz):
                    continue                        # TX would feed straight back into RX
                if best is None or snr > best[2]:
                    best = (band, center, snr)
        with self._lock:
            self._pending = None if best is None else {
                "band": best[0], "src_mhz": best[1], "dst_mhz": self.dst_mhz, "sync_snr_db": best[2]}

    # ---- arbitration hooks (scan loop) ----
    def pending(self):
        with self._lock:
            p, self._pending = self._pending, None
        return p

    def has_pending(self):
        with self._lock:
            return self._pending is not None

    # ---- session (scan loop, blocking) ----
    def run_relay(self, req):
        error = None
        radio = None
        c = self._cfg
        src = float(req["src_mhz"]); dst = float(req["dst_mhz"]); band = req.get("band")
        self._stop.clear()
        try:
            self._reset()                     # free the sweep's bladeRF before opening full-duplex
            radio = self._open_fn(int(src * 1e6), int(dst * 1e6), int(c.fs_hz),
                                  int(c.rx_gain_db), int(c.tx_gain_db), int(c.block_samples))
            since = self._now()
            deadline = float(since + int(c.max_s))
            st = {"active": True, "status": "relaying", "src_mhz": src, "band": band, "dst_mhz": dst,
                  "since_ts": since, "until_ts": int(deadline), "rx_level_db": None, "error": None}
            self._pub(st, since)
            watch = _LevelWatch(float(c.lost_db), float(c.lost_s), self._clock)
            last_pub = [self._clock()]

            def on_level(rms):
                watch.feed(rms)
                now = self._clock()
                if now - last_pub[0] >= LEVEL_PUBLISH_S:
                    last_pub[0] = now
                    self._pub({**st, "rx_level_db": _LevelWatch.level_db(rms)}, int(now))

            def stop_check():
                return (self._stop.is_set() or not self.armed or self._manual_pending()
                        or watch.lost or self._clock() >= deadline)

            self._loop_fn(radio, int(c.block_samples), stop_check, on_level, float(c.agc_target))
            if watch.lost:
                LOG.info("relay: source %.1f MHz lost (%.0f dB below reference for %.0fs)",
                         src, c.lost_db, c.lost_s)
        except Exception as e:
            LOG.exception("relay run_relay failed")
            error = str(e)
        finally:
            if radio is not None:
                try: radio.close()
                except Exception: LOG.exception("relay radio close failed")
            self._pub({**self._idle_state(), "error": error}, self._now())
            try: self._reset()                # leave the device clean for the next sweep
            except Exception: LOG.exception("relay: device reset failed")
            self._stop.clear()
        return error

    def announce(self):
        """(Re)publish last-known retained relaystate — capability announce on (re)connect;
        also clears a stale active:true after a crash."""
        self._pub(self._last, self._now())

    # ---- helpers ----
    def _now(self):
        return int(self._clock())

    def _idle_state(self):
        return {"active": False, "status": "idle", "src_mhz": None, "band": None,
                "dst_mhz": self.dst_mhz, "since_ts": None, "until_ts": None,
                "rx_level_db": None, "error": None}

    def _pub(self, state, ts):
        # armed/dst always reflect the live setting; an ACTIVE session keeps the dst it opened with
        state = {**state, "armed": self.armed,
                 "dst_mhz": state.get("dst_mhz") if state.get("active") else self.dst_mhz}
        self._last = state
        if self._publisher is None:
            return
        try:
            self._publisher.publish_relaystate(ts, state)
        except Exception:
            LOG.exception("relay state publish failed")
```

- [ ] **Step 4: Run → PASS**

Run: `cd agent/relay && python -m pytest tests -q`
Expected: `16 passed`

- [ ] **Step 5: Commit**

```bash
git add agent/relay/relay_controller.py agent/relay/tests/test_relay_controller.py
git commit -m "feat(relay): RelayController — arm/disarm, strongest sync-locked source, session with lost/deadline/manual stop"
```

---

### Task 4: `agent/scan` — роутинг команди, стан, хіти зі свіпу, арбітрація

**Files:**
- Modify: `agent/scan/publisher.py:56-61` (топік+колбек), `:120-126` (гілка `relay`), `:181-186` (після `publish_txstate`)
- Modify: `agent/scan/video_emit.py:23,40,44-57`
- Modify: `agent/scan/main.py:104-110` (сигнатура+`video_hits`), `:159-163` (строгий шлях), `:189-193` (carrier-шлях), `:211-217` (перед payload), `:369-370` (init), `:384-394` (on_connected), `:414-419` (initial announce), `:442-454` (цикл)
- Test: `agent/scan/tests/test_publisher.py` (append), `agent/scan/tests/test_run_cycle.py` (append)

**Interfaces:**
- Consumes: `RelayController` (Task 3): `set_command`, `update_hits`, `pending`, `run_relay`, `announce`; `open_bladerf_relay_radio`, `relay_loop` (Task 2); `load_relay_config` (Task 1).
- Produces: `MqttPublisher.on_relay_command` (fn(dict)), `MqttPublisher.publish_relaystate(ts, state)` → retained `fpv/<id>/relaystate`; `VideoEmitter.last_sync_snr_db`; `run_cycle(..., relay=None)`.

- [ ] **Step 1: Failing tests — publisher**

Append to `agent/scan/tests/test_publisher.py`:
```python
def test_on_message_routes_relay_and_not_rx():
    fake = FakeClient()
    p = _pub(fake)
    seen = {}
    p.on_relay_command = lambda d: seen.setdefault("relay", d)
    p.on_command = lambda *a: seen.setdefault("rx", a)     # must NOT fire
    p._on_message(None, None, _Msg(json.dumps({"relay": {"action": "arm", "dst_mhz": 5771}}).encode()))
    assert seen.get("relay") == {"relay": {"action": "arm", "dst_mhz": 5771}}
    assert "rx" not in seen


def test_on_message_relay_none_handler_is_safe():
    p = _pub(FakeClient())
    p.on_relay_command = None
    p.on_command = lambda *a: (_ for _ in ()).throw(AssertionError("rx must not fire"))
    p._on_message(None, None, _Msg(json.dumps({"relay": {"action": "disarm"}}).encode()))


def test_publish_relaystate_topic_retained_payload():
    fake = FakeClient()
    p = _pub(fake); p.connect(ts=1)
    p.publish_relaystate(800, {"armed": True, "active": True, "status": "relaying", "src_mhz": 3470})
    msg = [m for m in fake.published if m[0] == "fpv/hackrf/relaystate"][-1]
    topic, payload, qos, retain = msg
    assert qos == 1 and retain is True
    body = json.loads(payload)
    assert body["scanner_id"] == "hackrf" and body["ts"] == 800
    assert body["armed"] is True and body["status"] == "relaying" and body["src_mhz"] == 3470
```

- [ ] **Step 2: Run → FAIL**

Run: `cd agent/scan && python -m pytest tests/test_publisher.py -q -k relay`
Expected: 3 FAIL (`AttributeError: ... publish_relaystate` / routing falls to rx handler)

- [ ] **Step 3: Implement publisher**

`agent/scan/publisher.py` — після рядка `self._t_txfiles = ...` (57):
```python
        self._t_relaystate = f"fpv/{scanner_id}/relaystate"
```
після `self.on_tx_command = None ...` (61):
```python
        self.on_relay_command = None    # set by the caller: fn(dict) — auto-relay arm/disarm
```
у `_on_message`, після гілки `if "tx" in data: ... return` (126):
```python
        if "relay" in data:             # auto-relay command — not routed to the RX5808 handler
            if self.on_relay_command is not None:
                try:
                    self.on_relay_command(data)
                except Exception:
                    LOG.exception("on_relay_command handler failed")
            return
```
після `publish_txfiles` (193):
```python
    def publish_relaystate(self, ts, state):
        self._publish(
            self._t_relaystate,
            {"scanner_id": self.scanner_id, "ts": ts, **state},
            self.QOS_DETECTION,
        )
```

- [ ] **Step 4: Run → PASS**

Run: `cd agent/scan && python -m pytest tests/test_publisher.py -q`
Expected: all pass (існуючі + 3 нових)

- [ ] **Step 5: Failing tests — run_cycle**

Append to `agent/scan/tests/test_run_cycle.py` (в самий кінець файлу; `_FakeEmitter`, `_NotVideoEmitter`, `_write_fixtures`, `_config`, `_FakePub` уже визначені вище):
```python
class _FakeRelay:
    def __init__(self):
        self.hits = None
    def update_hits(self, hits):
        self.hits = list(hits)


def test_run_cycle_feeds_relay_with_sync_locked_hits(tmp_path, monkeypatch):
    _write_fixtures(tmp_path)
    cfg = _config(tmp_path)
    em = _FakeEmitter()                            # always "published"
    em.last_sync_snr_db = 33.0
    monkeypatch.setattr(main, "classify", lambda feat, thr: ("analog", 0.9))
    relay = _FakeRelay()

    main.run_cycle(cfg, now_ts=1718530000, publisher=_FakePub(), emitter=em, relay=relay)

    assert relay.hits                              # called once per cycle with this cycle's hits
    band, center, snr = relay.hits[0]
    assert band == "5.8G" and abs(center - 5800.0) < 2.0 and snr == 33.0


def test_run_cycle_feeds_relay_empty_list_when_nothing_locks(tmp_path, monkeypatch):
    _write_fixtures(tmp_path)
    cfg = _config(tmp_path)
    monkeypatch.setattr(main, "classify", lambda feat, thr: ("digital", 0.7))
    relay = _FakeRelay()

    main.run_cycle(cfg, now_ts=1718530000, publisher=_FakePub(), emitter=_NotVideoEmitter(), relay=relay)

    assert relay.hits == []                        # still called: lets the controller clear pending


def test_run_cycle_relay_failure_does_not_break_cycle(tmp_path, monkeypatch):
    _write_fixtures(tmp_path)
    cfg = _config(tmp_path)
    class _Boom:
        def update_hits(self, hits): raise RuntimeError("boom")
    payload = main.run_cycle(cfg, now_ts=1718530000, publisher=_FakePub(), emitter=_FakeEmitter(), relay=_Boom())
    assert payload is not None and len(payload["detections"]) >= 1
```

- [ ] **Step 6: Run → FAIL**

Run: `cd agent/scan && python -m pytest tests/test_run_cycle.py -q -k relay`
Expected: FAIL `TypeError: run_cycle() got an unexpected keyword argument 'relay'`

- [ ] **Step 7: Implement video_emit + run_cycle**

`agent/scan/video_emit.py`: у `__init__` після `self.last_frame_path = None` (23):
```python
        self.last_sync_snr_db = None # sync SNR (dB) of the most recent published frame
```
у `maybe_emit` після `self.last_frame_path = None` (40):
```python
        self.last_sync_snr_db = None
```
перед `LOG.info("video published ...` (55):
```python
        self.last_sync_snr_db = round(float(vf.sync_snr_db), 1)
```

`agent/scan/main.py` — сигнатура `run_cycle` (104-105):
```python
def run_cycle(cfg: Config, now_ts: int, publisher=None, emitter=None, controller=None,
              abort=None, relay=None) -> dict | None:
```
після `rx_carrier_centers = []` (109):
```python
    video_hits = []          # (band, center_mhz, sync_snr_db) per sync-locked frame this cycle -> auto-relay
```
строгий шлях (162-163) стає:
```python
                    if emitter.maybe_emit(iq, cfg.dwell_sample_rate_hz, c.center_mhz, now_ts) == "published":
                        frame = emitter.last_frame_path
                        video_hits.append((band, c.center_mhz, getattr(emitter, "last_sync_snr_db", None)))
```
carrier-шлях — одразу після `LOG.info("carrier video band=%s ...")` (192-193), перед коментарем `# A line-sync-locked demod IS ...`:
```python
                        video_hits.append((band, c.center_mhz, getattr(emitter, "last_sync_snr_db", None)))
```
перед `payload = build_payload(...)` (217):
```python
    if relay is not None:
        try:
            relay.update_hits(video_hits)
        except Exception:
            LOG.exception("relay update_hits failed")
```

- [ ] **Step 8: Run → PASS**

Run: `cd agent/scan && python -m pytest tests -q`
Expected: all pass (існуючі + 6 нових)

- [ ] **Step 9: Wire `main()` (без тесту — інтеграційний код за наявним TX-патерном)**

`agent/scan/main.py` після TX-блоку (після рядка `LOG.exception("TX generator init failed; continuing without it")`, 370):
```python
    relay = None
    try:
        if publisher is not None:
            _relaydir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "relay"))
            if _relaydir not in sys.path:
                sys.path.append(_relaydir)   # relay module names are unique; append so scan/video win any tie
            from relayconfig import load_relay_config
            relaycfg = load_relay_config()
            if relaycfg.relay_enabled:
                from bladerf_relay import open_bladerf_relay_radio, relay_loop
                from relay_controller import RelayController
                relay = RelayController(
                    relaycfg, publisher, open_fn=open_bladerf_relay_radio, loop_fn=relay_loop,
                    reset=_reset_bladerf_backend,               # free the sweep's bladeRF before/after relay
                    manual_pending=lambda: ((view is not None and view.has_pending())
                                            or (tx_ctl is not None and tx_ctl.has_pending())),
                )
                publisher.on_relay_command = relay.set_command
                LOG.info("Auto-relay enabled (dst=%.0f MHz armed=%s max=%.0fs guard=%.0fMHz)",
                         relaycfg.dst_mhz, relaycfg.armed, relaycfg.max_s, relaycfg.guard_mhz)
    except Exception:
        LOG.exception("Auto-relay init failed; continuing without it")
```
після блоку `if tx_ctl is not None: ... publisher.on_connected = _on_connected_tx` (394):
```python
    if relay is not None:
        prev_relay = publisher.on_connected
        def _on_connected_relay():
            if prev_relay is not None:
                prev_relay()
            relay.announce()                                   # retained capability announce
        publisher.on_connected = _on_connected_relay
```
у initial-connect блоці після `tx_ctl.announce()`/`publish_txfiles` try/except (419):
```python
            if relay is not None:
                relay.announce()
```
у головному циклі після view-блоку (`LOG.info("SDR view ended; sweep resumes"); continue`, 447), ПЕРЕД `if not cfg.scan_enabled:`:
```python
            if relay is not None:
                rreq = relay.pending()
                if rreq is not None:
                    LOG.info("entering RELAY %s %.1f -> %.1f MHz (sweep paused)",
                             rreq.get("band"), rreq["src_mhz"], rreq["dst_mhz"])
                    relay.run_relay(rreq)                   # frees/reopens the bladeRF via reset internally
                    LOG.info("RELAY ended; sweep resumes")
                    continue
```
виклик `run_cycle(...)` (451-454) — додати `relay=relay`:
```python
            payload = run_cycle(cfg, now_ts=int(time.time()), publisher=publisher,
                                emitter=emitter, controller=controller, relay=relay,
                                abort=(lambda: (view is not None and view.has_pending())
                                       or (tx_ctl is not None and tx_ctl.has_pending())))
```

- [ ] **Step 10: Smoke-import + повний прогін**

Run: `cd agent/scan && python -c "import main; print('ok')" && python -m pytest tests -q && cd ../tx && python -m pytest tests -q && cd ../relay && python -m pytest tests -q`
Expected: `ok`, усі три пакети зелені.

- [ ] **Step 11: Commit**

```bash
git add agent/scan/publisher.py agent/scan/video_emit.py agent/scan/main.py agent/scan/tests/test_publisher.py agent/scan/tests/test_run_cycle.py
git commit -m "feat(scan): auto-relay wiring — relay command routing/relaystate, sync-locked hits from run_cycle, main-loop arbitration"
```

---

### Task 5: Дашборд — reduce/команда + блок «Авто-ретрансляція»

**Files:**
- Modify: `dashboard/public/mqtt-scan.js:24-42` (після `buildTxCommand`), `:56` (`ensure`), `:65` (regex), `:141` (reduce гілка), `:175` (subscribe), `:211-216` (після `publishTx`)
- Modify: `dashboard/public/views/tx.js:20-66` (картка), `:70-99` (`updateCard`)
- Modify: `dashboard/public/app.js:126-128`
- Modify: `dashboard/public/fixtures.js:35-36`
- Modify: `dashboard/public/styles.css:271-273`
- Test: `test/mqtt-scan.test.js` (append)

**Interfaces:**
- Consumes: retained `relaystate` payload (Task 4 contract).
- Produces: `buildRelayCommand(action, params={}) -> {relay:{action, dst_mhz?}}`; `ScanClient.publishRelay(id, action, params)`; `store[id].relaystate`; `ctx.onRelayArm(id, {dstMhz})`, `ctx.onRelayDisarm(id)`.

- [ ] **Step 1: Failing tests**

Append to `test/mqtt-scan.test.js` (і додати `buildRelayCommand` до import у рядку 3):
```js
test('buildRelayCommand arm/disarm', () => {
  assert.deepEqual(buildRelayCommand('disarm'), { relay: { action: 'disarm' } });
  assert.deepEqual(buildRelayCommand('arm', { dstMhz: '5771' }), { relay: { action: 'arm', dst_mhz: 5771 } });
  assert.deepEqual(buildRelayCommand('arm'), { relay: { action: 'arm' } });        // empty field -> agent default
});

test('reduce relaystate', () => {
  let store = emptyStore();
  store = reduce(store, 'fpv/bladerf/relaystate', JSON.stringify({
    ts: 5, armed: true, active: true, status: 'relaying', src_mhz: 3470, band: '3.3G', dst_mhz: 5771,
    since_ts: 100, until_ts: 700, rx_level_db: -13.2, error: null }));
  const r = store.bladerf.relaystate;
  assert.equal(r.armed, true);
  assert.equal(r.active, true);
  assert.equal(r.status, 'relaying');
  assert.equal(r.src_mhz, 3470);
  assert.equal(r.band, '3.3G');
  assert.equal(r.dst_mhz, 5771);
  assert.equal(r.until_ts, 700);
  assert.equal(r.rx_level_db, -13.2);
  assert.equal(r.error, null);
  store = reduce(store, 'fpv/bladerf/relaystate', JSON.stringify({ ts: 6, armed: false, active: false, status: 'idle', dst_mhz: 5771 }));
  assert.equal(store.bladerf.relaystate.armed, false);
  assert.equal(store.bladerf.relaystate.src_mhz, null);
  assert.equal(store.bladerf.relaystate.rx_level_db, null);
});
```
Імпорт (рядок 3):
```js
import { emptyStore, reduce, buildCommand, buildViewCommand, buildThresholdCommand, buildTxCommand, buildRelayCommand } from '../dashboard/public/mqtt-scan.js';
```

- [ ] **Step 2: Run → FAIL**

Run: `npm test 2>&1 | tail -12`
Expected: FAIL (`buildRelayCommand` is not exported / `store.bladerf.relaystate` undefined)

- [ ] **Step 3: Implement `mqtt-scan.js`**

Після `buildTxCommand` (42):
```js
// Build an auto-relay command for fpv/<id>/rxcmd ({relay:{action:'arm',dst_mhz?}} | {relay:{action:'disarm'}}).
export function buildRelayCommand(action, params = {}) {
  if (action === 'disarm') return { relay: { action: 'disarm' } };
  const relay = { action: 'arm' };
  if (Number.isFinite(Number(params.dstMhz))) relay.dst_mhz = Number(params.dstMhz);
  return { relay };
}
```
`ensure` (56): додати `relaystate: null` після `txfiles: null`.
Regex (65): `...|txstate|txfiles|relaystate)$/`.
Reduce — після гілки `txfiles` (146), перед `} else if (kind === 'spectrum') {`:
```js
  } else if (kind === 'relaystate') {
    s.relaystate = {
      ts: data.ts || 0,
      armed: !!data.armed,
      active: !!data.active,
      status: data.status || 'idle',
      src_mhz: data.src_mhz == null ? null : Number(data.src_mhz),
      band: data.band || null,
      dst_mhz: data.dst_mhz == null ? null : Number(data.dst_mhz),
      since_ts: data.since_ts == null ? null : Number(data.since_ts),
      until_ts: data.until_ts == null ? null : Number(data.until_ts),
      rx_level_db: data.rx_level_db == null ? null : Number(data.rx_level_db),
      error: data.error || null,
    };
```
Subscribe (175): додати `'fpv/+/relaystate'` у масив.
Після `publishTx` (216), перед закриваючою `}` класу:
```js
  // Auto-relay command — same rxcmd topic, NOT retained (a retained arm would re-arm on every reconnect).
  publishRelay(id, action, params) {
    if (!this.client || !id) return;
    this.client.publish(`fpv/${id}/rxcmd`, JSON.stringify(buildRelayCommand(action, params)),
      { qos: 1, retain: false });
  }
```

- [ ] **Step 4: Run → PASS**

Run: `npm test 2>&1 | tail -8`
Expected: `# fail 0`

- [ ] **Step 5: Картка (`views/tx.js`)**

У `buildCard` innerHTML — після `</div>` блоку `.tx-actions` (35) додати:
```js
    <div class="tx-relay" data-role="relay" hidden>
      <span class="tx-relay-title mono">⇄ Авто-ретрансляція</span>
      <label>Ціль, МГц<input class="tx-relay-dst" type="number" min="100" max="6000" step="1" placeholder="5771"></label>
      <button type="button" class="btn tx-relay-arm" data-role="relay-arm">⚡ Озброїти</button>
      <button type="button" class="btn tx-relay-disarm" data-role="relay-disarm">■ Зняти</button>
      <span class="tx-relay-status mono" data-role="relay-status"></span>
    </div>`;
```
(`</div>` картки + backtick-крапка з комою з рядка 35 переїжджають після цього блоку — картка має закінчуватись саме `</div>\`;`).

Після обробника `.tx-retune` (64), перед `return card;`:
```js
  const rdst = card.querySelector('.tx-relay-dst');
  card.querySelector('.tx-relay-arm').addEventListener('click', () => {
    const d = Number(rdst.value);
    ctx.onRelayArm(id, { dstMhz: rdst.value === '' || !Number.isFinite(d) ? undefined : d });
  });
  card.querySelector('.tx-relay-disarm').addEventListener('click', () => ctx.onRelayDisarm(id));
```
У `updateCard` — в кінці функції (після рядка `card.querySelector('[data-role=err]').textContent = tx.error || '';`, 98):
```js
  const rs = s.relaystate;
  const relay = card.querySelector('[data-role=relay]');
  relay.hidden = !rs;                                     // no relaystate -> node has no relay role
  if (rs) {
    card.querySelector('[data-role=relay-arm]').disabled = !!rs.armed;
    card.querySelector('[data-role=relay-disarm]').disabled = !rs.armed;
    const st = card.querySelector('[data-role=relay-status]');
    if (rs.active && rs.status === 'relaying') {
      st.className = 'tx-relay-status mono on';
      st.textContent = `⇄ ${rs.src_mhz} → ${rs.dst_mhz} МГц · ${rs.band || ''} · ⏱ ${fmtCountdown(rs.until_ts, nowS)}`
        + (rs.rx_level_db == null ? '' : ` · вхід ${rs.rx_level_db} дБ`);
    } else if (rs.armed) {
      st.className = 'tx-relay-status mono armed';
      st.textContent = 'озброєно · чекаю синхро-лок';
    } else {
      st.className = 'tx-relay-status mono';
      st.textContent = 'вимкнено';
    }
    if (rs.error && !tx.error) card.querySelector('[data-role=err]').textContent = rs.error;
  }
```
Оновити шапковий коментар файлу (рядок 1-4): додати `+ relay arm/disarm block (store[id].relaystate)`.

- [ ] **Step 6: `app.js`, `fixtures.js`, `styles.css`**

`app.js` після `onTxRetune` (128):
```js
  onRelayArm: (id, params) => { if (!PREVIEW) scanClient.publishRelay(id, 'arm', params); },
  onRelayDisarm: (id) => { if (!PREVIEW) scanClient.publishRelay(id, 'disarm'); },
```
`fixtures.js` після `txfiles` bladeRF-ноди (36):
```js
    relaystate: { ts:NOW, armed:true, active:true, status:'relaying', src_mhz:3470, band:'3.3G', dst_mhz:5771, since_ts:NOW-40, until_ts:NOW+560, rx_level_db:-13.2, error:null },
```
(hackrf-ноду НЕ чіпати — без `relaystate` блок має бути прихований.)

`styles.css` після `.tx-err` (273):
```css
.tx-relay { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 10px; padding-top: 10px; border-top: 1px dashed var(--line); }
.tx-relay[hidden] { display: none; }
.tx-relay label { display: flex; flex-direction: column; gap: 3px; font-size: 11px; color: var(--muted); font-family: var(--mono); }
.tx-relay input { background: #0d0d0d; border: 1px solid var(--line); color: var(--text); font-family: var(--mono); padding: 5px 7px; width: 84px; }
.tx-relay .btn[disabled] { opacity: .5; cursor: not-allowed; }
.tx-relay-title { font-size: 11px; color: var(--muted); }
.tx-relay-status { font-size: 12px; color: var(--muted); }
.tx-relay-status.armed { color: var(--warn); }
.tx-relay-status.on { color: #fff; font-weight: 700; }
```

- [ ] **Step 7: Перевірка превʼю + тести**

Run: `npm test 2>&1 | tail -8`
Expected: `# fail 0`

Run: `node dashboard/dev-serve.mjs` (або як описано у `dashboard/` README) → відкрити `http://localhost:<port>/?preview=1#/tx` → картка bladerf показує блок «⇄ Авто-ретрансляція» зі статусом `⇄ 3470 → 5771 МГц · 3.3G · ⏱ 9:20 · вхід -13.2 дБ`, кнопка «Озброїти» disabled, «Зняти» enabled; картка hackrf блоку НЕ показує. (Якщо dev-serve недоступний — пропустити, зафіксувати у звіті.)

- [ ] **Step 8: Commit**

```bash
git add dashboard/public/mqtt-scan.js dashboard/public/views/tx.js dashboard/public/app.js dashboard/public/fixtures.js dashboard/public/styles.css test/mqtt-scan.test.js
git commit -m "feat(dashboard): auto-relay arm/disarm block on TX screen + relaystate reduce/command"
```

---

### Task 6: Деплой + ручний гейт (виконує контролер сесії, НЕ сабагент)

**Files:** none (конфіг на orange_pi6 + сервер).

- [ ] **Step 1: PR + merge** — `gh pr create` з гілки `feat/auto-relay`, squash-merge у main.
- [ ] **Step 2: orange_pi6** (root@192.168.1.190, key `~/.ssh/fpv_deploy`):
```bash
cd /opt/fpv-video-stream && git pull
printf "[Service]\nEnvironment=RELAY_ENABLED=1\nEnvironment=SCAN_BANDS=1.2G:1080-1360,2.4G:2370-2510,3.3G:3200-3500,5.8G:5645-5945\n" > /etc/systemd/system/fpv-scan.service.d/relay.conf
systemctl daemon-reload && systemctl restart fpv-scan
journalctl -u fpv-scan --since "-20s" --no-pager -o cat | grep -E "Auto-relay enabled|MQTT publisher connected"
```
- [ ] **Step 3: Сервер** (andriy@193.242.163.139): `git pull && sudo docker compose build dashboard && sudo docker compose up -d --no-deps dashboard`; перевірити `relaystate` retained на брокері (`mosquitto_sub -t fpv/orange_pi6/relaystate -C 1`).
- [ ] **Step 4: Гейт** — дашборд → «Передавач» → orange_pi6 → Озброїти (ціль порожня = 5771) → увімкнути VTX 3470 → протягом ≤1 циклу свіпу лог `entering RELAY 3.3G 3470.0 -> 5771.0 MHz`, `relaystate.status=relaying`, **картинка на Skydroid B3** → вимкнути VTX → ≤~6 с `RELAY ended`, `idle`, спектр знову оновлюється → увімкнути VTX → повторне захоплення. Під час relay натиснути Старт TX-генератора → relay зупиняється, TX іде.
