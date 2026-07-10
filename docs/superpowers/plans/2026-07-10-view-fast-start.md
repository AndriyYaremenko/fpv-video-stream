# View Fast Start (click → picture) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut SDR-view click→picture latency from 5–20 s to ≤3 s (session start) and ≤1.5 s (channel switch), per spec `docs/superpowers/specs/2026-07-10-view-fast-start-design.md`.

**Architecture:** Three stacked PRs. PR-A: 1-second GOP + faster WHEP retry (kills the 16.7 s IDR wait). PR-B: one ffmpeg RTSP push lives for the whole agent lifetime (placeholder frames while idle), and the dashboard player binds to the stream name only, surviving start/stop/retune. PR-C: in-process HackRF capture over cffi/libhackrf — retune becomes `hackrf_set_freq()` (milliseconds) instead of a subprocess restart.

**Tech Stack:** Python 3.12+ (numpy, cffi), pytest; Node ≥18 `node --test`; ffmpeg/libx264; MediaMTX WHEP; libhackrf.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-10-view-fast-start-design.md`. Targets: start ≤ 3 s, retune ≤ 1.5 s, no black screen during retune.
- The demod path (`chunk_to_frames`, `reconstruct_frames`, `SyncTracker`) is NOT touched — the 6 MS/s realtime budget and the sync lock stay bit-for-bit.
- The legacy engine must keep working unchanged: env `VIEW_ENGINE=legacy` restores today's per-session `hackrf_transfer` + per-session ffmpeg exactly. Default is `persistent`.
- The scan/sweep path (`dweller.py`, `sweeper.py`, `run_cycle`) is untouched.
- Stats log line must keep the fields `fps, queue=, mailbox=, dropped_frames=, dropped_chunks=` (acceptance metric; `repeats=` is appended).
- Fixed output canvas: height **288** always (`VIEW_CANVAS_HEIGHT`); NTSC 240-row fields are row-resampled up by the existing `resize_rows`.
- Branches are stacked: `feat/view-fast-start` (PR-A, exists) → `feat/view-persistent-stream` (PR-B) → `feat/view-inprocess-capture` (PR-C). Open each PR against `main`; merge/deploy in order A→B→C (rebase B/C onto main after the previous merge if needed).
- Python tests: `cd agent/video && python -m pytest tests -q` and `cd agent/scan && python -m pytest tests -q`. JS tests: `npm test` (repo root). Run the full relevant suite before every commit.
- Commit messages end with the `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` / `Claude-Session: https://claude.ai/code/session_01AoKgEkvkesR7Y1KjQWekB8` trailer (project convention).

---

# Phase A — PR-A: quick wins (branch `feat/view-fast-start`, already checked out)

### Task 1: 1-second GOP in the encode command

**Files:**
- Modify: `agent/video/stream_demod.py:29-35` (`build_encode_cmd`)
- Test: `agent/video/tests/test_stream_demod.py`

**Interfaces:**
- Consumes: existing `build_encode_cmd(push_url, width, height, fps)`.
- Produces: same signature; argv now contains `"-g", str(max(1, int(round(fps))))`. Later tasks (ViewEncoder) reuse this builder as-is.

- [ ] **Step 1: Write the failing test**

Append to `agent/video/tests/test_stream_demod.py` (next to `test_encode_cmd_rawgray_to_rtsp`):

```python
def test_encode_cmd_short_gop():
    # libx264 defaults to keyint=250 (~17 s at 15 fps): a WHEP viewer joining
    # mid-GOP waits that long for an IDR. -g fps = a keyframe every ~1 s.
    cmd = build_encode_cmd("rtsp://u:p@10.8.0.1:8554/hackrf-view", 480, 288, 15)
    assert cmd[cmd.index("-g") + 1] == "15"
    cmd = build_encode_cmd("rtsp://u:p@h:8554/s", 480, 288, 12.6)
    assert cmd[cmd.index("-g") + 1] == "13"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent/video && python -m pytest tests/test_stream_demod.py::test_encode_cmd_short_gop -v`
Expected: FAIL with `ValueError: '-g' is not in list`

- [ ] **Step 3: Write minimal implementation**

In `agent/video/stream_demod.py` replace `build_encode_cmd`:

```python
def build_encode_cmd(push_url, width, height, fps):
    """ffmpeg argv: raw gray frames on stdin -> low-latency H.264 RTSP push.
    -g fps = an IDR every ~1 s so a WHEP viewer joining mid-stream decodes
    within a second (libx264's default 250-frame GOP is ~17 s at 15 fps)."""
    return ["ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{width}x{height}",
            "-r", str(fps), "-i", "-",
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
            "-g", str(max(1, int(round(fps)))),
            "-pix_fmt", "yuv420p", "-f", "rtsp", "-rtsp_transport", "tcp", push_url]
```

- [ ] **Step 4: Run the video test suite**

Run: `cd agent/video && python -m pytest tests -q`
Expected: all PASS (existing `test_encode_cmd_rawgray_to_rtsp` still passes — it doesn't pin argv length).

- [ ] **Step 5: Commit**

```bash
git add agent/video/stream_demod.py agent/video/tests/test_stream_demod.py
git commit -m "feat(view): 1s GOP so WHEP viewers join in <=1s instead of libx264's 17s default"
```

### Task 2: faster WHEP retry backoff on the dashboard

**Files:**
- Modify: `dashboard/public/viewer.js` (add `whepRetryDelay`)
- Modify: `dashboard/public/app.js:3-11` (import) and `app.js:361-370` (`startViewerWhep`)
- Test: `test/viewer.test.js`

**Interfaces:**
- Produces: `export function whepRetryDelay(attempt: number): number` in `dashboard/public/viewer.js` — 300 ms doubling to a 1500 ms cap. Task 10 keeps using it.

- [ ] **Step 1: Write the failing test**

Append to `test/viewer.test.js`:

```js
test('whepRetryDelay backs off 300ms -> 1.5s cap', () => {
  assert.equal(whepRetryDelay(0), 300);
  assert.equal(whepRetryDelay(1), 600);
  assert.equal(whepRetryDelay(2), 1200);
  assert.equal(whepRetryDelay(3), 1500);
  assert.equal(whepRetryDelay(10), 1500);
});
```

And add `whepRetryDelay` to the import list at the top of `test/viewer.test.js` (it imports from `../dashboard/public/viewer.js`).

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test test/viewer.test.js`
Expected: FAIL — `whepRetryDelay` is not exported.

- [ ] **Step 3: Write minimal implementation**

Append to `dashboard/public/viewer.js`:

```js
// WHEP (re)connect backoff: the RTSP path (re)appears within a couple of
// seconds of a view command, so retry quickly at first, then settle to 1.5 s.
export function whepRetryDelay(attempt) {
  return Math.min(1500, 300 * 2 ** attempt);
}
```

In `dashboard/public/app.js` add `whepRetryDelay` to the existing import from `/viewer.js` (lines 8-11), and in `startViewerWhep` replace the retry line:

```js
  } catch {
    setTimeout(() => startViewerWhep(video, stream, key, attempt + 1), whepRetryDelay(attempt));
  }
```

- [ ] **Step 4: Run the JS test suite**

Run: `npm test`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/public/viewer.js dashboard/public/app.js test/viewer.test.js
git commit -m "feat(dashboard): exponential 0.3-1.5s WHEP retry for faster view join"
```

### Task 3: open PR-A

- [ ] **Step 1: Full test sweep**

Run: `cd agent/video && python -m pytest tests -q && cd ../scan && python -m pytest tests -q && cd ../.. && npm test`
Expected: all PASS.

- [ ] **Step 2: Push and open the PR**

```bash
git push -u origin feat/view-fast-start
gh pr create --base main --title "feat(view): fast WHEP join — 1s GOP + quick retry (PR-A)" --body "Part A of docs/superpowers/specs/2026-07-10-view-fast-start-design.md: IDR every second + 0.3s-first WHEP retry. Cuts click->picture from 5-20s (IDR lottery) to ~3-5s on the current architecture. Deploy: Pi agent restart + dashboard restart.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01AoKgEkvkesR7Y1KjQWekB8"
```

---

# Phase B — PR-B: persistent stream (branch `feat/view-persistent-stream` off `feat/view-fast-start`)

