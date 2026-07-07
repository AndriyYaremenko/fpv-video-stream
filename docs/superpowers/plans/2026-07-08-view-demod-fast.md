# Fast View Demod (2b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the view demod fit 6 MS/s comfortably under realtime on the loaded Pi 5 (target ≤ ~0.75×) with pixel-equivalent output, and absorb transient CPU spikes with a 2-deep chunk mailbox — restoring 6 MS/s sharpness and reaching `dropped_chunks=0`.

**Architecture:** Five pure-numpy hot-path changes measured to cut a 6 MS/s chunk from ~112 ms to ~50 ms on the dev box: strided-median DC estimate, integer-samples-per-line `reshape` instead of `np.interp`, a no-`/128` int8→complex64 reader for the view path only, float32 end-to-end, and building only the fps-budget fields. Plus `ChunkMailbox` depth 2 (FIFO, drop-oldest). Golden tests pin pixel equivalence.

**Tech Stack:** Python 3 / numpy (no new deps), pytest (existing fake-clock/pipe patterns in `agent/video/tests/`).

**Spec:** `docs/superpowers/specs/2026-07-08-view-demod-fast-design.md`

## Global Constraints

- Pure numpy — no numba/scipy/C extensions.
- The SCAN path is untouched: `agent/scan/dweller.py` `iq_from_int8` keeps its `/128` scale (dBm calibration); `agent/video/iqio.py` keeps importing it.
- Output must stay pixel-equivalent: golden tests compare frames (uint8 after `normalize_luma`) within ±1; the integer-spl reshape path must equal the interp reference exactly (atol 1e-12).
- All existing tests keep passing UNMODIFIED except `test_chunk_mailbox_counts_overwrites`, which Task 5 explicitly rewrites for the new depth-2 FIFO semantics.
- Tests: `python -m pytest agent/video/tests -q` (and `agent/scan/tests -q` once before the branch is done).
- Commit after every task (conventional commits).

---

### Task 1: `demod.py` — strided-median DC estimate + float32 path

**Files:**
- Modify: `agent/video/demod.py`
- Test: `agent/video/tests/test_demod.py`

**Interfaces:**
- Produces: `fm_demod(iq, median_stride=64)` — `median_stride=1` restores the exact old behavior; complex64 in → float32 out. `lowpass(x, fs, cutoff_hz)` — float32 in/out (float64 accumulator inside), length/edge behavior unchanged. Tasks 2–4 rely on float32 flowing through.

- [ ] **Step 1: Write the failing tests**

Append to `agent/video/tests/test_demod.py`:

```python
def test_fm_demod_strided_median_matches_exact():
    rng = np.random.default_rng(7)
    iq = (rng.normal(size=100_000) + 1j * rng.normal(size=100_000)).astype(np.complex64)
    exact = fm_demod(iq, median_stride=1)
    fast = fm_demod(iq, median_stride=64)
    # Same signal; only the DC estimate differs by a hair (frames are normalized
    # downstream, so a sub-millirad offset is invisible).
    assert abs(float(exact.mean() - fast.mean())) < 5e-3
    assert np.allclose(exact - exact.mean(), fast - fast.mean(), atol=1e-6)


def test_fm_demod_complex64_stays_float32():
    iq = np.exp(1j * np.linspace(0, 20, 4096)).astype(np.complex64)
    assert fm_demod(iq).dtype == np.float32


def test_lowpass_float32_in_out_matches_float64_reference():
    fs = 1_000_000.0
    rng = np.random.default_rng(11)
    x64 = rng.normal(size=50_000)
    x32 = x64.astype(np.float32)
    y = lowpass(x32, fs, cutoff_hz=20_000.0)
    assert y.dtype == np.float32
    # float64 reference computed inline (old algorithm)
    win = int(round(fs / 20_000.0))
    c = np.cumsum(np.insert(x64, 0, 0.0))
    ma = (c[win:] - c[:-win]) / win
    pad_l = (len(x64) - len(ma)) // 2
    ref = np.concatenate([np.full(pad_l, ma[0]), ma,
                          np.full(len(x64) - len(ma) - pad_l, ma[-1])])
    assert np.allclose(y, ref, atol=1e-4)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest agent/video/tests/test_demod.py -q`
