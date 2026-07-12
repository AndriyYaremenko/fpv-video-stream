# bladeRF як вьювер + роль-конфіг SDR — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Дати bladeRF ту саму можливість живого вью-стріму, що має HackRF, і зробити ролі
(свіп/вью) config-driven, щоб будь-який SDR вузла міг свіпити та/або стрімити.

**Architecture:** Додаємо in-process `BladerfViewSource` (той самий duck-type, що `HackrfSource`:
reader-потік наповнює спільний `IqRing` сирими SC16_Q11 байтами; демод тягне з кільця). Спільний
демод-цикл `run_stream_source` стає format-agnostic через `source.bytes_per_sample` + `source.to_iq`.
`main.py` симетрично вмикає bladeRF-джерело для вью і додає прапорець `SCAN_ENABLED` (свіп on/off).

**Tech Stack:** Python 3, numpy, cffi/libbladeRF (sync_rx), pytest. Без змін дашборда/сервера.

## Global Constraints

- HackRF IQ = int8, **2 байти/комплексний семпл**; bladeRF IQ = SC16_Q11 (int16), **4 байти/семпл**.
- Демодуляція scale-invariant: `iq_from_int8_fast` (без /128) і `iq_from_sc16q11` (÷2048) обидва
  повертають `complex64` — різниця масштабу не впливає на кадри.
- Тести запускаються з КОРЕНЯ репо: `python -m pytest agent/scan/tests/... ` та
  `python -m pytest agent/video/tests/...` (conftest.py у `agent/scan` та `agent/video` додають
  потрібні теки в `sys.path`; `agent/video/conftest.py` кладе і `agent/scan`).
- **Жодних змін** у `dashboard/`, `lib/`, `test/` (Node), `server`. Дашборд уже data-driven.
- bladeRF sample_rate для вью = `view_sample_rate_hz` (default 8 MS/s); підсилення = `bladerf_gain_db`.
- Config default `scan_enabled = True` — інакше зламаються наявні `test_main_*` (вони будують `Config()`
  й розраховують, що свіп працює).
- Кожен коміт закінчувати футером:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01JLJdXSvLMBojY7w2dcUwuN
  ```
- Гілка вже створена: `feat/bladerf-view-role-config` (спека там закомічена).

## Файлова структура

- Create `agent/scan/iqring.py` — винесений спільний `IqRing` (реюз hackrf+bladerf).
- Modify `agent/scan/hackrf_source.py` — `IqRing` → імпорт з `iqring` (re-export зберігає сумісність).
- Modify `agent/scan/bladerf_source.py` — новий `BladerfViewSource` + `BladeRfViewRadio` +
  `open_bladerf_view_radio` (єдине bladeRF-touching місце).
- Modify `agent/video/stream_demod.py:439-505` — `run_stream_source` бере wire-format із джерела.
- Modify `agent/scan/config.py` — поле `scan_enabled` + парсинг `SCAN_ENABLED`.
- Modify `agent/scan/main.py` — bladeRF-гілка вью (269-293) + `SCAN_ENABLED`-gate у циклі (322-361).
- Modify `docs/bladerf-scanner-deploy.md` — секція про bladeRF-вьювер і ролі.
- Tests: `agent/scan/tests/test_config.py`, `test_bladerf_source.py`, `test_run_cycle.py`,
  `agent/video/tests/test_stream_demod.py`.

---

### Task 1: `SCAN_ENABLED` config flag

**Files:**
- Modify: `agent/scan/config.py:19-59` (dataclass) та `:81-139` (loader)
- Test: `agent/scan/tests/test_config.py`

**Interfaces:**
- Produces: `Config.scan_enabled: bool` (default `True`); env `SCAN_ENABLED` (falsey: `0/false/no/""`).

- [ ] **Step 1: Write the failing test**

Додати в `agent/scan/tests/test_config.py`:

```python
def test_scan_enabled_default_and_env():
    assert load_config({}).scan_enabled is True
    assert load_config({"SCAN_ENABLED": "0"}).scan_enabled is False
    assert load_config({"SCAN_ENABLED": "false"}).scan_enabled is False
    assert load_config({"SCAN_ENABLED": "1"}).scan_enabled is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest agent/scan/tests/test_config.py::test_scan_enabled_default_and_env -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'scan_enabled'`.

- [ ] **Step 3: Write minimal implementation**

У `agent/scan/config.py`, у dataclass `Config` поряд з `source` (рядок ~27) додати:

```python
    scan_enabled: bool = True                  # gate the sweep loop; a pure viewer sets 0