- [ ] **Phase entry: create the branch**

```bash
git checkout -b feat/view-persistent-stream feat/view-fast-start
```

### Task 4: `view_engine` config knob

**Files:**
- Modify: `agent/video/vconfig.py`
- Test: `agent/video/tests/test_vconfig.py`

**Interfaces:**
- Produces: `VideoConfig.view_engine: str = "persistent"`, env override `VIEW_ENGINE` (lowercased). Tasks 9/15 switch on it; `legacy` = pre-change behavior.

- [ ] **Step 1: Write the failing test**

Append to `agent/video/tests/test_vconfig.py`:

```python
def test_view_engine_default_and_env():
    from vconfig import load_video_config
    assert load_video_config({}).view_engine == "persistent"
    assert load_video_config({"VIEW_ENGINE": "Legacy"}).view_engine == "legacy"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent/video && python -m pytest tests/test_vconfig.py::test_view_engine_default_and_env -v`
Expected: FAIL — `VideoConfig` has no attribute `view_engine`.

- [ ] **Step 3: Implement**

In `agent/video/vconfig.py` add to the dataclass (after `view_standard`):

```python
    view_engine: str = "persistent"      # persistent (agent-lifetime ffmpeg) | legacy (per-session)
```

and to `load_video_config` (after the `VIEW_STANDARD` block):

```python
    if "VIEW_ENGINE" in env:
        c.view_engine = env["VIEW_ENGINE"].strip().lower()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd agent/video && python -m pytest tests/test_vconfig.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/video/vconfig.py agent/video/tests/test_vconfig.py
git commit -m "feat(view): VIEW_ENGINE knob (persistent default, legacy rollback)"
```

### Task 5: `ViewEncoder` writer core (freeze / black / live) + `FrameQueue.clear`

**Files:**
- Create: `agent/video/view_encoder.py`
- Modify: `agent/video/stream_demod.py` (add `VIEW_CANVAS_HEIGHT = 288` next to `VIEW_HEIGHT`; add `FrameQueue.clear()`)
- Test: `agent/video/tests/test_view_encoder.py` (new), `agent/video/tests/test_stream_demod.py` (queue clear)

**Interfaces:**
- Consumes: `FrameQueue`, `FramePacer`, `build_encode_cmd` from `stream_demod`.
- Produces (used by Tasks 6/7/9/14):
  - `stream_demod.VIEW_CANVAS_HEIGHT = 288`
  - `FrameQueue.clear()` — drop all queued frames.
  - `class ViewEncoder(vcfg, popen=None, clock=None, sleep=None, log_every_s=10.0)` with `submit(frame_bytes)`, `idle()`, `set_session_stats(fn_or_None)` (fn returns `{"mailbox": int, "dropped_chunks": int, "sync": dict|None}`), attribute `repeats` (int), and internal `_run_writer(enc)` (returns when encoder dies or `_stop` is set).

- [ ] **Step 1: Write the failing tests**

Add to `agent/video/tests/test_stream_demod.py`:

```python
def test_frame_queue_clear_drops_pending():
    q = FrameQueue(maxlen=4)
    q.put(b"a"); q.put(b"b")
    q.clear()
    assert len(q) == 0 and q.get(timeout=0.01) is None
```

(`FrameQueue` is already imported in that file via `stream_demod` imports — add it to the import list if missing.)

Create `agent/video/tests/test_view_encoder.py`:

```python
import threading

from vconfig import VideoConfig
from stream_demod import VIEW_CANVAS_HEIGHT
from view_encoder import ViewEncoder


def _vcfg(width=4, fps=50.0):
    c = VideoConfig()
    c.view_push_url = "rtsp://u:p@10.8.0.1:8554/hackrf-view"
    c.view_width = width
    c.view_fps = fps
    return c


class _Clock:
    """Fake monotonic clock: sleep() advances it, so the pacer never really waits."""
    def __init__(self):
        self.t = 0.0
    def __call__(self):
        return self.t
    def sleep(self, s):
        self.t += max(s, 0.0)


class _FakeEnc:
    """Stands in for the ffmpeg Popen: records stdin writes, optional stop hook."""
    def __init__(self, on_write=None, rc=None):
        self.writes = []
        self._on_write = on_write
        self.rc = rc
        self.killed = False
        outer = self
        class _Stdin:
            def write(self, b):
                outer.writes.append(bytes(b))
                if outer._on_write:
                    outer._on_write(len(outer.writes))
        self.stdin = _Stdin()
    def poll(self):
        return self.rc
    def kill(self):
        self.killed = True
    def wait(self, timeout=None):
        return 0


def test_writer_emits_black_while_idle():
    clk = _Clock()
    ve = ViewEncoder(_vcfg(), clock=clk, sleep=clk.sleep)
    enc = _FakeEnc(on_write=lambda n: n >= 3 and ve._stop.set())
    ve._run_writer(enc)
    black = bytes(4 * VIEW_CANVAS_HEIGHT)
    assert enc.writes == [black, black, black]
    assert ve.repeats == 3


def test_writer_plays_live_frames_then_freezes_last():
    clk = _Clock()
    ve = ViewEncoder(_vcfg(), clock=clk, sleep=clk.sleep)
    a = b"A" * (4 * VIEW_CANVAS_HEIGHT)
    b = b"B" * (4 * VIEW_CANVAS_HEIGHT)
    ve.submit(a); ve.submit(b)
    enc = _FakeEnc(on_write=lambda n: n >= 4 and ve._stop.set())
    ve._run_writer(enc)
    assert enc.writes == [a, b, b, b]          # live, live, freeze, freeze
    assert ve.repeats == 2


def test_idle_clears_freeze_back_to_black_and_drains_queue():
    clk = _Clock()
    ve = ViewEncoder(_vcfg(), clock=clk, sleep=clk.sleep)
    a = b"A" * (4 * VIEW_CANVAS_HEIGHT)
    ve.submit(a)

    def hook(n):
        if n == 1:
            ve.submit(b"S" * (4 * VIEW_CANVAS_HEIGHT))   # stale frame from the ended session
            ve.idle()                                     # session over: drop freeze + queue
        if n >= 3:
            ve._stop.set()
    enc = _FakeEnc(on_write=hook)
    ve._run_writer(enc)
    black = bytes(4 * VIEW_CANVAS_HEIGHT)
    assert enc.writes == [a, black, black]


def test_writer_returns_when_encoder_dies():
    clk = _Clock()
    ve = ViewEncoder(_vcfg(), clock=clk, sleep=clk.sleep)
    enc = _FakeEnc(rc=1)                                  # dead from the start
    ve._run_writer(enc)                                   # must return, not hang
    assert enc.writes == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent/video && python -m pytest tests/test_view_encoder.py tests/test_stream_demod.py::test_frame_queue_clear_drops_pending -v`
Expected: FAIL — `No module named 'view_encoder'`, `FrameQueue has no attribute 'clear'`.

- [ ] **Step 3: Implement**

In `agent/video/stream_demod.py` add after the `VIEW_HEIGHT` line:

```python
VIEW_CANVAS_HEIGHT = 288   # fixed encoder canvas (PAL field height); NTSC is row-resampled up
```

Add to `FrameQueue`:

```python
    def clear(self):
        with self._cond:
            self._d.clear()
```

Create `agent/video/view_encoder.py`:

```python
"""Persistent view encoder: ONE ffmpeg RTSP push that outlives view sessions.

The writer paces frames at a fixed fps forever: live frames while a session
feeds it, a freeze of the last live frame through retunes/short stalls, black
after idle() (session over / sweeping). The rawvideo timeline never stops, so
the MediaMTX path — and the dashboard's WHEP session — survive everything.
If ffmpeg dies it is respawned with backoff."""
import logging
import subprocess
import threading
import time

from stream_demod import FrameQueue, FramePacer, build_encode_cmd, VIEW_CANVAS_HEIGHT

LOG = logging.getLogger("video.viewenc")


class ViewEncoder:
    def __init__(self, vcfg, popen=None, clock=None, sleep=None, log_every_s=10.0):
        self._vcfg = vcfg
        self._popen = popen or subprocess.Popen
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._log_every_s = log_every_s
        self._black = bytes(vcfg.view_width * VIEW_CANVAS_HEIGHT)
        self._q = FrameQueue(maxlen=max(1, int(vcfg.view_fps)))
        self._last = None                # freeze source; None -> black
        self._stats = None               # session stats fn or None
        self._stop = threading.Event()
        self._thread = None
        self.repeats = 0                 # placeholder/freeze writes (stats line)

    def submit(self, frame_bytes):
        """Demod loop hands one canvas-sized gray frame to the writer."""
        self._q.put(frame_bytes)

    def idle(self):
        """Session over: drop the freeze frame and stale queue -> black."""
        self._last = None
        self._stats = None
        self._q.clear()

    def set_session_stats(self, fn):
        """fn() -> {'mailbox': int, 'dropped_chunks': int, 'sync': dict|None}."""
        self._stats = fn

    def start(self):
        self._thread = threading.Thread(target=self._supervise, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def _supervise(self):
        backoff = 1.0
        while not self._stop.is_set():
            enc = None
            try:
                enc = self._popen(
                    build_encode_cmd(self._vcfg.view_push_url, self._vcfg.view_width,
                                     VIEW_CANVAS_HEIGHT, self._vcfg.view_fps),
                    stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
            except Exception:
                LOG.exception("view encoder spawn failed")
            if enc is not None:
                t0 = self._clock()
                self._run_writer(enc)
                try:
                    enc.kill()
                    enc.wait(timeout=5)
                except Exception:
                    pass
                if self._clock() - t0 > 60.0:
                    backoff = 1.0            # a long healthy run earns a fresh backoff
            if self._stop.is_set():
                return
            LOG.warning("view encoder died; respawn in %.0fs", backoff)
            self._sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    def _run_writer(self, enc):
        period = 1.0 / self._vcfg.view_fps
        pacer = FramePacer(self._vcfg.view_fps, enc.stdin.write,
                           clock=self._clock, sleep=self._sleep)
        written = 0
        last_log = self._clock()
        last_written = 0
        while not self._stop.is_set():
            fr = self._q.get(timeout=period)
            if fr is None:
                if enc.poll() is not None:
                    return                   # encoder died -> supervisor respawns
                fr = self._last if self._last is not None else self._black
                self.repeats += 1
            else:
                self._last = fr
            try:
                pacer.tick(fr)
                written += 1
            except (BrokenPipeError, OSError):
                return
            now = self._clock()
            if now - last_log >= self._log_every_s:
                st = self._stats() if self._stats is not None else None
                sync = ""
                if st and st.get("sync"):
                    s = st["sync"]
                    vrow = s["vsync_row"] if s["vsync_row"] is not None else "-"
                    sync = (" sync=H%.2f V%s line=%.0fHz"
                            % (s["line_hz"] / s["nominal"] - 1.0, vrow, s["line_hz"]))
                LOG.info("view stream: %.1f fps, queue=%d, mailbox=%d, dropped_frames=%d, "
                         "dropped_chunks=%d, repeats=%d%s",
                         (written - last_written) / max(now - last_log, 1e-9), len(self._q),
                         st["mailbox"] if st else 0, self._q.dropped,
                         st["dropped_chunks"] if st else 0, self.repeats, sync)
                last_log = now
                last_written = written
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent/video && python -m pytest tests/test_view_encoder.py tests/test_stream_demod.py -q`
Expected: PASS. (The empty-queue `get(timeout=period)` waits real wall-time — the tests above hit it at most a few times at 20 ms; total runtime stays well under a second.)

- [ ] **Step 5: Commit**

```bash
git add agent/video/view_encoder.py agent/video/stream_demod.py agent/video/tests/test_view_encoder.py agent/video/tests/test_stream_demod.py
git commit -m "feat(view): ViewEncoder writer core - live/freeze/black over a fixed 288 canvas"
```

### Task 6: `ViewEncoder` supervisor (spawn + respawn with backoff)

**Files:**
- Modify: `agent/video/view_encoder.py` (already contains `_supervise` from Task 5 — this task TESTS it; adjust only if tests expose bugs)
- Test: `agent/video/tests/test_view_encoder.py`

**Interfaces:**
- Produces: `ViewEncoder.start()` / `stop()` verified behavior: ffmpeg argv built from vcfg (contains `-g` and `{width}x288`), dead encoder → respawn after backoff 1→2→4…30 s, `stop()` terminates the loop and kills the child.

- [ ] **Step 1: Write the failing tests**

Append to `agent/video/tests/test_view_encoder.py`:

```python
def test_supervisor_respawns_dead_encoder_with_backoff():
    clk = _Clock()
    slept = []
    spawned = []
    ve = ViewEncoder(_vcfg(), clock=clk,
                     sleep=lambda s: (slept.append(s), clk.sleep(s)))

    def popen(cmd, **kw):
        assert cmd[0] == "ffmpeg" and "4x288" in cmd and "-g" in cmd
        enc = _FakeEnc(rc=1)                       # dies instantly
        spawned.append(enc)
        if len(spawned) == 3:
            ve._stop.set()
        return enc
    ve._popen = popen
    ve._supervise()                                # run inline: deterministic
    assert len(spawned) == 3
    assert all(e.killed for e in spawned)
    assert slept[:2] == [1.0, 2.0]                 # backoff between respawns


def test_supervisor_stop_kills_child_and_exits():
    clk = _Clock()
    ve = ViewEncoder(_vcfg(), clock=clk, sleep=clk.sleep)
    enc = _FakeEnc(on_write=lambda n: ve._stop.set())   # first placeholder write -> stop
    ve._popen = lambda cmd, **kw: enc
    ve._supervise()
    assert enc.killed
```

- [ ] **Step 2: Run tests**

Run: `cd agent/video && python -m pytest tests/test_view_encoder.py -v`
Expected: both new tests PASS if Task 5's `_supervise` is correct; if either FAILS, fix `_supervise` (not the test) until green. Common trap: `_stop` set during `_sleep(backoff)` must exit the loop — the `while not self._stop.is_set()` check covers it.

- [ ] **Step 3: Commit**

```bash
git add agent/video/tests/test_view_encoder.py agent/video/view_encoder.py
git commit -m "test(view): ViewEncoder supervisor respawn/backoff/stop coverage"
```

### Task 7: `run_stream_persistent` — session demod feeding the shared encoder

**Files:**
- Modify: `agent/video/stream_demod.py` (new function, after `run_stream`)
- Test: `agent/video/tests/test_stream_demod.py`

**Interfaces:**
- Consumes: `ViewEncoder.submit/set_session_stats` (Task 5), `VIEW_CANVAS_HEIGHT`.
- Produces: `run_stream_persistent(vcfg, freq_mhz, stop_event, max_s, encoder, lna=40, vga=20, amp=0, popen=None, clock=None, sleep=None) -> str|None` — same error-string contract as `run_stream`. Task 9 wires it into `ViewController`.

- [ ] **Step 1: Write the failing tests**

Append to `agent/video/tests/test_stream_demod.py`:

```python
from stream_demod import run_stream_persistent, VIEW_CANVAS_HEIGHT


class _FakeEncoder:
    def __init__(self):
        self.frames = []
        self.stats_fn = None
    def submit(self, fr):
        self.frames.append(fr)
    def set_session_stats(self, fn):
        self.stats_fn = fn


def test_run_stream_persistent_submits_canvas_frames_and_spawns_no_ffmpeg():
    fs = 4e6
    cmds = []

    def popen(cmd, **kw):
        cmds.append(cmd[0])
        return _FakeProc(stdout=io.BytesIO(_chunk_bytes(fs) * 2))

    fenc = _FakeEncoder()
    t = [0.0]
    err = run_stream_persistent(_vcfg(), 947.0, threading.Event(), max_s=60.0,
                                encoder=fenc, popen=popen, clock=lambda: t[0],
                                sleep=lambda s: t.__setitem__(0, t[0] + max(s, 0.01)))
    assert err == "hackrf_transfer exited"           # finite stdout EOF, same as legacy
    assert cmds == ["hackrf_transfer"]               # NO ffmpeg spawn: encoder is shared
    assert fenc.frames and all(len(f) == 320 * VIEW_CANVAS_HEIGHT for f in fenc.frames)
    st = fenc.stats_fn()                             # session stats got wired
    assert set(st) == {"mailbox", "dropped_chunks", "sync"}


def test_run_stream_persistent_stops_cleanly_on_stop_event():
    stop = threading.Event()
    stop.set()
    err = run_stream_persistent(_vcfg(), 947.0, stop, max_s=60.0, encoder=_FakeEncoder(),
                                popen=lambda cmd, **kw: _FakeProc(stdout=io.BytesIO(b"")),
                                clock=lambda: 0.0, sleep=lambda s: None)
    assert err is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent/video && python -m pytest tests/test_stream_demod.py -k persistent -v`
