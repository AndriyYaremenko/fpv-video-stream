# Smooth 15 fps View Stream Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the ~40% air gaps in the SDR view stream by decoupling demod from output pacing — a writer thread paces frames from a bounded queue into ffmpeg while the demod loop keeps up with the capture mailbox.

**Architecture:** All changes in `agent/video/stream_demod.py`. Two new small units (`ChunkMailbox` with a dropped-chunk counter, `FrameQueue` bounded drop-oldest FIFO) plus a `writer_loop` thread body that owns the existing `FramePacer` and the ffmpeg stdin. `run_stream`'s public contract is unchanged. A `--pipeline` bench mode proves zero chunk drops at the real-time rate.

**Tech Stack:** Python 3 / numpy / threading (stdlib only — no new deps), pytest with fake clocks/pipes (existing patterns in `agent/video/tests/test_stream_demod.py`).

**Spec:** `docs/superpowers/specs/2026-07-07-view-smooth-15fps-design.md`

## Global Constraints

- `run_stream(vcfg, freq_mhz, stop_event, max_s, lna=40, vga=20, amp=0, popen=None, clock=None, sleep=None) -> error|None` — signature and semantics unchanged (view controller/retune untouched).
- Queue capacity: `int(vcfg.view_fps * 1.0)` frames (~1 s); overflow drops the OLDEST frame and counts it.
- Stop/retune (`stop_event`) exits WITHOUT draining the queue; clean exits (timeout, capture death) drain the tail.
- Stats log line (the acceptance metric), every ~10 s from the writer:
  `view stream: <fps> fps, queue=<n>, dropped_frames=<n>, dropped_chunks=<n>`.
- Tests: `python -m pytest agent/video/tests -q` (run from repo root; flat-module imports via `agent/video/conftest.py`); the three existing `run_stream` integration tests MUST keep passing unmodified.
- No new dependencies; VIEW_FPS stays 15 in production config.
- Commit after every task (conventional commits).

---

### Task 1: `ChunkMailbox` — counted single-slot chunk handoff

**Files:**
- Modify: `agent/video/stream_demod.py` (add class near `CHUNK_S`; rewire `_reader`/loop in Task 4)
- Test: `agent/video/tests/test_stream_demod.py`

**Interfaces:**
- Produces: `ChunkMailbox` with `put(buf)` (replacing an unconsumed chunk increments `self.dropped`), `take() -> buf|None` (consumes), attribute `dropped: int`. Thread-safe. Task 4's `run_stream` and Task 5's bench consume it.

- [ ] **Step 1: Write the failing test**

Append to `agent/video/tests/test_stream_demod.py`:

```python
from stream_demod import ChunkMailbox


def test_chunk_mailbox_counts_overwrites():
    mb = ChunkMailbox()
    assert mb.take() is None
    mb.put(b"a")
    assert mb.take() == b"a" and mb.take() is None
    assert mb.dropped == 0
    mb.put(b"b")
    mb.put(b"c")                     # unconsumed "b" is replaced -> 1 dropped chunk
    assert mb.dropped == 1
    assert mb.take() == b"c"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest agent/video/tests/test_stream_demod.py::test_chunk_mailbox_counts_overwrites -q`
Expected: FAIL — `ImportError: cannot import name 'ChunkMailbox'`.

- [ ] **Step 3: Implement**

In `agent/video/stream_demod.py`, after the `CHUNK_S = 0.5` line:

```python
class ChunkMailbox:
    """Single-slot 'latest chunk' handoff from the USB reader to the demod loop.
    Replacing an unconsumed chunk counts as a dropped chunk (air lost)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._buf = None
        self.dropped = 0

    def put(self, buf):
        with self._lock:
            if self._buf is not None:
                self.dropped += 1
            self._buf = buf

    def take(self):
        with self._lock:
            buf, self._buf = self._buf, None
            return buf
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest agent/video/tests/test_stream_demod.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/video/stream_demod.py agent/video/tests/test_stream_demod.py
git commit -m "feat(view): counted single-slot chunk mailbox"
```

---

### Task 2: `FrameQueue` — bounded drop-oldest frame FIFO

**Files:**
- Modify: `agent/video/stream_demod.py`
- Test: `agent/video/tests/test_stream_demod.py`