```

У `load_config`, поряд із блоком `SCAN_MQTT_ENABLED` (рядок ~97), додати:

```python
    if "SCAN_ENABLED" in env:
        c.scan_enabled = env["SCAN_ENABLED"].strip().lower() not in ("0", "false", "no", "")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest agent/scan/tests/test_config.py -v`
Expected: PASS (усі тести файлу, включно з новим).

- [ ] **Step 5: Commit**

```bash
git add agent/scan/config.py agent/scan/tests/test_config.py
git commit -m "feat(scan): SCAN_ENABLED flag to gate the sweep loop

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JLJdXSvLMBojY7w2dcUwuN"
```

---

### Task 2: Винести `IqRing` у спільний модуль

**Files:**
- Create: `agent/scan/iqring.py`
- Modify: `agent/scan/hackrf_source.py:15-64` (видалити локальний `IqRing`, імпортувати)
- Test: `agent/scan/tests/test_iqring.py` (новий), наявний `test_hackrf_source.py` лишається зеленим

**Interfaces:**
- Produces: `iqring.IqRing(capacity_bytes)` з методами `write(buf)`, `read(n, timeout_s) -> bytes|None`,
  `clear()`, `pending() -> int` та полем `dropped_bytes`. `hackrf_source.IqRing` лишається доступним
  (re-export), тож наявні імпорти не ламаються.

- [ ] **Step 1: Write the failing test**

Створити `agent/scan/tests/test_iqring.py`:

```python
from iqring import IqRing


def test_reads_exactly_n_in_arrival_order():
    r = IqRing(100)
    r.write(b"ab")
    r.write(b"cd")
    assert r.read(3, timeout_s=0.1) == b"abc"
    assert r.read(1, timeout_s=0.1) == b"d"
    assert r.pending() == 0


def test_overflow_drops_oldest_and_counts():
    r = IqRing(4)
    r.write(b"ab")
    r.write(b"cd")
    r.write(b"ef")                      # cap 4 -> "ab" dropped
    assert r.dropped_bytes == 2
    assert r.read(4, timeout_s=0.1) == b"cdef"


def test_hackrf_source_still_reexports_iqring():
    from hackrf_source import IqRing as HR
    assert HR is IqRing              # moved, not duplicated
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest agent/scan/tests/test_iqring.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'iqring'`.

- [ ] **Step 3: Write minimal implementation**

Створити `agent/scan/iqring.py`, ПЕРЕНІСШИ клас `IqRing` дослівно з `hackrf_source.py`:

```python
"""Bounded byte FIFO shared by the in-process view capture sources (HackRF, bladeRF).

Overflow drops the OLDEST buffers — air lost, counted, surfaced as dropped_bytes."""
import threading
import time
from collections import deque


class IqRing:
    """Bounded byte FIFO between a capture producer (USB callback / reader thread)
    and read(). Overflow drops the OLDEST buffers — air lost, counted, surfaced
    as the dropped_chunks stat."""

    def __init__(self, capacity_bytes):
        self._cap = int(capacity_bytes)
        self._d = deque()
        self._size = 0
        self.dropped_bytes = 0
        self._cond = threading.Condition()

    def write(self, buf):
        with self._cond:
            self._d.append(buf)
            self._size += len(buf)
            while self._size > self._cap and len(self._d) > 1:
                old = self._d.popleft()
                self._size -= len(old)
                self.dropped_bytes += len(old)
            self._cond.notify()

    def read(self, n, timeout_s):
        """Exactly n bytes in arrival order, or None on timeout (underrun)."""
        deadline = time.monotonic() + timeout_s
        with self._cond:
            while self._size < n:
                left = deadline - time.monotonic()
                if left <= 0:
                    return None
                self._cond.wait(left)
            out = bytearray()
            while len(out) < n:
                buf = self._d.popleft()
                take = min(len(buf), n - len(out))
                out += buf[:take]
                if take < len(buf):
                    self._d.appendleft(buf[take:])
                self._size -= take
            return bytes(out)

    def clear(self):
        with self._cond:
            self._d.clear()
            self._size = 0

    def pending(self):
        with self._cond:
            return self._size
```

У `agent/scan/hackrf_source.py`: ВИДАЛИТИ клас `IqRing` (рядки 15-64) і додати імпорт зверху файлу.
`threading`, `time`, `from collections import deque` після виносу використовує лише IqRing — прибрати
їх; лишити `import logging`. Верх файлу має стати:

```python
"""In-process HackRF capture for the SDR live view.
... (докстрінг без змін) ..."""
import logging

from iqring import IqRing