Expected: FAIL — `cannot import name 'run_stream_persistent'`.

- [ ] **Step 3: Implement**

Append to `agent/video/stream_demod.py` (after `run_stream`):

```python
def run_stream_persistent(vcfg, freq_mhz, stop_event, max_s, encoder,
                          lna=40, vga=20, amp=0, popen=None, clock=None, sleep=None):
    """Session capture->demod loop for the persistent-encoder engine.

    Spawns ONLY hackrf_transfer; frames go to the long-lived ViewEncoder, which
    owns ffmpeg/pacing/stats and survives the session (freeze during retunes,
    black after idle). Same error-string contract as run_stream."""
    popen = popen or subprocess.Popen
    clock = clock or time.monotonic
    sleep = sleep or time.sleep
    fs = vcfg.view_sample_rate_hz
    chunk_bytes = int(fs * 2 * CHUNK_S)
    cap = popen(build_capture_cmd(freq_mhz * 1e6, fs, lna, vga, amp),
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=chunk_bytes)
    standard = None
    tracker = None
    error = None
    mailbox = ChunkMailbox()

    def _reader():
        while not stop_event.is_set():
            try:
                buf = cap.stdout.read(chunk_bytes)
            except Exception:
                return
            if not buf or len(buf) < chunk_bytes:
                return                               # EOF: capture died
            mailbox.put(buf)

    threading.Thread(target=_reader, daemon=True).start()
    t_end = clock() + max_s
    frame_budget = max(1, int(round(CHUNK_S * vcfg.view_fps)))
    try:
        while not stop_event.is_set() and clock() < t_end:
            buf = mailbox.take()
            if buf is None:
                if cap.poll() is not None:
                    error = "hackrf_transfer exited"
                    break
                sleep(0.05)
                continue
            iq = iq_from_int8_fast(buf)
            if standard is None:
                bb = lowpass(fm_demod(iq), fs, vcfg.lpf_cutoff_hz)
                standard = pick_standard(bb, fs, vcfg.view_standard,
                                         vcfg.line_snr_db, vcfg.harm_snr_db)
                tracker = SyncTracker(standard)
                tracker.seed(bb, fs)
                trk, mbx = tracker, mailbox
                encoder.set_session_stats(lambda: {
                    "mailbox": len(mbx), "dropped_chunks": mbx.dropped,
                    "sync": trk.status()})
                LOG.info("view stream: %s -> %dx%d canvas @%.0ffps", standard,
                         vcfg.view_width, VIEW_CANVAS_HEIGHT, vcfg.view_fps)
            for fr in select_frames(
                    chunk_to_frames(iq, fs, standard, vcfg.view_width, VIEW_CANVAS_HEIGHT,
                                    vcfg.lpf_cutoff_hz, vcfg.blank_frac,
                                    budget=frame_budget, tracker=tracker),
                    CHUNK_S, vcfg.view_fps):
                encoder.submit(fr.tobytes())
    finally:
        try:
            cap.kill()          # stop the reader FIRST: teardown must not inflate dropped_chunks
            cap.wait(timeout=5)
        except Exception:
            pass
    return error
```

- [ ] **Step 4: Run the video suite**

Run: `cd agent/video && python -m pytest tests -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/video/stream_demod.py agent/video/tests/test_stream_demod.py
git commit -m "feat(view): run_stream_persistent - session demod over the shared encoder"
```

### Task 8: `ViewController.on_idle` hook

**Files:**
- Modify: `agent/scan/view_controller.py:23-33` (constructor) and `:93-99` (finally block)
- Test: `agent/scan/tests/test_view_controller.py`

**Interfaces:**
- Produces: `ViewController(..., on_idle=None)`; `on_idle()` is called exactly once per session, in `run_view`'s `finally`, BEFORE `reset` (so the encoder blanks before the device reset can delay things). Never raises.

- [ ] **Step 1: Write the failing test**

Append to `agent/scan/tests/test_view_controller.py`:

```python
def test_run_view_calls_on_idle_once_at_session_end():
    order = []
    vc = ViewController(_Pub(), lambda f, s, m: None, max_s=60.0,
                        reset=lambda: order.append("reset"),
                        on_idle=lambda: order.append("idle"))
    vc.run_view(5000.0)
    assert order == ["idle", "reset"]


def test_on_idle_failure_does_not_break_session_end():
    def boom():
        raise RuntimeError("boom")
    vc = ViewController(_Pub(), lambda f, s, m: None, max_s=60.0,
                        reset=lambda: None, on_idle=boom)
    assert vc.run_view(5000.0) is None             # error swallowed, session ends clean
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent/scan && python -m pytest tests/test_view_controller.py -k on_idle -v`
Expected: FAIL — unexpected keyword argument `on_idle`.

- [ ] **Step 3: Implement**

In `agent/scan/view_controller.py`, constructor:

```python
    def __init__(self, publisher, run_stream, max_s=600.0, reset=None, clock=None,
                 stream=None, on_idle=None):
```

store it (next to `self._reset`):

```python
        self._on_idle = on_idle or (lambda: None)
```

and in `run_view`'s `finally`, insert BEFORE the `self._pub(...)` line:

```python
        finally:
            try:
                self._on_idle()          # persistent engine: blank the stream to black
            except Exception:
                LOG.exception("view: on_idle failed")
```

(keep the existing `_pub` + `_reset` lines after it, unchanged).

- [ ] **Step 4: Run the scan suite**

Run: `cd agent/scan && python -m pytest tests -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/scan/view_controller.py agent/scan/tests/test_view_controller.py
git commit -m "feat(view): on_idle session-end hook for the persistent encoder"
```

### Task 9: engine wiring in `main.py`

**Files:**
- Modify: `agent/scan/main.py:258-281` (view init block)

**Interfaces:**
- Consumes: `ViewEncoder` (Task 5), `run_stream_persistent` (Task 7), `on_idle` (Task 8), `view_engine` (Task 4).
- Produces: agent starts the persistent encoder at boot when `view_engine == "persistent"`; `VIEW_ENGINE=legacy` keeps the exact old wiring.

- [ ] **Step 1: Implement (glue code — covered by the suites + live acceptance, no new unit test)**

Replace the body of the `if viewcfg.view_enabled and viewcfg.view_push_url:` block in `agent/scan/main.py` with:

```python
            if viewcfg.view_enabled and viewcfg.view_push_url:
                import stream_demod
                from view_controller import ViewController, stream_name_from_push_url
                encoder = None
                if viewcfg.view_engine == "persistent":
                    from view_encoder import ViewEncoder
                    encoder = ViewEncoder(viewcfg)
                    encoder.start()          # RTSP path (black placeholder) is up from boot
                    run = lambda freq, stop, max_s: stream_demod.run_stream_persistent(
                        viewcfg, freq, stop, max_s, encoder,
                        lna=cfg.lna_gain, vga=cfg.vga_gain, amp=cfg.amp_enable)
                else:
                    run = lambda freq, stop, max_s: stream_demod.run_stream(
                        viewcfg, freq, stop, max_s,
                        lna=cfg.lna_gain, vga=cfg.vga_gain, amp=cfg.amp_enable)
                view = ViewController(
                    publisher,
                    run_stream=run,
                    max_s=viewcfg.view_max_s,
                    reset=reset_hackrf,
                    stream=stream_name_from_push_url(viewcfg.view_push_url),
                    on_idle=encoder.idle if encoder is not None else None,
                )
                publisher.on_view_command = view.set_command
                publisher.on_connected = view.announce   # retained announce on every (re)connect
                LOG.info("SDR view mode enabled (engine=%s push=%s max=%.0fs)",
                         viewcfg.view_engine, viewcfg.view_push_url.split("@")[-1],
                         viewcfg.view_max_s)
```