**Interfaces:**
- Produces: `FrameQueue(maxlen)` with `put(frame)` (never blocks; full → drops OLDEST, increments `dropped`), `get(timeout=0.1) -> frame|None` (None = closed-and-drained OR timeout), `close()`, `__len__`, attributes `dropped: int`, `maxlen: int`, property `closed: bool`. Tasks 3–5 consume it.

- [ ] **Step 1: Write the failing tests**

Append to `agent/video/tests/test_stream_demod.py`:

```python
from stream_demod import FrameQueue


def test_frame_queue_fifo_and_drop_oldest():
    q = FrameQueue(maxlen=2)
    q.put(b"1"); q.put(b"2")
    assert len(q) == 2 and q.dropped == 0
    q.put(b"3")                       # full: "1" (oldest) is dropped
    assert q.dropped == 1
    assert q.get() == b"2" and q.get() == b"3"
    assert q.get(timeout=0.01) is None            # empty, not closed -> timeout


def test_frame_queue_close_drains_then_none():
    q = FrameQueue(maxlen=4)
    q.put(b"a"); q.put(b"b")
    q.close()
    assert q.closed
    assert q.get() == b"a" and q.get() == b"b"    # close still drains the tail
    assert q.get(timeout=0.01) is None            # drained -> end of stream
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest agent/video/tests/test_stream_demod.py -q -k frame_queue`
Expected: FAIL — `ImportError: cannot import name 'FrameQueue'`.

- [ ] **Step 3: Implement**

In `agent/video/stream_demod.py`, after `ChunkMailbox` (needs `from collections import deque` at the top imports):

```python
class FrameQueue:
    """Bounded frame FIFO between the demod loop and the writer thread.
    put() never blocks: when full, the OLDEST frame is dropped (the live tail
    matters more than stale frames). close() marks end-of-stream: get() drains
    the remainder, then returns None."""

    def __init__(self, maxlen):
        self.maxlen = max(1, int(maxlen))
        self.dropped = 0
        self._d = deque()
        self._cond = threading.Condition()
        self._closed = False

    def __len__(self):
        with self._cond:
            return len(self._d)

    @property
    def closed(self):
        with self._cond:
            return self._closed

    def put(self, frame):
        with self._cond:
            if len(self._d) >= self.maxlen:
                self._d.popleft()
                self.dropped += 1
            self._d.append(frame)
            self._cond.notify()

    def get(self, timeout=0.1):
        with self._cond:
            if not self._d and not self._closed:
                self._cond.wait(timeout)
            if self._d:
                return self._d.popleft()
            return None

    def close(self):
        with self._cond:
            self._closed = True
            self._cond.notify_all()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest agent/video/tests/test_stream_demod.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/video/stream_demod.py agent/video/tests/test_stream_demod.py
git commit -m "feat(view): bounded drop-oldest frame queue"
```

---

### Task 3: `writer_loop` — paced writer-thread body with stats log

**Files:**
- Modify: `agent/video/stream_demod.py`
- Test: `agent/video/tests/test_stream_demod.py`

**Interfaces:**
- Consumes: `FrameQueue` (Task 2), existing `FramePacer`.
- Produces: `writer_loop(q, pacer, enc, stop_event, err, dropped_chunks=None, clock=None, log_every_s=10.0)` — runs until stop / queue closed-and-drained / write error / encoder death. `err` is a shared one-slot dict `{"msg": None}`; failures set `err["msg"]` and return. `dropped_chunks` is a zero-arg callable for the stats log. Task 4 runs it in a thread; Task 5's bench runs it too.

- [ ] **Step 1: Write the failing tests**

Append to `agent/video/tests/test_stream_demod.py`:

```python
from stream_demod import writer_loop


class _Pacer:
    def __init__(self, fail_after=None):
        self.out = []
        self._fail_after = fail_after

    def tick(self, fr):
        if self._fail_after is not None and len(self.out) >= self._fail_after:
            raise BrokenPipeError()
        self.out.append(fr)


def test_writer_loop_drains_closed_queue_in_order():
    q = FrameQueue(maxlen=8)
    for b in (b"1", b"2", b"3"):
        q.put(b)
    q.close()
    pacer = _Pacer()
    err = {"msg": None}
    writer_loop(q, pacer, _FakeProc(), threading.Event(), err, clock=lambda: 0.0)
    assert pacer.out == [b"1", b"2", b"3"]
    assert err["msg"] is None


def test_writer_loop_exits_immediately_on_stop_without_draining():
    q = FrameQueue(maxlen=8)
    q.put(b"1")
    stop = threading.Event()
    stop.set()
    pacer = _Pacer()
    writer_loop(q, pacer, _FakeProc(), stop, {"msg": None}, clock=lambda: 0.0)
    assert pacer.out == []                        # nothing written after stop


def test_writer_loop_reports_pipe_and_encoder_death():
    q = FrameQueue(maxlen=8)
    q.put(b"1"); q.put(b"2")
    err = {"msg": None}
    writer_loop(q, _Pacer(fail_after=1), _FakeProc(), threading.Event(), err,
                clock=lambda: 0.0)
    assert err["msg"] == "ffmpeg pipe closed"

    dead = _FakeProc()
    dead.poll = lambda: 1                         # encoder died, queue open+empty
    err2 = {"msg": None}
    writer_loop(FrameQueue(maxlen=8), _Pacer(), dead, threading.Event(), err2,
                clock=lambda: 0.0)
    assert err2["msg"] == "ffmpeg exited"


def test_writer_loop_logs_stats_line(caplog):
    import logging
    q = FrameQueue(maxlen=8)
    for b in (b"1", b"2", b"3"):
        q.put(b)
    q.close()
    t = [0.0]

    def clock():
        t[0] += 6.0                               # 2 reads cross the 10 s threshold
        return t[0]

    with caplog.at_level(logging.INFO):
        writer_loop(q, _Pacer(), _FakeProc(), threading.Event(), {"msg": None},
                    dropped_chunks=lambda: 7, clock=clock)
    lines = [r.getMessage() for r in caplog.records if "dropped_chunks" in r.getMessage()]
    assert lines and "dropped_chunks=7" in lines[0] and "queue=" in lines[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest agent/video/tests/test_stream_demod.py -q -k writer_loop`
Expected: FAIL — `ImportError: cannot import name 'writer_loop'`.

- [ ] **Step 3: Implement**

In `agent/video/stream_demod.py`, after `FramePacer`:

```python
def writer_loop(q, pacer, enc, stop_event, err, dropped_chunks=None, clock=None,
                log_every_s=10.0):
    """Writer-thread body: pace frames from the queue into ffmpeg stdin.

    Runs until stop_event (immediate, no drain — a retune/stop must not flush
    stale frames), the queue is closed and drained (clean end), a write fails,
    or the encoder dies. Failures land in the shared err slot for the demod
    loop to pick up. Logs the smoothness stats (the acceptance metric) every
    ~log_every_s seconds."""
    clock = clock or time.monotonic
    written = 0
    last_log = clock()
    last_written = 0
    while not stop_event.is_set():
        fr = q.get(timeout=0.1)
        if fr is None:
            if q.closed:
                return
            if enc.poll() is not None:
                err["msg"] = "ffmpeg exited"
                return
        else:
            try:
                pacer.tick(fr)
                written += 1
            except (BrokenPipeError, OSError):
                err["msg"] = "ffmpeg pipe closed"
                return
        now = clock()
        if now - last_log >= log_every_s:
            fps = (written - last_written) / (now - last_log)
            LOG.info("view stream: %.1f fps, queue=%d, dropped_frames=%d, dropped_chunks=%d",
                     fps, len(q), q.dropped,
                     dropped_chunks() if dropped_chunks is not None else 0)
            last_log = now
            last_written = written
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest agent/video/tests/test_stream_demod.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/video/stream_demod.py agent/video/tests/test_stream_demod.py
git commit -m "feat(view): paced writer-loop with smoothness stats log"
```

---

### Task 4: Restructure `run_stream` — demod produces, writer thread paces

**Files:**
- Modify: `agent/video/stream_demod.py` (`run_stream`, lines ~103-188)
- Test: `agent/video/tests/test_stream_demod.py`

**Interfaces:**
- Consumes: `ChunkMailbox`, `FrameQueue`, `writer_loop`, `FramePacer` (Tasks 1–3).
- Produces: `run_stream` — SAME signature/return contract. Behavior deltas: demod loop never sleeps for pacing; per-chunk drops become queue-level frame drops; clean exits (timeout/capture death) drain the queue tail, stop/retune exits do not. The three pre-existing `run_stream` integration tests pass unmodified.