LOG = logging.getLogger("scan.hackrf")
```

Переконатися, що `RING_SECONDS = 2.0` і `class HackrfSource` лишаються без змін.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest agent/scan/tests/test_iqring.py agent/scan/tests/test_hackrf_source.py -v`
Expected: PASS (новий файл + усі 8 наявних `test_hackrf_source` тестів — `IqRing` re-export працює).

- [ ] **Step 5: Commit**

```bash
git add agent/scan/iqring.py agent/scan/hackrf_source.py agent/scan/tests/test_iqring.py
git commit -m "refactor(scan): extract IqRing to shared module for reuse

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JLJdXSvLMBojY7w2dcUwuN"
```

---

### Task 3: `run_stream_source` бере wire-format із джерела

**Files:**
- Modify: `agent/video/stream_demod.py:439-505` (`run_stream_source`)
- Test: `agent/video/tests/test_stream_demod.py`

**Interfaces:**
- Consumes (duck-type джерела, опційно): `source.bytes_per_sample: int` (default 2),
  `source.to_iq(buf) -> np.ndarray[complex64]` (default `iq_from_int8_fast`).
- HackRF-джерело не оголошує їх → дефолти = int8/2 байти (поведінка без змін).

- [ ] **Step 1: Write the failing test**

Додати в `agent/video/tests/test_stream_demod.py` (після наявних `run_stream_source`-тестів,
поряд з рядком ~670), використовуючи наявні хелпери `_vcfg`, `CHUNK_S`, `make_cvbs`, `fm_modulate`
та `_FakeEncoder`:

```python
from bladerf_source import iq_from_sc16q11


def _sc16_chunk_bytes(fs, seconds=CHUNK_S):
    """PAL CVBS -> FM IQ quantized to SC16_Q11 (int16 interleaved), len = int(fs*4*seconds)."""
    img = (np.indices((32, 32)).sum(axis=0) % 2).astype(float)
    bb = make_cvbs("PAL", img, fs, frames=max(1, int(round(seconds * 25))))
    iq = fm_modulate(bb, fs, 2e6)
    n = int(fs * seconds)
    iq = np.resize(iq, n)
    i16 = np.empty(2 * n, dtype=np.int16)
    i16[0::2] = np.clip(np.real(iq) * 2000, -2047, 2047).astype(np.int16)
    i16[1::2] = np.clip(np.imag(iq) * 2000, -2047, 2047).astype(np.int16)
    return i16.tobytes()


class _Sc16Source:
    """4-bytes/sample source: run_stream_source must size chunks x4 and use to_iq."""
    bytes_per_sample = 4

    def __init__(self, chunk, stop):
        self._chunk = chunk
        self._stop = stop
        self.asked = []
        self.to_iq_calls = 0
        self.dropped_bytes = 0

    def tune(self, hz):
        pass

    def read_chunk(self, n, timeout_s):
        self.asked.append(n)
        if self._chunk is None:
            return None
        c, self._chunk = self._chunk, None
        self._stop.set()                # one chunk, then clean stop
        return c

    def to_iq(self, buf):
        self.to_iq_calls += 1
        return iq_from_sc16q11(buf)

    def pending_bytes(self):
        return 0


def test_run_stream_source_uses_source_wire_format():
    fs = 4e6
    stop = threading.Event()
    src = _Sc16Source(_sc16_chunk_bytes(fs), stop)
    fenc = _FakeEncoder()
    err = run_stream_source(_vcfg(), src, 947.0, stop, max_s=60.0, encoder=fenc,
                            clock=lambda: 0.0)
    assert err is None
    assert src.asked[0] == int(fs * 4 * CHUNK_S)         # sized by bytes_per_sample=4
    assert src.to_iq_calls >= 1                           # source decoder used, not int8
    assert fenc.frames and all(len(f) == 320 * VIEW_CANVAS_HEIGHT for f in fenc.frames)
```

Переконатися, що `VIEW_CANVAS_HEIGHT` імпортований у тест-файлі (він уже використовується наявними
тестами; якщо ні — додати `from stream_demod import VIEW_CANVAS_HEIGHT`).

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest agent/video/tests/test_stream_demod.py::test_run_stream_source_uses_source_wire_format -v`
Expected: FAIL — `src.asked[0]` дорівнює `int(fs*2*CHUNK_S)` (старий int8-розмір), не `*4`; або кадри
порожні (int8-декод сирих int16 байтів дає сміття).

- [ ] **Step 3: Write minimal implementation**

У `agent/video/stream_demod.py`, у `run_stream_source` (початок, де `fs = vcfg.view_sample_rate_hz`,
рядок ~450), замінити розрахунок `chunk_bytes` і декод:

```python
    clock = clock or time.monotonic
    fs = vcfg.view_sample_rate_hz
    bytes_per_sample = getattr(source, "bytes_per_sample", 2)
    to_iq = getattr(source, "to_iq", None) or iq_from_int8_fast
    chunk_bytes = int(fs * bytes_per_sample * CHUNK_S)