- [ ] **Step 2: Sanity-run both suites (imports, no syntax errors)**

Run: `cd agent/scan && python -m pytest tests -q && cd ../video && python -m pytest tests -q`
Expected: all PASS.

- [ ] **Step 3: Commit**

```bash
git add agent/scan/main.py
git commit -m "feat(view): wire persistent encoder engine (VIEW_ENGINE) into the agent"
```

### Task 10: dashboard — player survives start/stop/retune

**Files:**
- Modify: `dashboard/public/viewer.js:108-112` (`playerKey`)
- Modify: `dashboard/public/whep.js` (add `onDead` hook)
- Modify: `dashboard/public/app.js:348-370` (`syncViewerPlayer` / `startViewerWhep`)
- Test: `test/viewer.test.js`

**Interfaces:**
- Produces: `playerKey(view, stream)` → `view ? stream : ''` (stable across retune/restart; non-empty whenever the retained view state exists — the persistent path streams a placeholder even when inactive). `startWhep(video, whepUrl, user, pass, onDead)` — `onDead()` fires once on `connectionState` `failed`/`closed`. Grid players (4-arg callers) are unaffected.

- [ ] **Step 1: Update the playerKey test (write failing test)**

In `test/viewer.test.js`, REPLACE the whole existing test `'playerKey changes on session restart and is empty when inactive'` with:

```js
test('playerKey is stable across retune/restart, empty without view state', () => {
  const a = playerKey({ active: true, freq_mhz: 5865, until_ts: 1000 }, 'hackrf-view');
  const b = playerKey({ active: true, freq_mhz: 5905, until_ts: 1600 }, 'hackrf-view');
  assert.equal(a, 'hackrf-view');
  assert.equal(a, b);                       // retune must NOT tear the player down
  // idle agent still pushes the placeholder stream -> keep the player connected
  assert.equal(playerKey({ active: false, stream: 'hackrf-view' }, 'hackrf-view'), 'hackrf-view');
  assert.equal(playerKey(null, 'hackrf-view'), '');       // no view capability -> no player
  assert.equal(playerKey(undefined, 'hackrf-view'), '');
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test test/viewer.test.js`
Expected: FAIL (old implementation returns `''` for inactive and embeds freq/until_ts).

- [ ] **Step 3: Implement viewer.js**

Replace `playerKey` in `dashboard/public/viewer.js`:

```js
// Player identity: the persistent engine keeps ONE MediaMTX path alive across
// start/stop/retune (placeholder while idle), so the player binds to the
// stream name only. It changes exactly when the panel switches scanners.
export function playerKey(view, stream) {
  return view ? stream : '';
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `node --test test/viewer.test.js`
Expected: PASS.

- [ ] **Step 5: Implement whep.js onDead + app.js re-kick**

`dashboard/public/whep.js` — replace `startWhep` (keep `iceGatheringComplete` as is):

```js
// dashboard/public/whep.js — minimal non-trickle WHEP reader.
export async function startWhep(video, whepUrl, user, pass, onDead) {
  const pc = new RTCPeerConnection({ iceServers: [] });
  pc.addTransceiver('video', { direction: 'recvonly' });
  pc.addTransceiver('audio', { direction: 'recvonly' });
  const stream = new MediaStream();
  pc.ontrack = (e) => { stream.addTrack(e.track); video.srcObject = stream; };
  if (onDead) {
    let fired = false;
    pc.onconnectionstatechange = () => {
      if (!fired && (pc.connectionState === 'failed' || pc.connectionState === 'closed')) {
        fired = true;
        onDead();
      }
    };
  }

  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);
  await iceGatheringComplete(pc, 2000);

  const res = await fetch(whepUrl, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/sdp',
      Authorization: 'Basic ' + btoa(`${user}:${pass}`),
    },
    body: pc.localDescription.sdp,
  });
  if (!res.ok) { pc.close(); throw new Error(`WHEP ${res.status}`); }
  const answer = await res.text();
  await pc.setRemoteDescription({ type: 'answer', sdp: answer });
  // Null only if we still own the element — a stale close must not blank a newer stream.
  return { close: () => { pc.close(); if (video.srcObject === stream) video.srcObject = null; } };
}
```

`dashboard/public/app.js` — add retry state next to `viewerStreamKey` (line ~25):

```js
let viewerRetry = { timer: null, inflight: false };
```

Replace `syncViewerPlayer` and `startViewerWhep`:

```js
// Keep the in-panel WHEP player in sync. The persistent engine keeps one path
// alive across start/stop/retune, so the key is the stream name: connect once
// when the panel appears, re-kick only if the connection actually died.
function syncViewerPlayer(store, viewerId, view) {
  const video = document.getElementById('viewer-video');
  const want = playerKey(view, viewerId ? viewStream(store, viewerId) : '');
  if (want !== viewerStreamKey) {
    if (viewerPlayer && viewerPlayer.player) viewerPlayer.player.close();
    viewerPlayer = null;
    viewerStreamKey = want;
    if (viewerRetry.timer) clearTimeout(viewerRetry.timer);
    viewerRetry = { timer: null, inflight: false };   // new generation: stale chains go inert
    if (!want) { video.srcObject = null; return; }
    startViewerWhep(video, viewStream(store, viewerId), viewerRetry, 0);
    return;
  }
  // Same key, but the player died (encoder respawn, server restart) and the
  // retries gave up — any view-state tick re-kicks the connection.
  if (want && !viewerPlayer && !viewerRetry.timer && !viewerRetry.inflight) {
    startViewerWhep(video, viewStream(store, viewerId), viewerRetry, 0);
  }
}

// `retry` is this attempt-chain's generation token: minted by syncViewerPlayer,
// mutated ONLY by its own chain, dead the moment a new generation replaces it.
async function startViewerWhep(video, stream, retry, attempt) {
  if (viewerRetry !== retry || attempt > 40) return;
  retry.inflight = true;
  try {
    const p = await startWhep(video, `${cfg.webrtcBase}/${stream}/whep`, cfg.readUser, cfg.readPass,
      () => { if (viewerPlayer && viewerPlayer.player === p) { p.close(); viewerPlayer = null; } });
    retry.inflight = false;
    if (viewerRetry !== retry || viewerPlayer) { p.close(); return; }  // superseded or a sibling won
    viewerPlayer = { player: p };
  } catch {
    retry.inflight = false;
    if (viewerRetry !== retry) return;                 // superseded: stay inert
    retry.timer = setTimeout(() => {
      retry.timer = null;
      startViewerWhep(video, stream, retry, attempt + 1);
    }, whepRetryDelay(attempt));
  }
}
```

Note: hardened during execution (da882dc) — the retry object is a per-generation token passed into startViewerWhep, continuations never touch the module-level viewerRetry.

- [ ] **Step 6: Run the full JS suite**

Run: `npm test`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add dashboard/public/viewer.js dashboard/public/whep.js dashboard/public/app.js test/viewer.test.js
git commit -m "feat(dashboard): stream-stable player key + dead-connection re-kick"
```

### Task 11: open PR-B

- [ ] **Step 1: Full test sweep**

Run: `cd agent/video && python -m pytest tests -q && cd ../scan && python -m pytest tests -q && cd ../.. && npm test`
Expected: all PASS.

- [ ] **Step 2: Push and open the PR**

```bash
git push -u origin feat/view-persistent-stream
gh pr create --base main --title "feat(view): persistent stream - agent-lifetime encoder, stable player (PR-B)" --body "Part B of docs/superpowers/specs/2026-07-10-view-fast-start-design.md: one ffmpeg RTSP push for the agent lifetime (black placeholder while idle, freeze through retunes, fixed 288 canvas), dashboard player keyed by stream name only. Rollback: VIEW_ENGINE=legacy. Retune still restarts hackrf_transfer (~2-3s) — PR-C removes that. Stacked on feat/view-fast-start; merge after PR-A.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01AoKgEkvkesR7Y1KjQWekB8"
```

---

# Phase C — PR-C: in-process capture (branch `feat/view-inprocess-capture` off `feat/view-persistent-stream`)