- [ ] **Step 1: Write the failing test**

Append to `agent/video/tests/test_stream_demod.py`:

```python
def test_run_stream_writes_every_selected_frame_of_a_chunk():
    # One full chunk then EOF: the writer must drain the ENTIRE select budget
    # (chunk_s * fps frames) before run_stream returns — no air lost to pacing.
    fs = 4e6
    procs = []

    def popen(cmd, **kw):
        p = _FakeProc(stdout=io.BytesIO(_chunk_bytes(fs))) if cmd[0] == "hackrf_transfer" else _FakeProc()
        procs.append(p)
        return p

    t = [0.0]
    err = run_stream(_vcfg(), 947.0, threading.Event(), max_s=60.0, popen=popen,
                     clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + max(s, 0.01)))
    assert err == "hackrf_transfer exited"
    frame_size = 320 * 288
    budget = int(round(CHUNK_S * 10.0))            # _vcfg() fps = 10 -> 5 frames
    assert len(procs[1].stdin.getvalue()) == budget * frame_size
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest agent/video/tests/test_stream_demod.py::test_run_stream_writes_every_selected_frame_of_a_chunk -q`
Expected: PASS or FAIL against the OLD code depending on pacing timing — if it passes, verify it fails against the new architecture requirement differently: the point of this test is the DRAIN guarantee. Record the observed result; after Step 3 it must pass deterministically (the old code's single-thread pacing makes it timing-dependent, the new drain join makes it exact).

- [ ] **Step 3: Implement**

Replace `run_stream` in `agent/video/stream_demod.py` with:

```python
def run_stream(vcfg, freq_mhz, stop_event, max_s, lna=40, vga=20, amp=0,
               popen=None, clock=None, sleep=None):
    """Blocking capture->demod->queue loop for one view session.

    A writer thread paces queued frames into ffmpeg (writer_loop), so the demod
    loop returns to the mailbox in demod-time only and keeps up with the air.
    Returns None on clean stop/timeout, or an error string when a subprocess
    died. Always kills both subprocesses before returning."""
    popen = popen or subprocess.Popen
    clock = clock or time.monotonic
    sleep = sleep or time.sleep
    fs = vcfg.view_sample_rate_hz
    chunk_bytes = int(fs * 2 * CHUNK_S)
    cap = popen(build_capture_cmd(freq_mhz * 1e6, fs, lna, vga, amp),
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=chunk_bytes)
    enc = None
    q = None
    writer = None
    err = {"msg": None}
    standard = None
    height = None
    error = None
    mailbox = ChunkMailbox()

    def _reader():
        while not stop_event.is_set():
            try:
                buf = cap.stdout.read(chunk_bytes)
            except Exception:
                return
            if not buf or len(buf) < chunk_bytes:
                return                                   # EOF: capture died
            mailbox.put(buf)

    threading.Thread(target=_reader, daemon=True).start()
    t_end = clock() + max_s
    try:
        while not stop_event.is_set() and clock() < t_end:
            if err["msg"]:
                error = err["msg"]
                break
            buf = mailbox.take()
            if buf is None:
                if cap.poll() is not None:
                    error = "hackrf_transfer exited"
                    break
                sleep(0.05)
                continue
            iq = iq_from_int8(buf)
            if standard is None:
                bb = lowpass(fm_demod(iq), fs, vcfg.lpf_cutoff_hz)
                standard = pick_standard(bb, fs, vcfg.view_standard,
                                         vcfg.line_snr_db, vcfg.harm_snr_db)
                height = VIEW_HEIGHT[standard]
                enc = popen(build_encode_cmd(vcfg.view_push_url, vcfg.view_width,
                                             height, vcfg.view_fps),
                            stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
                q = FrameQueue(maxlen=int(vcfg.view_fps * 1.0))
                pacer = FramePacer(vcfg.view_fps, enc.stdin.write, clock=clock, sleep=sleep)
                writer = threading.Thread(
                    target=writer_loop, args=(q, pacer, enc, stop_event, err),
                    kwargs={"dropped_chunks": lambda: mailbox.dropped, "clock": clock},
                    daemon=True)
                writer.start()
                LOG.info("view stream: %s %dx%d @%.0ffps", standard, vcfg.view_width,
                         height, vcfg.view_fps)
            for fr in select_frames(
                    chunk_to_frames(iq, fs, standard, vcfg.view_width, height,
                                    vcfg.lpf_cutoff_hz, vcfg.blank_frac),
                    CHUNK_S, vcfg.view_fps):
                q.put(fr.tobytes())
        if error is None and err["msg"]:
            error = err["msg"]                           # writer failure surfaced after the loop
    finally:
        if q is not None:
            q.close()
        if writer is not None and err["msg"] is None and not stop_event.is_set():
            # Clean end (timeout / capture death): let the writer drain the tail.
            writer.join(timeout=q.maxlen / vcfg.view_fps + 1.0)
        for proc in (cap, enc):
            if proc is None:
                continue
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass
    return error
```

- [ ] **Step 4: Run the whole video suite (existing integration tests must pass unmodified)**

Run: `python -m pytest agent/video/tests -q`
Expected: all PASS, including the untouched `test_run_stream_reports_capture_death_and_kills_procs`, `test_run_stream_stops_cleanly_on_stop_event`, `test_run_stream_times_out`. Then run the scan suite once (`python -m pytest agent/scan/tests -q`) — the view controller integration must be unaffected.

- [ ] **Step 5: Commit**

```bash
git add agent/video/stream_demod.py agent/video/tests/test_stream_demod.py
git commit -m "feat(view): decouple demod from output pacing via writer thread"
```

---

### Task 5: `bench_stream.py --pipeline` — real-time-rate drop gate

**Files:**
- Modify: `agent/video/bench_stream.py`

**Interfaces:**
- Consumes: `ChunkMailbox`, `FrameQueue`, `writer_loop`, `FramePacer`, `chunk_to_frames`, `select_frames`, `VIEW_HEIGHT` from `stream_demod`.
- Produces: `--pipeline` CLI mode printing `dropped_chunks=<n> dropped_frames=<n> avg_fps=<x>`; gate on the Pi 5 is `dropped_chunks=0` at `--fs 6e6`.

- [ ] **Step 1: Implement (bench script — verified by running, not by pytest)**

Add to `agent/video/bench_stream.py` (new import at top: `import threading`; new flag + function; call it from `main()` when `--pipeline` is set):

```python
def bench_pipeline(fs, chunk_s, rounds, width, fps):
    """Feed synthetic chunks at the REAL-TIME rate through the restructured
    pipeline (mailbox -> demod -> queue -> paced writer) and report drops."""
    from stream_demod import (ChunkMailbox, FrameQueue, FramePacer, writer_loop,
                              chunk_to_frames, select_frames, VIEW_HEIGHT)
    img = (np.indices((64, 64)).sum(axis=0) % 2).astype(float)
    bb = make_cvbs("PAL", img, fs, frames=max(1, int(round(chunk_s * 25))))
    raw = to_int8(fm_modulate(bb, fs, 4e6), noise_std=0.05)
    n_bytes = int(fs * 2 * chunk_s)
    raw = bytes((raw * (n_bytes // len(raw) + 1))[:n_bytes])

    mailbox = ChunkMailbox()
    q = FrameQueue(maxlen=int(fps))
    stop = threading.Event()
    err = {"msg": None}
    written = [0]

    class _Enc:
        def poll(self):
            return None

    pacer = FramePacer(fps, lambda fr: written.__setitem__(0, written[0] + 1))
    writer = threading.Thread(target=writer_loop, args=(q, pacer, _Enc(), stop, err),
                              kwargs={"dropped_chunks": lambda: mailbox.dropped},
                              daemon=True)
    writer.start()

    done = threading.Event()

    def feeder():
        for _ in range(rounds):
            time.sleep(chunk_s)                     # chunks arrive at the air rate
            mailbox.put(raw)
        done.set()

    threading.Thread(target=feeder, daemon=True).start()

    t0 = time.perf_counter()
    height = VIEW_HEIGHT["PAL"]
    while not (done.is_set() and mailbox.take() is None):
        buf = mailbox.take()
        if buf is None:
            time.sleep(0.005)
            continue
        iq = iq_from_int8(buf)
        for fr in select_frames(chunk_to_frames(iq, fs, "PAL", width, height, 5e6),
                                chunk_s, fps):
            q.put(fr.tobytes())
    q.close()
    writer.join(timeout=int(fps) / fps + 2.0)
    stop.set()
    dur = time.perf_counter() - t0
    print(f"pipeline fs={fs / 1e6:.1f}MS/s rounds={rounds} width={width} fps={fps}")
    print(f"dropped_chunks={mailbox.dropped} dropped_frames={q.dropped} "
          f"avg_fps={written[0] / dur:.1f} (gate: dropped_chunks=0)")
```

And in `main()`:

```python
    ap.add_argument("--pipeline", action="store_true",
                    help="feed chunks at the real-time rate; gate: dropped_chunks=0")
    ap.add_argument("--fps", type=float, default=15.0)
    args = ap.parse_args()
    ...
    if args.pipeline:
        bench_pipeline(args.fs, args.chunk_s, args.rounds, args.width, args.fps)
        return
```

- [ ] **Step 2: Run locally as a sanity check**

Run: `python agent/video/bench_stream.py --pipeline --fs 4e6 --rounds 6 --width 320`
Expected: prints `dropped_chunks=0 ... avg_fps=~15` (a fast dev box keeps up easily). Also run `python -m pytest agent/video/tests -q` — still green (script change only).

- [ ] **Step 3: Commit**

```bash
git add agent/video/bench_stream.py
git commit -m "feat(view): --pipeline bench mode gating on zero chunk drops"
```

---

### Task 6: Deploy + live acceptance (operator/hardware)

**Files:** none (operational).

Pre-req: PR merged to `main` (superpowers:finishing-a-development-branch).

- [ ] **Step 1: Pi bench gate at production rate**

```bash
ssh andriy@192.168.1.204 \
  '/opt/fpv-video-stream/agent/scan/.venv/bin/python - --pipeline --fs 6e6 --rounds 20 --width 360' \
  < agent/video/bench_stream.py
```
Expected: `dropped_chunks=0`, `avg_fps≈15` (run while `fpv-scan` bladerf sweep is active — that's the production CPU load).

- [ ] **Step 2: Deploy**

```bash
ssh andriy@192.168.1.204 "echo '<sudo-pw>' | sudo -S git -C /opt/fpv-video-stream pull --ff-only && echo '<sudo-pw>' | sudo -S systemctl restart fpv-scan-hackrf"
```
(bladerf unit unaffected — do not restart it.)

- [ ] **Step 3: Live acceptance**

1. Start a view from the FPV Viewer panel on a real signal; watch ≥60 s.
2. `journalctl -u fpv-scan-hackrf -f` shows the stats line every ~10 s with `~15.0 fps` and `dropped_chunks=0`.
3. The in-panel player (over WG) shows continuous motion — no periodic freezes.
4. Retune to another detection and stop — semantics unchanged (fresh session, sweep resumes).

- [ ] **Step 4: Update memory** — refresh `improve-sdr-view-next.md` (sub-feature 2 shipped; 2b = DSP/25fps follow-up still open) and `multiband-view-workflow`/`sdr-view-stream` notes if behavior descriptions changed.

---

## Self-Review (done at plan-writing time)

- **Spec coverage:** demod/writer decoupling (T4), bounded drop-oldest queue (T2), chunk-drop counting (T1), stats log (T3), no-drain-on-stop vs drain-on-clean-exit (T3/T4), bench `--pipeline` gate (T5), unchanged `run_stream` contract + untouched existing integration tests (T4 Step 4), deploy+acceptance (T6). Non-goals (25 fps, DSP, latency) correctly absent. ✓
- **Type consistency:** `ChunkMailbox.put/take/dropped`, `FrameQueue.put/get/close/__len__/dropped/maxlen/closed`, `writer_loop(q, pacer, enc, stop_event, err, dropped_chunks=, clock=, log_every_s=)` used identically in T3 tests, T4 wiring, T5 bench. `_FakeProc`/`_vcfg`/`_chunk_bytes` reused from the existing test file. ✓
- **Placeholder scan:** every code step has complete code; T4 Step 2 documents the possibly-ambiguous RED outcome explicitly instead of pretending. ✓