Expected: `test_fm_demod_strided_median_matches_exact` FAILS (`TypeError: unexpected keyword 'median_stride'`); the dtype tests fail on float64 output.

- [ ] **Step 3: Implement**

Replace both functions in `agent/video/demod.py`:

```python
def fm_demod(iq, median_stride=64):
    """FM-demodulate: instantaneous frequency via phase differencing, de-carriered.

    angle(iq[n] * conj(iq[n-1])) is the per-sample phase advance; subtracting the
    median removes the residual carrier offset. The DC estimate uses a strided
    subsample (median_stride=1 restores the exact full median): statistically
    identical on millions of samples, ~60x cheaper than sorting them all.
    complex64 in -> float32 out (no float64 promotion)."""
    if len(iq) < 2:
        return np.zeros(0, dtype=np.float32)
    inst = np.angle(iq[1:] * np.conj(iq[:-1]))
    step = max(1, int(median_stride))
    return inst - inst.dtype.type(np.median(inst[::step]))


def lowpass(x, fs, cutoff_hz):
    """Moving-average low-pass (cumsum), length-preserving, edge-padded.

    Window ~ fs/cutoff. float32 in/out (halves the memory traffic of the view
    chain); the running sum accumulates in float64 — a float32 cumsum over
    millions of samples loses precision."""
    x = np.asarray(x)
    if x.dtype != np.float32:
        x = x.astype(np.float32)
    win = int(round(fs / cutoff_hz)) if cutoff_hz > 0 else 1
    if win <= 1 or win >= len(x):
        return x.copy()
    c = np.cumsum(np.insert(x, 0, np.float32(0.0)), dtype=np.float64)
    ma = ((c[win:] - c[:-win]) / win).astype(np.float32)
    pad_total = len(x) - len(ma)
    pad_left = pad_total // 2
    pad_right = pad_total - pad_left
    return np.concatenate([np.full(pad_left, ma[0], dtype=np.float32), ma,
                           np.full(pad_right, ma[-1], dtype=np.float32)])
```

- [ ] **Step 4: Run the video suite**

Run: `python -m pytest agent/video/tests -q`
Expected: all PASS (existing demod/standard/pipeline tests are tolerance-based and scale/dtype-tolerant; if one fails on an exact-float64 implementation detail rather than behavior, STOP and report it — do not silently loosen assertions).

- [ ] **Step 5: Commit**

```bash
git add agent/video/demod.py agent/video/tests/test_demod.py
git commit -m "perf(view): strided-median DC estimate + float32 demod path"
```

---

### Task 2: `frame.py` — integer-spl reshape fast path in `slice_lines`

**Files:**
- Modify: `agent/video/frame.py` (`slice_lines`)
- Test: `agent/video/tests/test_render.py` — NO: frame tests live in `agent/video/tests/test_pipeline.py` and `test_render.py`; ADD the new tests to `agent/video/tests/test_pipeline.py` if `slice_lines` tests already exist there, otherwise append to the file that currently tests `slice_lines`/`reconstruct_frames` (grep `slice_lines` under `agent/video/tests/` first and use that file; report which in your notes).

**Interfaces:**
- Produces: `slice_lines(baseband, fs, standard)` — same signature; when `fs/LINE_HZ[standard]` is integer within 1e-9 (PAL at 4/6/8 MS/s) it slices by `reshape` (no `np.interp`, no float64 coercion — dtype follows the input); otherwise the old interp path. Sync-roll unchanged.

- [ ] **Step 1: Write the failing tests**

Append to the test file located above:

```python
import numpy as np

from frame import slice_lines
from standard import LINE_HZ


def _slice_interp_reference(bb, fs, standard):
    # The pre-optimization algorithm, kept as an oracle.
    bb = np.asarray(bb, dtype=np.float64)
    spl = fs / LINE_HZ[standard]
    spl_i = int(round(spl))
    n = int(len(bb) // spl)
    starts = (np.arange(n) * spl)[:, None]
    cols = np.arange(spl_i)[None, :]
    rows = np.interp((starts + cols).ravel(), np.arange(len(bb)), bb).reshape(n, spl_i)
    k = int(np.argmin(rows.mean(axis=0)))
    return np.roll(rows, -k, axis=1)


def test_slice_lines_integer_spl_equals_interp_reference():
    fs = 6e6                                     # 6e6 / 15625 == 384 exactly
    bb = np.random.default_rng(3).normal(size=int(fs * 0.02))
    fast = slice_lines(bb, fs, "PAL")
    ref = _slice_interp_reference(bb, fs, "PAL")
    assert fast.shape == ref.shape
    assert np.allclose(fast, ref, atol=1e-12)    # np.interp at grid positions == grid values


def test_slice_lines_preserves_float32():
    fs = 6e6
    bb = np.random.default_rng(4).normal(size=int(fs * 0.01)).astype(np.float32)
    assert slice_lines(bb, fs, "PAL").dtype == np.float32
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest <test-file> -q -k slice_lines`
Expected: the float32 test FAILS (old code coerces to float64); the reference test passes already (interp==interp) — it becomes the regression oracle for the new path.

- [ ] **Step 3: Implement**

Replace `slice_lines` in `agent/video/frame.py`:

```python
def slice_lines(baseband, fs, standard):
    """Slice the baseband into sync-aligned line rows: shape (n_lines, samples_per_line).

    When fs is an integer multiple of the line rate (PAL at 4/6/8 MS/s) the
    slicing is a plain reshape — identical output, no per-sample interpolation.
    dtype follows the input (the view chain stays float32)."""
    bb = np.asarray(baseband)
    spl = fs / LINE_HZ[standard]
    spl_i = int(round(spl))
    n = int(len(bb) // spl)
    if n < 2 or spl_i < 4:
        return np.zeros((0, max(spl_i, 1)))
    if abs(spl - spl_i) < 1e-9:                  # integer samples-per-line: exact fast path
        rows = bb[:n * spl_i].reshape(n, spl_i)
    else:                                        # e.g. NTSC: line rate not integer-divisible
        starts = (np.arange(n) * spl)[:, None]
        cols = np.arange(spl_i)[None, :]
        pos = (starts + cols).ravel()
        rows = np.interp(pos, np.arange(len(bb)), bb.astype(np.float64)).reshape(n, spl_i)
    # Align sync (lowest mean column) to col 0.
    sync_col = int(np.argmin(rows.mean(axis=0)))
    return np.roll(rows, -sync_col, axis=1)
```

- [ ] **Step 4: Run the full video suite**

Run: `python -m pytest agent/video/tests -q`
Expected: all PASS (NTSC tests exercise the interp fallback; PAL fixtures at 4/8 MS/s take the fast path with identical output per the oracle test).

- [ ] **Step 5: Commit**

```bash
git add agent/video/frame.py <test-file>
git commit -m "perf(view): reshape fast path for integer samples-per-line"
```

---

### Task 3: `frame.py` — field budget in `reconstruct_frames` + float32 `build_frame`

**Files:**
- Modify: `agent/video/frame.py` (`reconstruct_frames`, `build_frame`)
- Test: same test file as Task 2

**Interfaces:**
- Produces: `reconstruct_frames(baseband, fs, standard, width=720, blank_frac=0.18, budget=None)` — with `budget=k`, builds exactly `min(k, n_fields)` frames picked EVENLY via `np.round(np.linspace(0, n_frames - 1, budget)).astype(int)`; `budget=None` = old behavior. Each budgeted frame equals the corresponding unbudgeted one. `build_frame` computes in the input dtype (float32 stays float32). Task 4 passes the streamer's fps budget.

- [ ] **Step 1: Write the failing tests**