- [ ] **Phase entry: create the branch**

```bash
git checkout -b feat/view-inprocess-capture feat/view-persistent-stream
```

### Task 12: `IqRing` — bounded byte FIFO between the rx callback and the demod

**Files:**
- Create: `agent/scan/hackrf_source.py` (module docstring + `IqRing`)
- Test: `agent/scan/tests/test_hackrf_source.py` (new)

**Interfaces:**
- Produces: `IqRing(capacity_bytes)` with `write(buf)` (drop-OLDEST buffers over capacity, counted in `dropped_bytes`), `read(n, timeout_s) -> bytes|None` (exactly n bytes in arrival order, None on timeout), `clear()`, `pending() -> int`. Thread-safe; `write` is called from the libhackrf USB thread.

- [ ] **Step 1: Write the failing tests**

Create `agent/scan/tests/test_hackrf_source.py`:

```python
from hackrf_source import IqRing


def test_ring_reads_exactly_n_in_arrival_order():
    r = IqRing(100)
    r.write(b"ab")
    r.write(b"cd")
    assert r.read(3, timeout_s=0.1) == b"abc"       # splits a buffer, keeps the tail
    assert r.read(1, timeout_s=0.1) == b"d"
    assert r.pending() == 0


def test_ring_times_out_on_underrun():
    r = IqRing(100)
    r.write(b"ab")
    assert r.read(3, timeout_s=0.05) is None        # watchdog signal
    assert r.read(2, timeout_s=0.1) == b"ab"        # data not lost by the timeout


def test_ring_overflow_drops_oldest_and_counts():
    r = IqRing(4)
    r.write(b"ab")
    r.write(b"cd")
    r.write(b"ef")                                   # cap 4 -> "ab" dropped
    assert r.dropped_bytes == 2
    assert r.read(4, timeout_s=0.1) == b"cdef"


def test_ring_clear_flushes_pending():
    r = IqRing(100)
    r.write(b"abcd")
    r.clear()
    assert r.pending() == 0
    assert r.read(1, timeout_s=0.05) is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd agent/scan && python -m pytest tests/test_hackrf_source.py -v`
Expected: FAIL — `No module named 'hackrf_source'`.

- [ ] **Step 3: Implement**

Create `agent/scan/hackrf_source.py`:

```python
"""In-process HackRF capture for the SDR live view.

libhackrf streams rx into a bounded ring; retune is hackrf_set_freq (ms) plus
a ring flush — no subprocess restart. Mirrors bladerf_source.py: everything
above the radio is injected/testable without hardware; LibHackrfRadio (added
below IqRing) is the only cffi-touching code."""
import logging
import threading
import time
from collections import deque

LOG = logging.getLogger("scan.hackrf")


class IqRing:
    """Bounded byte FIFO between the libhackrf rx callback (USB thread) and
    read(). Overflow drops the OLDEST buffers — air lost, counted, surfaced
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

- [ ] **Step 4: Run to verify they pass**

Run: `cd agent/scan && python -m pytest tests/test_hackrf_source.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/scan/hackrf_source.py agent/scan/tests/test_hackrf_source.py
git commit -m "feat(view): IqRing - bounded drop-oldest FIFO for in-process HackRF rx"
```

### Task 13: `HackrfSource` + `LibHackrfRadio` (cffi)

**Files:**
- Modify: `agent/scan/hackrf_source.py`
- Modify: `agent/scan/requirements.txt` (add `cffi>=1.16`)
- Test: `agent/scan/tests/test_hackrf_source.py`

**Interfaces:**
- Produces (consumed by Task 14/15):
  - `HackrfSource(open_radio, sample_rate_hz, ring_s=2.0)` — `tune(freq_hz)` (lazy open + `set_freq` + ring flush), `read_chunk(n_bytes, timeout_s) -> bytes|None`, `recover()` (close+reopen+retune), `close()` (idempotent), `pending_bytes() -> int`, `dropped_bytes` property.
  - `open_hackrf_radio(lna_db, vga_db, amp) -> LibHackrfRadio` — the ONLY cffi/libhackrf-touching function. Radio duck-type: `start_rx(sink, sample_rate_hz)`, `set_freq(hz)`, `close()`.

- [ ] **Step 1: Write the failing tests**

Append to `agent/scan/tests/test_hackrf_source.py`:

```python
from hackrf_source import HackrfSource


class _FakeRadio:
    def __init__(self):
        self.freqs = []
        self.sink = None
        self.fs = None
        self.closed = False

    def start_rx(self, sink, sample_rate_hz):
        self.sink = sink
        self.fs = sample_rate_hz

    def set_freq(self, hz):
        self.freqs.append(hz)

    def close(self):
        self.closed = True


def _source(radios):
    def factory():
        r = _FakeRadio()
        radios.append(r)
        return r
    return HackrfSource(factory, 1e6)


def test_tune_opens_lazily_and_flushes_the_transient():
    radios = []
    s = _source(radios)
    s.tune(5865e6)
    assert len(radios) == 1 and radios[0].fs == 1e6
    assert radios[0].freqs == [5865000000]
    radios[0].sink(b"stale-air")                    # pre-retune samples
    s.tune(5905e6)                                  # live retune: same open radio
    assert len(radios) == 1 and radios[0].freqs == [5865000000, 5905000000]
    assert s.read_chunk(4, timeout_s=0.05) is None  # transient flushed
    radios[0].sink(b"good")
    assert s.read_chunk(4, timeout_s=0.2) == b"good"


def test_recover_reopens_and_retunes():
    radios = []
    s = _source(radios)
    s.tune(5865e6)
    s.recover()
    assert radios[0].closed
    assert len(radios) == 2 and radios[1].freqs == [5865000000]


def test_close_is_idempotent_and_reopens_on_next_tune():
    radios = []
    s = _source(radios)
    s.tune(5865e6)
    s.close()
    s.close()                                        # second close: no-op
    assert radios[0].closed
    s.tune(5905e6)
    assert len(radios) == 2 and radios[1].freqs == [5905000000]
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd agent/scan && python -m pytest tests/test_hackrf_source.py -v`
Expected: FAIL — `cannot import name 'HackrfSource'`.

- [ ] **Step 3: Implement**

Append to `agent/scan/hackrf_source.py`:

```python
RING_SECONDS = 2.0           # rx ring depth: absorbs demod hiccups, bounds memory


class HackrfSource:
    """CaptureSource for the view stream over an injected radio factory.

    tune() opens the device lazily (so the sweep can own it between sessions)
    and flushes the tune transient; close() releases it back to the sweep's
    one-shot hackrf_transfer subprocesses; recover() is the wedge watchdog's
    close+reopen+retune. A later bladeRF backend only needs the same duck type."""

    def __init__(self, open_radio, sample_rate_hz, ring_s=RING_SECONDS):
        self._open_radio = open_radio
        self._fs = float(sample_rate_hz)
        self._ring = IqRing(int(self._fs * 2 * ring_s))
        self._radio = None
        self._freq_hz = None

    @property
    def dropped_bytes(self):
        return self._ring.dropped_bytes

    def pending_bytes(self):
        return self._ring.pending()

    def tune(self, freq_hz):
        if self._radio is None:
            self._radio = self._open_radio()
            self._radio.start_rx(self._ring.write, self._fs)
        self._radio.set_freq(int(freq_hz))
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
        try:
            r.close()
        except Exception:
            LOG.exception("hackrf close failed")


_CDEF = """
typedef struct hackrf_device hackrf_device;
typedef struct {
    hackrf_device* device;
    uint8_t* buffer;
    int buffer_length;
    int valid_length;
    void* rx_ctx;
    void* tx_ctx;
} hackrf_transfer;
typedef int (*hackrf_sample_block_cb_fn)(hackrf_transfer* transfer);
int hackrf_init(void);
int hackrf_open(hackrf_device** device);
int hackrf_close(hackrf_device* device);
int hackrf_set_freq(hackrf_device* device, const uint64_t freq_hz);
int hackrf_set_sample_rate(hackrf_device* device, const double freq_hz);
int hackrf_set_lna_gain(hackrf_device* device, uint32_t value);
int hackrf_set_vga_gain(hackrf_device* device, uint32_t value);
int hackrf_set_amp_enable(hackrf_device* device, const uint8_t value);
int hackrf_start_rx(hackrf_device* device, hackrf_sample_block_cb_fn callback, void* rx_ctx);
int hackrf_stop_rx(hackrf_device* device);
"""