```

І нижче в циклі замінити `iq = iq_from_int8_fast(buf)` на:

```python
        iq = to_iq(buf)
```

(решта тіла `run_stream_source` — без змін.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest agent/video/tests/test_stream_demod.py -v`
Expected: PASS — новий тест зелений І всі наявні `run_stream_source`/`run_stream`/`run_stream_persistent`
тести зелені (HackRF-шлях через дефолти int8/2 байти незмінний).

- [ ] **Step 5: Commit**

```bash
git add agent/video/stream_demod.py agent/video/tests/test_stream_demod.py
git commit -m "feat(view): run_stream_source uses per-source IQ wire format

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JLJdXSvLMBojY7w2dcUwuN"
```

---

### Task 4: `BladerfViewSource` (in-process bladeRF capture для вью)

**Files:**
- Modify: `agent/scan/bladerf_source.py` (додати наприкінці файлу)
- Test: `agent/scan/tests/test_bladerf_source.py`

**Interfaces:**
- Consumes: `iqring.IqRing` (Task 2); `iq_from_sc16q11` (вже у цьому модулі).
- Produces:
  - `BladerfViewSource(open_radio, sample_rate_hz, read_samples=65536, ring_s=2.0)` з duck-type:
    `tune(freq_hz)`, `read_chunk(n_bytes, timeout_s) -> bytes|None`, `recover()`, `close()`,
    `pending_bytes() -> int`, властивість `dropped_bytes`, атрибут `bytes_per_sample = 4`,
    `to_iq(raw) -> complex64`. `open_radio()` повертає об'єкт з `set_frequency(hz)`,
    `read(num_samples) -> bytes` (сирі SC16_Q11), `close()`.
  - `open_bladerf_view_radio(gain_db, sample_rate_hz, bandwidth_hz)` — прод-фабрика радіо
    (єдине bladeRF-touching місце тут).

- [ ] **Step 1: Write the failing test**

Додати в `agent/scan/tests/test_bladerf_source.py` (наприкінці файлу):

```python
import collections
import threading

from bladerf_source import BladerfViewSource, iq_from_sc16q11
import numpy as np


class _FakeBladeRadio:
    """Streaming radio double: read() blocks briefly for fed data, else raises (timeout)."""
    def __init__(self):
        self.freqs = []
        self.closed = False
        self._q = collections.deque()
        self._cv = threading.Condition()

    def set_frequency(self, hz):
        self.freqs.append(hz)

    def feed(self, data):
        with self._cv:
            self._q.append(data)
            self._cv.notify()

    def read(self, num_samples):
        with self._cv:
            if not self._q:
                self._cv.wait(0.05)
            if not self._q:
                raise TimeoutError("no samples")
            return self._q.popleft()

    def close(self):
        self.closed = True


def _view_source(radios, read_samples=2):
    def factory():
        r = _FakeBladeRadio()
        radios.append(r)
        return r
    return BladerfViewSource(factory, sample_rate_hz=4e6, read_samples=read_samples)


def test_view_source_wire_format_fields():
    s = BladerfViewSource(lambda: None, 4e6)
    assert s.bytes_per_sample == 4
    raw = np.array([2048, 0, 0, 2048], dtype=np.int16).tobytes()
    iq = s.to_iq(raw)
    assert iq.dtype == np.complex64
    assert np.allclose(iq, np.array([1 + 0j, 0 + 1j], dtype=np.complex64))


def test_tune_opens_lazily_sets_freq_and_delivers():
    radios = []
    s = _view_source(radios)
    s.tune(947e6)
    assert len(radios) == 1 and radios[0].freqs == [947000000]
    radios[0].feed(b"01234567")                         # 8 raw bytes = 2 SC16 samples
    assert s.read_chunk(8, timeout_s=1.0) == b"01234567"
    s.close()
    assert radios[0].closed


def test_recover_reopens_and_retunes():
    radios = []
    s = _view_source(radios)
    s.tune(947e6)
    s.recover()
    assert radios[0].closed
    assert len(radios) == 2 and radios[1].freqs == [947000000]
    s.close()


def test_close_stops_reader_and_reopens_on_next_tune():
    radios = []
    s = _view_source(radios)
    s.tune(947e6)
    s.close()
    s.close()                                            # idempotent
    assert radios[0].closed
    s.tune(905e6)
    assert len(radios) == 2 and radios[1].freqs == [905000000]
    s.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest agent/scan/tests/test_bladerf_source.py -v`