```python
from frame import reconstruct_frames
from synth import make_cvbs


def test_reconstruct_budget_picks_even_subset_of_identical_frames():
    fs = 4e6
    img = (np.indices((32, 32)).sum(axis=0) % 2).astype(float)
    bb = make_cvbs("PAL", img, fs, frames=6)     # 12 fields
    full = reconstruct_frames(bb, fs, "PAL", width=320)
    k = 5
    budgeted = reconstruct_frames(bb, fs, "PAL", width=320, budget=k)
    assert len(budgeted) == k
    expect_idx = np.round(np.linspace(0, len(full) - 1, k)).astype(int)
    for got, idx in zip(budgeted, expect_idx):
        assert np.allclose(got, full[idx], atol=1e-6)


def test_reconstruct_budget_none_and_oversized_are_noops():
    fs = 4e6
    img = np.tile(np.linspace(0, 1, 32), (32, 1))
    bb = make_cvbs("PAL", img, fs, frames=3)
    full = reconstruct_frames(bb, fs, "PAL", width=320)
    assert len(reconstruct_frames(bb, fs, "PAL", width=320, budget=None)) == len(full)
    assert len(reconstruct_frames(bb, fs, "PAL", width=320, budget=999)) == len(full)


def test_build_frame_pipeline_stays_float32():
    fs = 6e6
    bb = np.random.default_rng(5).normal(size=int(fs * 0.05)).astype(np.float32)
    frames = reconstruct_frames(bb, fs, "PAL", width=360)
    assert frames and frames[0].dtype == np.float32
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest <test-file> -q -k "budget or build_frame_pipeline"`
Expected: FAIL — `TypeError: unexpected keyword 'budget'`; float32 test fails on float64 output.

- [ ] **Step 3: Implement**

In `agent/video/frame.py`, replace `build_frame` and `reconstruct_frames`:

```python
def build_frame(rows, width=720, blank_frac=0.18):
    """Drop sync+blanking, resample each active line to `width` px (vectorized).
    Computes in the input dtype — the view chain stays float32."""
    if rows.shape[0] == 0:
        return np.zeros((0, width), dtype=rows.dtype if rows.size else np.float32)
    start = int(rows.shape[1] * blank_frac)
    active = rows[:, start:]
    src_w = active.shape[1]
    if src_w < 2:
        return np.zeros((rows.shape[0], width), dtype=rows.dtype)
    ratio = (src_w - 1) / (width - 1) if width > 1 else 0.0
    pos = np.arange(width, dtype=np.float32) * np.float32(ratio)
    lo = np.floor(pos).astype(np.int32)
    hi = np.minimum(lo + 1, src_w - 1)
    frac = pos - lo
    return active[:, lo] * (np.float32(1.0) - frac) + active[:, hi] * frac


def reconstruct_frames(baseband, fs, standard, width=720, blank_frac=0.18, budget=None):
    """Slice into lines, chunk into FIELDS (LINES/2), align each to its vertical
    sync, build each frame.

    Real CVBS is interlaced: every field is a complete vertical scan of the
    picture, so stacking a full 2-field frame shows the image TWICE. One field
    per output frame gives a single copy at half vertical resolution.

    budget: build at most this many frames, picked EVENLY across the chunk's
    fields (the streamer's fps budget) — skipped fields are never aligned or
    resampled, which is the point: they would be discarded downstream anyway."""
    rows = slice_lines(baseband, fs, standard)
    field = LINES[standard] // 2
    n_frames = rows.shape[0] // field
    idx = range(n_frames)
    if budget is not None and 0 < budget < n_frames:
        idx = np.round(np.linspace(0, n_frames - 1, budget)).astype(int)
    frames = [build_frame(_align_vsync(rows[f * field:(f + 1) * field]), width, blank_frac)
              for f in idx]
    if not frames:                       # fewer than one full field of lines
        frames.append(build_frame(_align_vsync(rows), width, blank_frac))
    return frames
```

- [ ] **Step 4: Run the full video suite**

Run: `python -m pytest agent/video/tests -q`
Expected: all PASS (scan-side video emit uses `reconstruct_frames` without budget — unchanged default).

- [ ] **Step 5: Commit**

```bash
git add agent/video/frame.py <test-file>
git commit -m "perf(view): field budget in reconstruct_frames + float32 build_frame"
```