def _ck(rc, what):
    if rc != 0:
        raise RuntimeError(f"{what} failed (rc={rc})")


class LibHackrfRadio:
    """The only hardware-touching class: cffi over libhackrf.so.0.
    hackrf_set_sample_rate auto-selects the matching baseband filter (same
    default hackrf_transfer uses), so no explicit filter call is needed."""

    def __init__(self, lna_db, vga_db, amp):
        from cffi import FFI
        self._ffi = FFI()
        self._ffi.cdef(_CDEF)
        self._lib = self._ffi.dlopen("libhackrf.so.0")
        _ck(self._lib.hackrf_init(), "hackrf_init")
        dev = self._ffi.new("hackrf_device**")
        _ck(self._lib.hackrf_open(dev), "hackrf_open")
        self._dev = dev[0]
        self._lna, self._vga, self._amp = int(lna_db), int(vga_db), int(amp)
        self._cb = None

    def start_rx(self, sink, sample_rate_hz):
        lib, ffi = self._lib, self._ffi
        _ck(lib.hackrf_set_sample_rate(self._dev, float(sample_rate_hz)), "set_sample_rate")
        _ck(lib.hackrf_set_lna_gain(self._dev, self._lna), "set_lna_gain")
        _ck(lib.hackrf_set_vga_gain(self._dev, self._vga), "set_vga_gain")
        _ck(lib.hackrf_set_amp_enable(self._dev, self._amp), "set_amp_enable")

        @ffi.callback("int(hackrf_transfer*)")
        def _on_rx(transfer):
            n = transfer.valid_length
            if n > 0:
                sink(bytes(ffi.buffer(transfer.buffer, n)))
            return 0

        self._cb = _on_rx        # MUST keep a ref: cffi callbacks are GC'd otherwise
        _ck(lib.hackrf_start_rx(self._dev, self._cb, ffi.NULL), "start_rx")

    def set_freq(self, freq_hz):
        _ck(self._lib.hackrf_set_freq(self._dev, int(freq_hz)), "set_freq")

    def close(self):
        try:
            self._lib.hackrf_stop_rx(self._dev)
        except Exception:
            pass
        try:
            self._lib.hackrf_close(self._dev)
        except Exception:
            pass
        self._cb = None


def open_hackrf_radio(lna_db, vga_db, amp):
    """Factory passed to HackrfSource; import-time cffi cost stays out of tests."""
    return LibHackrfRadio(lna_db, vga_db, amp)
```

Add to `agent/scan/requirements.txt` (after the `Pillow` line):

```
cffi>=1.16
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd agent/scan && python -m pytest tests/test_hackrf_source.py -q`
Expected: PASS (LibHackrfRadio is never instantiated by tests).

- [ ] **Step 5: Commit**

```bash
git add agent/scan/hackrf_source.py agent/scan/tests/test_hackrf_source.py agent/scan/requirements.txt
git commit -m "feat(view): HackrfSource - in-process libhackrf capture with live retune"
```

### Task 14: `run_stream_source` — demod over a CaptureSource with a stall watchdog

**Files:**
- Modify: `agent/video/stream_demod.py`
- Test: `agent/video/tests/test_stream_demod.py`

**Interfaces:**
- Consumes: source duck type from Task 13 (`tune`, `read_chunk(n, timeout_s)`, `recover`, `pending_bytes()`, `dropped_bytes`), `ViewEncoder.submit/set_session_stats`.
- Produces: `run_stream_source(vcfg, source, freq_mhz, stop_event, max_s, encoder, clock=None) -> str|None`. Constants `SOURCE_READ_TIMEOUT_S = 0.5`, `SILENCE_RECOVER_S = 3.0`, `CAPTURE_STALL_LIMIT = 3`. Task 15 wires it.

- [ ] **Step 1: Write the failing tests**

Append to `agent/video/tests/test_stream_demod.py`:

```python
from stream_demod import run_stream_source


class _FakeSource:
    """Yields prepared chunks, then None (underrun) forever; sets stop when told."""
    def __init__(self, chunks, stop=None, stop_after=None):
        self._chunks = list(chunks)
        self._stop = stop
        self._stop_after = stop_after
        self.reads = 0
        self.tunes = []
        self.recovers = 0
        self.dropped_bytes = 0
    def tune(self, hz):
        self.tunes.append(hz)
    def read_chunk(self, n, timeout_s):
        self.reads += 1
        if self._stop is not None and self._stop_after is not None \
                and self.reads > self._stop_after:
            self._stop.set()
        return self._chunks.pop(0) if self._chunks else None
    def recover(self):
        self.recovers += 1
    def pending_bytes(self):
        return 0


def test_run_stream_source_tunes_once_and_submits_frames():
    fs = 4e6
    stop = threading.Event()
    src = _FakeSource([bytes(_chunk_bytes(fs))], stop=stop, stop_after=1)
    fenc = _FakeEncoder()
    err = run_stream_source(_vcfg(), src, 947.0, stop, max_s=60.0, encoder=fenc,
                            clock=lambda: 0.0)
    assert err is None                                   # stop event = clean exit
    assert src.tunes == [947.0 * 1e6]
    assert fenc.frames and all(len(f) == 320 * VIEW_CANVAS_HEIGHT for f in fenc.frames)
    assert fenc.stats_fn is not None


def test_run_stream_source_watchdog_recovers_then_gives_up():
    src = _FakeSource([])                                # silence from the start
    fenc = _FakeEncoder()
    err = run_stream_source(_vcfg(), src, 947.0, threading.Event(), max_s=60.0,
                            encoder=fenc, clock=lambda: 0.0)
    assert err == "capture stalled"
    assert src.recovers == 3                             # 3 reopen attempts, then give up


def test_run_stream_source_reports_tune_failure():
    class _Broken(_FakeSource):
        def tune(self, hz):
            raise RuntimeError("no device")
    err = run_stream_source(_vcfg(), _Broken([]), 947.0, threading.Event(),
                            max_s=60.0, encoder=_FakeEncoder(), clock=lambda: 0.0)
    assert "no device" in err
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd agent/video && python -m pytest tests/test_stream_demod.py -k run_stream_source -v`
Expected: FAIL — `cannot import name 'run_stream_source'`.

- [ ] **Step 3: Implement**

Append to `agent/video/stream_demod.py`:

```python
SOURCE_READ_TIMEOUT_S = 0.5   # also the stop-event responsiveness bound
SILENCE_RECOVER_S = 3.0       # continuous rx silence before a device reopen
CAPTURE_STALL_LIMIT = 3       # reopen attempts before the session errors out