Expected: FAIL — `ImportError: cannot import name 'BladerfViewSource'`.

- [ ] **Step 3: Write minimal implementation**

Додати в кінець `agent/scan/bladerf_source.py`:

```python
import threading
import time

from iqring import IqRing

VIEW_RING_SECONDS = 2.0            # rx ring depth: absorbs demod hiccups, bounds memory
VIEW_READ_SAMPLES = 65536         # samples per sync_rx pull (~8 ms at 8 MS/s): bounds stop latency


class BladerfViewSource:
    """CaptureSource for the view stream over an injected streaming radio factory.

    Mirrors HackrfSource, but bladeRF has no rx callback: a reader thread pulls
    fixed sub-chunks via radio.read() (blocking sync_rx) and writes raw SC16_Q11
    into the shared IqRing, so capture overlaps demod (the demod side drains the
    ring). read_chunk returns RAW bytes; run_stream_source decodes via to_iq."""

    bytes_per_sample = 4          # SC16_Q11: 2 x int16 per complex sample

    def __init__(self, open_radio, sample_rate_hz, read_samples=VIEW_READ_SAMPLES,
                 ring_s=VIEW_RING_SECONDS):
        self._open_radio = open_radio
        self._fs = float(sample_rate_hz)
        self._read_samples = int(read_samples)
        self._ring = IqRing(int(self._fs * self.bytes_per_sample * ring_s))
        self._radio = None
        self._freq_hz = None
        self._reader = None
        self._stop_reader = None

    @staticmethod
    def to_iq(raw):
        return iq_from_sc16q11(raw)

    @property
    def dropped_bytes(self):
        return self._ring.dropped_bytes

    def pending_bytes(self):
        return self._ring.pending()

    def tune(self, freq_hz):
        if self._radio is None:
            self._radio = self._open_radio()
            self._start_reader(self._radio)
        self._radio.set_frequency(int(freq_hz))
        self._freq_hz = int(freq_hz)
        self._ring.clear()               # the tune transient must not reach the demod

    def read_chunk(self, n_bytes, timeout_s):
        return self._ring.read(n_bytes, timeout_s)

    def recover(self):
        """USB-wedge watchdog action: close + reopen + retune."""
        freq = self._freq_hz
        self.close()
        if freq is not None:
            self.tune(freq)

    def close(self):
        r, self._radio = self._radio, None
        if r is None:
            return
        if self._stop_reader is not None:
            self._stop_reader.set()
        if self._reader is not None:
            self._reader.join(timeout=2.0)
        self._reader = None
        self._stop_reader = None
        try:
            r.close()
        except Exception:
            LOG.exception("bladeRF view close failed")

    def _start_reader(self, radio):
        self._stop_reader = threading.Event()
        self._reader = threading.Thread(target=self._read_loop, args=(radio, self._stop_reader),
                                        daemon=True)
        self._reader.start()

    def _read_loop(self, radio, stop):
        while not stop.is_set():
            try:
                buf = radio.read(self._read_samples)
            except Exception:
                if stop.is_set():
                    return
                time.sleep(0.01)         # rx timeout / transient: back off, re-check stop
                continue
            if buf:
                self._ring.write(buf)


class BladeRfViewRadio:
    """Streaming RX handle for BladerfViewSource: continuous sync_rx, retune via
    set_frequency. Enables the module on the first tune. The radio/channel/enums are
    injected (see open_bladerf_view_radio) so this class imports nothing from `bladerf`."""

    def __init__(self, radio, channel):
        self._radio = radio
        self._ch = channel
        self._enabled = False

    def set_frequency(self, hz):
        self._radio.set_frequency(self._ch, int(hz))
        if not self._enabled:
            self._radio.enable_module(self._ch, True)
            self._enabled = True

    def read(self, num_samples):
        buf = bytearray(int(num_samples) * 4)          # SC16_Q11 = 2 x int16 per sample
        self._radio.sync_rx(buf, int(num_samples))
        return bytes(buf)

    def close(self):
        try:
            if self._enabled:
                self._radio.enable_module(self._ch, False)
                self._enabled = False
        except Exception:
            LOG.exception("bladeRF view disable failed")
        finally:
            try:
                self._radio.close()
            except Exception:
                LOG.exception("bladeRF view radio close failed")


def open_bladerf_view_radio(gain_db, sample_rate_hz, bandwidth_hz) -> BladeRfViewRadio:
    """Open the first bladeRF configured for continuous view streaming.
    The only function here that imports `bladerf`. Raises on no device."""
    import bladerf
    from bladerf import _bladerf
    radio = bladerf.BladeRF()
    ch = bladerf.CHANNEL_RX(0)
    radio.set_gain_mode(ch, _bladerf.GainMode.Manual)
    radio.set_gain(ch, int(gain_db))
    radio.set_sample_rate(ch, int(sample_rate_hz))
    radio.set_bandwidth(ch, int(bandwidth_hz))
    radio.sync_config(
        layout=_bladerf.ChannelLayout.RX_X1, fmt=_bladerf.Format.SC16_Q11,
        num_buffers=16, buffer_size=8192, num_transfers=8, stream_timeout=3500,
    )
    return BladeRfViewRadio(radio, ch)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest agent/scan/tests/test_bladerf_source.py -v`