---

### Task 4: `stream_demod.py` — fast reader + budget wiring (+ bench parity)

**Files:**
- Modify: `agent/video/stream_demod.py` (`chunk_to_frames` ~line 55, imports ~line 70, `run_stream` demod loop)
- Modify: `agent/video/bench_stream.py` (local reader + both modes)
- Test: `agent/video/tests/test_stream_demod.py`

**Interfaces:**
- Consumes: `reconstruct_frames(..., budget=)` (Task 3), `fm_demod`/`lowpass` float32 (Task 1).
- Produces: `iq_from_int8_fast(raw) -> np.complex64` (no `/128`; view path only); `chunk_to_frames(iq, fs, standard, width, height, lpf_cutoff_hz=5e6, blank_frac=0.18, budget=None)`; `run_stream` uses both, passing `budget=int(round(CHUNK_S * vcfg.view_fps))` (`select_frames` stays as a guard).

- [ ] **Step 1: Write the failing tests**

Append to `agent/video/tests/test_stream_demod.py`:

```python
def test_iq_from_int8_fast_is_scaled_dweller():
    from dweller import iq_from_int8
    from stream_demod import iq_from_int8_fast
    raw = bytes(range(0, 128)) + bytes([255, 254, 128, 127])
    fast = iq_from_int8_fast(raw)
    ref = iq_from_int8(raw) * 128.0
    assert fast.dtype == np.complex64
    assert np.array_equal(fast, ref)             # /128 and *128 are exact in float32


def test_chunk_to_frames_respects_budget():
    fs = 4e6
    img = (np.indices((48, 48)).sum(axis=0) % 2).astype(float)
    bb = make_cvbs("PAL", img, fs, frames=6)
    iq = fm_modulate(bb, fs, 2e6)
    full = chunk_to_frames(iq, fs, "PAL", width=320, height=VIEW_HEIGHT["PAL"],
                           lpf_cutoff_hz=2.5e6)
    lim = chunk_to_frames(iq, fs, "PAL", width=320, height=VIEW_HEIGHT["PAL"],
                          lpf_cutoff_hz=2.5e6, budget=4)
    assert len(full) > 4 and len(lim) == 4
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest agent/video/tests/test_stream_demod.py -q -k "fast or budget"`
Expected: FAIL — `ImportError: iq_from_int8_fast` / `TypeError: unexpected keyword 'budget'`.

- [ ] **Step 3: Implement**

In `agent/video/stream_demod.py`:

`chunk_to_frames` gains the budget and forwards it:

```python
def chunk_to_frames(iq, fs, standard, width, height, lpf_cutoff_hz=5e6, blank_frac=0.18,
                    budget=None):
    """One IQ chunk -> list of fixed-size uint8 gray frames (height x width).
    budget caps how many fields are even built (the streamer's fps budget)."""
    bb = lowpass(fm_demod(iq), fs, lpf_cutoff_hz)
    out = []
    for fr in reconstruct_frames(bb, fs, standard, width, blank_frac, budget=budget):
        if fr.size == 0:
            continue
        out.append(resize_rows(normalize_luma(fr), height))
    return out
```

Replace the `from dweller import iq_from_int8` import (line ~70) with the new local reader, placed right after `CHUNK_S = 0.5`:

```python
def iq_from_int8_fast(raw):
    """int8 IQ -> complex64 WITHOUT the /128 scale: one fewer pass over the chunk.
    FM demod, standard detection and per-frame luma normalization are all
    scale-invariant. The SCAN path keeps dweller.iq_from_int8 — its dBm
    features are calibrated to the /128 scale."""
    data = np.frombuffer(raw, dtype=np.int8).astype(np.float32)
    return data.view(np.complex64)
```

In `run_stream`'s demod loop: `iq = iq_from_int8(buf)` → `iq = iq_from_int8_fast(buf)`, and the frames call becomes:

```python
            for fr in select_frames(
                    chunk_to_frames(iq, fs, standard, vcfg.view_width, height,
                                    vcfg.lpf_cutoff_hz, vcfg.blank_frac,
                                    budget=int(round(CHUNK_S * vcfg.view_fps))),
                    CHUNK_S, vcfg.view_fps):
                q.put(fr.tobytes())
```

In `agent/video/bench_stream.py`: change the local `iq_from_int8` to the no-scale version (keeps the script self-contained AND production-identical):

```python
def iq_from_int8(raw):
    data = np.frombuffer(raw, dtype=np.int8).astype(np.float32)
    return data.view(np.complex64)               # no /128: matches the view path
```

and in `bench_pipeline` drop the `from dweller import iq_from_int8 as iq_from_int8_dweller` import, use the local `iq_from_int8(buf)`, and pass the budget for parity:

```python
        for fr in select_frames(chunk_to_frames(iq, fs, "PAL", width, height, 5e6,
                                                budget=int(round(chunk_s * fps))),
                                chunk_s, fps):
            q.put(fr.tobytes())
```

(`chunk_to_frames` is called positionally there today — keep `height` as the 5th positional arg and `5e6` as `lpf_cutoff_hz`.)

- [ ] **Step 4: Run suites + local perf sanity**

Run: `python -m pytest agent/video/tests -q && python -m pytest agent/scan/tests -q`
Expected: all PASS.
Then: `python agent/video/bench_stream.py --fs 6e6 --rounds 6 --width 360`
Expected: demod-only realtime factor drops to roughly HALF the pre-branch value on the same box (~0.10–0.14× on a dev box that measured 0.23× before). Record the number in your report.

- [ ] **Step 5: Commit**

```bash
git add agent/video/stream_demod.py agent/video/bench_stream.py agent/video/tests/test_stream_demod.py
git commit -m "perf(view): scale-free int8 reader + fps-budget demod wiring"
```

---

### Task 5: `ChunkMailbox` depth 2 — absorb isolated CPU spikes

**Files:**
- Modify: `agent/video/stream_demod.py` (`ChunkMailbox`, ~lines 75-93)
- Test: `agent/video/tests/test_stream_demod.py` (REWRITE `test_chunk_mailbox_counts_overwrites`)

**Interfaces:**
- Produces: `ChunkMailbox(depth=2)` — bounded FIFO: `put` beyond depth drops the OLDEST (increments `dropped`); `take()` pops FIFO order (oldest first — air continuity). `run_stream` and the bench construct it with the default depth; no call-site changes needed.

- [ ] **Step 1: Rewrite the failing test**

REPLACE `test_chunk_mailbox_counts_overwrites` in `agent/video/tests/test_stream_demod.py` with:

```python
def test_chunk_mailbox_fifo_depth2_drops_oldest():
    mb = ChunkMailbox()
    assert mb.take() is None
    mb.put(b"a")
    assert mb.take() == b"a" and mb.take() is None
    assert mb.dropped == 0
    mb.put(b"b")
    mb.put(b"c")                     # depth 2: both retained, in order
    assert mb.dropped == 0
    mb.put(b"d")                     # overflow: oldest ("b") dropped
    assert mb.dropped == 1
    assert mb.take() == b"c" and mb.take() == b"d" and mb.take() is None


def test_chunk_mailbox_custom_depth():
    mb = ChunkMailbox(depth=1)       # old single-slot semantics
    mb.put(b"x")
    mb.put(b"y")
    assert mb.dropped == 1 and mb.take() == b"y"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest agent/video/tests/test_stream_demod.py -q -k chunk_mailbox`
Expected: FAIL — old class keeps only the latest chunk (`take()` returns `b"c"` after two puts, `dropped == 1`).

- [ ] **Step 3: Implement**

Replace `ChunkMailbox` in `agent/video/stream_demod.py`:

```python
class ChunkMailbox:
    """Bounded FIFO handoff from the USB reader to the demod loop.

    depth=2 absorbs an isolated CPU spike: the demod falls one chunk behind and
    catches back up because its average cost is below realtime. Overflow drops
    the OLDEST chunk (air lost, counted — the stats-log metric). take() pops in
    air order, preserving continuity."""

    def __init__(self, depth=2):
        self._lock = threading.Lock()
        self._d = deque()
        self._depth = max(1, int(depth))
        self.dropped = 0

    def put(self, buf):
        with self._lock:
            if len(self._d) >= self._depth:
                self._d.popleft()
                self.dropped += 1
            self._d.append(buf)

    def take(self):
        with self._lock:
            return self._d.popleft() if self._d else None
```

- [ ] **Step 4: Run the full video suite ×2 (thread-sensitive tests)**

Run: `python -m pytest agent/video/tests -q` (twice)
Expected: all PASS both times — the `run_stream` integration tests construct `ChunkMailbox()` internally and tolerate the FIFO semantics (single-chunk scenarios are unaffected; the endless-capture tests just buffer one extra chunk).

- [ ] **Step 5: Commit**

```bash
git add agent/video/stream_demod.py agent/video/tests/test_stream_demod.py
git commit -m "feat(view): 2-deep FIFO chunk mailbox absorbs transient CPU spikes"
```

---

### Task 6: Deploy + Pi gates + live acceptance (operator/hardware)

**Files:** none (operational). Pre-req: PR merged to `main`.

- [ ] **Step 1: Pi pull + demod-only gate**

```bash
# pull as root (repo is root-owned): sudo git -C /opt/fpv-video-stream pull --ff-only
# copy the NEW bench to /tmp (sudo -S steals stdin from `python -`): cat bench > /tmp/bench_stream.py
# demod-only at 6 MS/s (expect <= ~0.9x realtime under the live bladeRF+x264 load):
/opt/fpv-video-stream/agent/scan/.venv/bin/python /tmp/bench_stream.py --fs 6e6 --rounds 6 --width 360
```

- [ ] **Step 2: Pipeline gate ×2 (production-like: stop `fpv-scan-hackrf` during the bench)**

```bash
sudo systemctl stop fpv-scan-hackrf
sudo nice -n -10 /opt/fpv-video-stream/agent/scan/.venv/bin/python /tmp/bench_stream.py --pipeline --fs 6e6 --rounds 40 --width 360
# expect: dropped_chunks=0 on 2 consecutive runs (the 2-deep mailbox absorbs isolated spikes)
```

- [ ] **Step 3: Deploy config + restart**

Update `/etc/systemd/system/fpv-scan-hackrf.service.d/tune.conf`: `VIEW_SAMPLE_RATE_HZ=6000000` (keep `Nice=-10`); `sudo systemctl daemon-reload && sudo systemctl start fpv-scan-hackrf`.

- [ ] **Step 4: Live acceptance (needs the HackRF back on USB — physical hub power-cycle if still absent)**

View a real signal ≥60 s at 6 MS/s → `journalctl -u fpv-scan-hackrf -f` stats lines show ~15.0 fps, `dropped_chunks=0` (judge from mid-session lines); picture visibly sharper than the 4 MS/s interim; retune/stop unchanged.

- [ ] **Step 5: Update memory** — sdr-view-stream (2b shipped, 6 MS/s restored) + improve-sdr-view-next (sub-feature list status).

---

## Self-Review (done at plan-writing time)

- **Spec coverage:** strided median + float32 demod (T1), reshape fast path (T2), field budget + float32 frames (T3), scale-free reader + budget wiring + bench parity (T4), 2-deep mailbox (T5), deploy/gates/acceptance + 6 MS/s restore (T6). Golden/equivalence tests in T1-T4; scan path untouched (only `stream_demod`/`bench` swap readers). ✓
- **Type consistency:** `fm_demod(iq, median_stride=64)`, `lowpass` float32, `slice_lines` dtype-follow, `reconstruct_frames(..., budget=None)`, `chunk_to_frames(..., budget=None)`, `iq_from_int8_fast(raw)`, `ChunkMailbox(depth=2)` — call sites in T4/T5 match. ✓
- **Placeholder scan:** complete code in every step; T2's test-file location is resolved by the implementer via grep with an explicit report-back (the file split isn't knowable from the plan author's cache). ✓