def run_stream_source(vcfg, source, freq_mhz, stop_event, max_s, encoder, clock=None):
    """Session demod loop over an in-process CaptureSource (PR-C engine).

    tune() is milliseconds, so a retune re-enters here with the SAME open
    source — no subprocess restarts anywhere. Rx silence > SILENCE_RECOVER_S
    triggers source.recover() (USB-wedge watchdog); CAPTURE_STALL_LIMIT
    consecutive recoveries end the session with an error. The caller's reset
    hook (ViewController) closes the source so the sweep can reclaim the
    device."""
    clock = clock or time.monotonic
    fs = vcfg.view_sample_rate_hz
    chunk_bytes = int(fs * 2 * CHUNK_S)
    standard = None
    tracker = None
    error = None
    silent_s = 0.0
    recoveries = 0
    try:
        source.tune(freq_mhz * 1e6)
    except Exception as e:
        return f"capture tune failed: {e}"
    t_end = clock() + max_s
    frame_budget = max(1, int(round(CHUNK_S * vcfg.view_fps)))
    while not stop_event.is_set() and clock() < t_end:
        buf = source.read_chunk(chunk_bytes, timeout_s=SOURCE_READ_TIMEOUT_S)
        if buf is None:
            silent_s += SOURCE_READ_TIMEOUT_S
            if silent_s < SILENCE_RECOVER_S:
                continue
            if recoveries >= CAPTURE_STALL_LIMIT:
                error = "capture stalled"
                break
            recoveries += 1
            LOG.warning("view capture stalled; reopening device (%d/%d)",
                        recoveries, CAPTURE_STALL_LIMIT)
            try:
                source.recover()
            except Exception as e:
                error = f"capture recover failed: {e}"
                break
            silent_s = 0.0
            continue
        silent_s = 0.0
        iq = iq_from_int8_fast(buf)
        if standard is None:
            bb = lowpass(fm_demod(iq), fs, vcfg.lpf_cutoff_hz)
            standard = pick_standard(bb, fs, vcfg.view_standard,
                                     vcfg.line_snr_db, vcfg.harm_snr_db)
            tracker = SyncTracker(standard)
            tracker.seed(bb, fs)
            trk, src = tracker, source
            encoder.set_session_stats(lambda: {
                "mailbox": src.pending_bytes() // chunk_bytes,
                "dropped_chunks": src.dropped_bytes // chunk_bytes,
                "sync": trk.status()})
            LOG.info("view stream: %s -> %dx%d canvas @%.0ffps (in-process capture)",
                     standard, vcfg.view_width, VIEW_CANVAS_HEIGHT, vcfg.view_fps)
        for fr in select_frames(
                chunk_to_frames(iq, fs, standard, vcfg.view_width, VIEW_CANVAS_HEIGHT,
                                vcfg.lpf_cutoff_hz, vcfg.blank_frac,
                                budget=frame_budget, tracker=tracker),
                CHUNK_S, vcfg.view_fps):
            encoder.submit(fr.tobytes())
    return error
```

Note: after `recoveries` reaches the limit, one more full `SILENCE_RECOVER_S` of quiet ends the session — but a successful chunk read resets ONLY `silent_s`, not `recoveries` (a device that needs a 4th reopen inside one session is wedged; let the error path + `reset` handle it).

- [ ] **Step 4: Run the video suite**

Run: `cd agent/video && python -m pytest tests -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/video/stream_demod.py agent/video/tests/test_stream_demod.py
git commit -m "feat(view): run_stream_source - in-process capture loop with stall watchdog"
```

### Task 15: wire the source engine in `main.py`

**Files:**
- Modify: `agent/scan/main.py` (the Task 9 view block)

**Interfaces:**
- Consumes: `HackrfSource`, `open_hackrf_radio` (Task 13), `run_stream_source` (Task 14).
- Produces: with `view_engine == "persistent"`, `sdr == "hackrf"`, `source == "live"` → in-process capture; the ViewController `reset` hook becomes `source.close` (releases the device to the sweep; the heavy `reset_hackrf` USB re-enumeration stays only in the main-loop crash handler). All other combinations keep Task 9's wiring.

- [ ] **Step 1: Implement (glue — covered by suites + live acceptance)**

In the Task 9 block in `agent/scan/main.py`, replace the `if viewcfg.view_engine == "persistent":` branch with:

```python
                reset = reset_hackrf
                if viewcfg.view_engine == "persistent":
                    from view_encoder import ViewEncoder
                    encoder = ViewEncoder(viewcfg)
                    encoder.start()          # RTSP path (black placeholder) is up from boot
                    if cfg.sdr == "hackrf" and cfg.source == "live":
                        from hackrf_source import HackrfSource, open_hackrf_radio
                        source = HackrfSource(
                            lambda: open_hackrf_radio(cfg.lna_gain, cfg.vga_gain, cfg.amp_enable),
                            viewcfg.view_sample_rate_hz)
                        run = lambda freq, stop, max_s: stream_demod.run_stream_source(
                            viewcfg, source, freq, stop, max_s, encoder)
                        reset = source.close     # release the device for the sweep; no USB re-enum per session
                    else:
                        run = lambda freq, stop, max_s: stream_demod.run_stream_persistent(
                            viewcfg, freq, stop, max_s, encoder,
                            lna=cfg.lna_gain, vga=cfg.vga_gain, amp=cfg.amp_enable)
                else:
                    run = lambda freq, stop, max_s: stream_demod.run_stream(
                        viewcfg, freq, stop, max_s,
                        lna=cfg.lna_gain, vga=cfg.vga_gain, amp=cfg.amp_enable)
```

and pass `reset=reset` (instead of `reset=reset_hackrf`) in the `ViewController(...)` call.

- [ ] **Step 2: Sanity-run both suites**

Run: `cd agent/scan && python -m pytest tests -q && cd ../video && python -m pytest tests -q`
Expected: all PASS.

- [ ] **Step 3: Commit**

```bash
git add agent/scan/main.py
git commit -m "feat(view): wire in-process HackRF capture engine; reset releases, not re-enums"
```

### Task 16: open PR-C + live acceptance checklist

- [ ] **Step 1: Full test sweep**

Run: `cd agent/video && python -m pytest tests -q && cd ../scan && python -m pytest tests -q && cd ../.. && npm test`
Expected: all PASS.

- [ ] **Step 2: Push and open the PR**

```bash
git push -u origin feat/view-inprocess-capture
gh pr create --base main --title "feat(view): in-process HackRF capture - ms retune + wedge watchdog (PR-C)" --body "Part C of docs/superpowers/specs/2026-07-10-view-fast-start-design.md: cffi/libhackrf capture into a bounded ring; retune = hackrf_set_freq + flush (~ms). Stall watchdog reopens the device; VIEW_ENGINE=legacy still restores the subprocess pipeline. Stacked on feat/view-persistent-stream; merge after PR-B. Deploy needs cffi in the Pi venv (requirements.txt updated).

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01AoKgEkvkesR7Y1KjQWekB8"
```

- [ ] **Step 3: Live acceptance (on the Pi + dashboard, after each PR merges & deploys, in order A→B→C)**

Deploy per PR: `ssh andriy@192.168.1.204`, `sudo git -C /opt/fpv-video-stream pull`, `sudo systemctl restart fpv-scan-hackrf` (agent); dashboard host: pull + restart the dashboard service. For PR-C additionally: `/opt/fpv-video-stream/agent/scan/.venv/bin/pip install cffi>=1.16`, then a one-off hardware smoke:

```bash
ssh andriy@192.168.1.204 "cd /opt/fpv-video-stream/agent/scan && sudo systemctl stop fpv-scan-hackrf && .venv/bin/python -c \"from hackrf_source import open_hackrf_radio; r = open_hackrf_radio(40, 20, 0); r.set_freq(5865000000); r.close(); print('hackrf cffi OK')\" && sudo systemctl start fpv-scan-hackrf"
```

Checklist (stopwatch from the dashboard click):
- [ ] Session start click→picture ≤ 3 s (PR-B target ~3-5 s; PR-C ≤ 3 s)
- [ ] Retune click→picture ≤ 1.5 s (PR-C), no black frame — freeze/placeholder only
- [ ] Stats log every ~10 s keeps `dropped_chunks=0`, steady ~15 fps, `mailbox=0` during a session; `repeats>0` only around idle/tuning
- [ ] ≥ 3 retunes + stop + sweep resumes + new session — sweep detections still publish between sessions
- [ ] `VIEW_ENGINE=legacy` in the unit env restores the old pipeline (rollback drill), then remove it
- [ ] `journalctl -u fpv-scan-hackrf` shows no encoder respawn loops; kill ffmpeg manually once → path returns and the player recovers without a page reload

---

## Plan self-review notes (already applied)

- Spec coverage: GOP (T1), retry (T2), fixed canvas + placeholder + persistent ffmpeg (T5-T7), playerKey/onDead (T10), VIEW_ENGINE incl. legacy parity (T4, T9, T15), CaptureSource abstraction + HackRF impl (T12-T13), live retune + watchdog (T14), device release for sweep (T15 reset), bladeRF backend explicitly deferred (spec non-goal).
- The legacy `run_stream` and its tests are never modified — `VIEW_ENGINE=legacy` rollback stays honest.
- Type consistency: `set_session_stats` dict keys (`mailbox`, `dropped_chunks`, `sync`) match between ViewEncoder (T5), `run_stream_persistent` (T7) and `run_stream_source` (T14); `playerKey(view, stream)` signature unchanged for all callers; `startWhep` 5th arg optional so grid players (4-arg) are untouched.