Expected: PASS — усі наявні bladeRF-тести + 4 нові.

- [ ] **Step 5: Commit**

```bash
git add agent/scan/bladerf_source.py agent/scan/tests/test_bladerf_source.py
git commit -m "feat(view): BladerfViewSource — in-process bladeRF view capture

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JLJdXSvLMBojY7w2dcUwuN"
```

---

### Task 5: `main.py` — bladeRF-гілка вью + `SCAN_ENABLED`-gate

**Files:**
- Modify: `agent/scan/main.py:269-293` (вибір джерела вью) та `:322-361` (головний цикл)
- Test: `agent/scan/tests/test_run_cycle.py`

**Interfaces:**
- Consumes: `BladerfViewSource`, `open_bladerf_view_radio` (Task 4); `cfg.scan_enabled` (Task 1);
  `cfg.bladerf_gain_db`, `viewcfg.view_sample_rate_hz`; наявні `_reset_bladerf_backend`,
  `stream_demod.run_stream_source`, `nearest_channel`.

- [ ] **Step 1: Write the failing tests**

Додати в `agent/scan/tests/test_run_cycle.py`:

```python
def test_main_viewer_only_skips_sweep(monkeypatch):
    # SCAN_ENABLED off: run_cycle is never called; the loop idles awaiting view commands.
    cfg = Config()
    cfg.source = "live"
    cfg.sdr = "hackrf"
    cfg.scan_enabled = False
    cfg.mqtt_enabled = False
    cfg.local_http_port = 0
    cfg.rx5808_enabled = False
    monkeypatch.setattr(main, "load_config", lambda: cfg)
    monkeypatch.setenv("FPV_VIDEO_ENABLED", "0")     # deterministic: skip emitter init

    cycles = [0]
    def _cycle(*a, **k):
        cycles[0] += 1
        raise AssertionError("run_cycle must not run when scan_enabled is False")
    monkeypatch.setattr(main, "run_cycle", _cycle)

    sleeps = []
    def _sleep(s):
        sleeps.append(s)
        raise KeyboardInterrupt()           # end the loop at the idle sleep
    monkeypatch.setattr(main, "time", types.SimpleNamespace(time=main.time.time, sleep=_sleep))

    with pytest.raises(KeyboardInterrupt):
        main.main()
    assert cycles[0] == 0                    # sweep gated off
    assert sleeps == [0.2]                   # hit the viewer-only idle sleep


def test_main_wires_bladerf_view_source(monkeypatch):
    # sdr=bladerf + view enabled must construct BladerfViewSource (not the hackrf_transfer path).
    cfg = Config()
    cfg.source = "live"
    cfg.sdr = "bladerf"
    cfg.mqtt_enabled = True
    cfg.local_http_port = 0
    cfg.rx5808_enabled = False
    monkeypatch.setattr(main, "load_config", lambda: cfg)

    class _FakePublisher:
        def __init__(self, *a, **k):
            self.on_command = None
            self.on_view_command = None
            self.on_connected = None
        def connect(self, ts): pass
        def publish_view(self, *a, **k): pass
    monkeypatch.setattr(main, "MqttPublisher", _FakePublisher)

    monkeypatch.setenv("VIEW_ENABLED", "1")
    monkeypatch.setenv("VIEW_PUSH_URL", "rtsp://u:p@h:8554/bladerf-view")
    monkeypatch.setenv("FPV_VIDEO_ENABLED", "0")         # no emitter

    import view_encoder
    class _FakeEnc:
        def __init__(self, *a, **k): pass
        def start(self): pass
        def idle(self): pass
    monkeypatch.setattr(view_encoder, "ViewEncoder", _FakeEnc)

    import bladerf_source
    made = []
    class _SpySource:
        bytes_per_sample = 4
        def __init__(self, *a, **k): made.append((a, k))
        def close(self): pass
    monkeypatch.setattr(bladerf_source, "BladerfViewSource", _SpySource)
    monkeypatch.setattr(bladerf_source, "open_bladerf_view_radio", lambda *a, **k: None)

    def _cycle(*a, **k):
        raise KeyboardInterrupt()            # end the loop right after wiring/setup
    monkeypatch.setattr(main, "run_cycle", _cycle)
    monkeypatch.setattr(main, "time", types.SimpleNamespace(time=main.time.time, sleep=lambda s: None))

    with pytest.raises(KeyboardInterrupt):
        main.main()
    assert len(made) == 1                    # bladeRF view source WAS wired
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest agent/scan/tests/test_run_cycle.py::test_main_viewer_only_skips_sweep agent/scan/tests/test_run_cycle.py::test_main_wires_bladerf_view_source -v`
Expected: FAIL — перший: `run_cycle` викликається (немає gate) → AssertionError; другий:
`made` порожній (bladeRF іде в `run_stream_persistent`, `BladerfViewSource` не конструюється).

- [ ] **Step 3: Write minimal implementation**

**(a) bladeRF-гілка вью.** У `agent/scan/main.py`, у блоці `if viewcfg.view_engine == "persistent":`
(рядки ~269-286) замінити `if cfg.sdr == "hackrf" and cfg.source == "live": ... else: ...` на:

```python
                if cfg.sdr == "hackrf" and cfg.source == "live":
                    from hackrf_source import HackrfSource, open_hackrf_radio
                    source = HackrfSource(
                        lambda: open_hackrf_radio(cfg.lna_gain, cfg.vga_gain, cfg.amp_enable),
                        viewcfg.view_sample_rate_hz)
                    run = lambda freq, stop, max_s: stream_demod.run_stream_source(
                        viewcfg, source, freq, stop, max_s, encoder,
                        channel_of=nearest_channel)
                    reset = source.close     # release the device for the sweep; no USB re-enum per session
                elif cfg.sdr == "bladerf" and cfg.source == "live":
                    from bladerf_source import BladerfViewSource, open_bladerf_view_radio
                    source = BladerfViewSource(
                        lambda: open_bladerf_view_radio(
                            cfg.bladerf_gain_db, viewcfg.view_sample_rate_hz,
                            viewcfg.view_sample_rate_hz),
                        viewcfg.view_sample_rate_hz)
                    def _run_blade_view(freq, stop, max_s):
                        _reset_bladerf_backend()   # free the sweep's bladeRF before the view opens it
                        return stream_demod.run_stream_source(
                            viewcfg, source, freq, stop, max_s, encoder,
                            channel_of=nearest_channel)
                    run = _run_blade_view
                    reset = source.close     # on exit the next sweep cycle reopens the backend
                else:
                    run = lambda freq, stop, max_s: stream_demod.run_stream_persistent(
                        viewcfg, freq, stop, max_s, encoder,
                        lna=cfg.lna_gain, vga=cfg.vga_gain, amp=cfg.amp_enable,
                        channel_of=nearest_channel)
```

**(b) `SCAN_ENABLED`-gate.** У головному циклі (рядки ~322-339), одразу ПІСЛЯ блоку
`if req is not None: ... continue` і ПЕРЕД `payload = run_cycle(...)` вставити:

```python
            if not cfg.scan_enabled:
                time.sleep(0.2)          # viewer-only: no sweep, just await view commands
                continue
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest agent/scan/tests/test_run_cycle.py -v`
Expected: PASS — 2 нові тести + усі наявні `test_main_*`/`test_run_cycle_*` (default `scan_enabled=True`
зберігає стару поведінку).

- [ ] **Step 5: Commit**

```bash
git add agent/scan/main.py agent/scan/tests/test_run_cycle.py
git commit -m "feat(view): wire bladeRF view source + SCAN_ENABLED gate in main loop

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JLJdXSvLMBojY7w2dcUwuN"
```

---

### Task 6: Повний прогін тестів + деплой-документація

**Files:**
- Modify: `docs/bladerf-scanner-deploy.md`

- [ ] **Step 1: Прогнати ВСІ python-тести (регресія)**

Run: `python -m pytest agent -q`
Expected: PASS — уся python-сюїта зелена (scan + video + telemetry).

- [ ] **Step 2: Прогнати Node-тести (мають бути незмінно зелені — код не чіпали)**

Run: `npm test`
Expected: PASS (напр. 126/126) — дашборд/сервер не змінювались.

- [ ] **Step 3: Додати секцію деплою**

Додати в кінець `docs/bladerf-scanner-deploy.md`:

```markdown
## bladeRF як вьювер + ролі SDR (2026-07-12)

Будь-який SDR може свіпити та/або стрімити. Роль юніта = два прапорці:

| env | дія | default |
|-----|-----|---------|
| `SCAN_ENABLED` | вмикає свіп-цикл | `1` |
| `VIEW_ENABLED` | вмикає вью-стрім | `0` |

- **Чистий свіпер:** `SCAN_ENABLED=1 VIEW_ENABLED=0` (напр. bladeRF на всі бенди).
- **Чистий вьювер:** `SCAN_ENABLED=0 VIEW_ENABLED=1 VIEW_PUSH_URL=rtsp://<pubuser>:<pass>@10.8.0.1:8554/<stream>`.
- **Обидва (один SDR):** `SCAN_ENABLED=1 VIEW_ENABLED=1` — свіпить, паузить на вью, повертається.

**bladeRF-вьювер:** виставити `SCAN_SDR=bladerf`, `VIEW_ENABLED=1`,
`VIEW_PUSH_URL=.../bladerf-view`. Підсилення береться з `BLADERF_GAIN`, sample_rate з
`VIEW_SAMPLE_RATE_HZ` (default 8 MS/s).

**Сервер (один раз):** зареєструвати publisher-девайс + MediaMTX-шлях `bladerf-view`
(як існуючий `hackrf-view`) у `devices.yml`, `node bin/gen-mediamtx.js`, перезапустити MediaMTX.
Дашборд підхопить новий стрім з ретейн-анонсу `fpv/<id>/view` — коду сервера/дашборда міняти НЕ треба.

**Два SDR на вузлі:** два systemd-юніти, по одному на пристрій, ролі призначаються вільно
(напр. bladeRF `SCAN_ENABLED=1 VIEW_ENABLED=0` + HackRF `SCAN_ENABLED=0 VIEW_ENABLED=1`, або навпаки).
Різні процеси/пристрої → свіп і стрім ідуть паралельно, без взаємних пауз. НЕ перезаписувати
hand-diverged unit-файли — редагувати їхні `Environment=`/`EnvironmentFile`.
```

- [ ] **Step 4: Commit**

```bash
git add docs/bladerf-scanner-deploy.md
git commit -m "docs(view): bladeRF viewer + SDR role deploy notes

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JLJdXSvLMBojY7w2dcUwuN"
```

---

## Live acceptance (після мержу, з реальним залізом)

Не автоматизується (потрібне живе аналогове TX + SDR). Кроки:
1. На bladeRF-юніті виставити `VIEW_ENABLED=1` + `VIEW_PUSH_URL=.../bladerf-view`, зареєструвати шлях
   на сервері, `restart` юніта.
2. У дашборді панель «FPV Viewer» → bladeRF анонсує `stream:"bladerf-view"`.
3. Клік на детекцію (напр. живий TX ~4240 МГц) → відео у плеєрі за секунди.
4. Ретьюн на іншу детекцію → перемикання (session restart, WHEP авто-reconnect).
5. Stop/timeout → якщо `SCAN_ENABLED=1`, свіп відновлюється (спектр знову оновлюється); якщо
   `SCAN_ENABLED=0`, юніт просто ідлить.
6. **Два SDR:** поки bladeRF стрімить, другий SDR свіпить — список детекцій лишається свіжим
   (перевірити паралельність, відсутність взаємних пауз).
7. Метрика якості: `mailbox`/`dropped_chunks` у stats-лозі агента (як для HackRF); при 8 MS/s
   демод має встигати (`dropped_chunks` близько 0 у steady-state).

## Self-review (звірка з спекою)

- ✅ bladeRF як вью-джерело (Task 4) — duck-type як HackrfSource, reader-потік + IqRing.
- ✅ Формат IQ у run_stream_source (Task 3) — bytes_per_sample + to_iq.
- ✅ Роль-конфіг SCAN_ENABLED (Task 1) + gate (Task 5b).
- ✅ Wiring main.py bladeRF-гілки (Task 5a); прибрано хибний hackrf_transfer-шлях для bladeRF.
- ✅ Арбітрація пристрою single-SDR (Task 5a: `_reset_bladerf_backend()` у `_run_blade_view`; вихід →
  `reset=source.close`, наступний свіп перевідкриває бекенд).
- ✅ IqRing extraction (Task 2).
- ✅ Тести: BladerfViewSource, to_iq/bytes_per_sample, run_stream_source-seam, SCAN_ENABLED gate,
  config-парсинг, wiring.
- ✅ Деплой без змін сервера/дашборда (Task 6): реєстрація bladerf-view шляху + env.
- ✅ Дашборд/Node-код не чіпається (Task 6 Step 2 перевіряє регресію).
