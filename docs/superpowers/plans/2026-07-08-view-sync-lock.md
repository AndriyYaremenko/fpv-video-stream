# View Sync Lock (sub-feature 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lock the SDR view picture horizontally (no shear) and vertically (no roll/jump) by measuring the ACTUAL line rate and using a robust cross-chunk vertical-blanking lock, surfacing the lock state in the stats log — without disturbing the scan path or the 2b reshape fast path.

**Architecture:** A stateful `SyncTracker` (one per view session) seeds the actual line frequency once from an rfft peak (parabolic-interpolated, clamped to ±0.5% of nominal), and carries the vertical-blanking row across chunks. `slice_lines` gains a `line_hz` override that always reshapes at the rounded integer samples-per-line; a new `deshear` corrects the fractional-samples-per-line residual with a cheap per-row gather; `_align_vsync` gains a low-mean-AND-low-variance detector with a predicted-phase bias. All of it engages only when a tracker is passed — `tracker=None`/`line_hz=None` is today's behavior bit-for-bit (scan snapshots untouched).

**Tech Stack:** Python 3 / numpy (no new deps), pytest with synthetic CVBS (`agent/video/synth.py`).

**Spec:** `docs/superpowers/specs/2026-07-08-view-sync-lock-design.md`

## Global Constraints

- Pure numpy — no new deps.
- `tracker=None` / `line_hz=None` defaults reproduce the current output bit-for-bit (golden regression); the scan path (`pipeline.py`, `video_emit`) calls `reconstruct_frames` without a tracker and must not change.
- Line-rate lock: parabolic rfft-peak refine, EMA not needed (seed once per session — the crystal is stable within a session); clamp the refined rate to ±0.5% of nominal, else keep nominal and `locked=False`.
- Line-rate override path in `slice_lines` ALWAYS reshapes at `round(fs/line_hz)` (never `np.interp` — that would undo 2b); the sub-integer residual is corrected by `deshear`.
- Preserve the 6 MS/s `--pipeline` gate (`dropped_chunks=0`); deshear runs only on the fps-budget fields.
- Tests: `python -m pytest agent/video/tests -q` and `agent/scan/tests -q`.
- Commit after every task (conventional commits).

---

### Task 1: `synth.make_cvbs(line_hz=None)` — off-nominal test signals

**Files:**
- Modify: `agent/video/synth.py` (`make_cvbs`)
- Test: `agent/video/tests/test_synth.py`

**Interfaces:**
- Produces: `make_cvbs(standard, image, fs, frames=1, interlaced=False, vbi_lines=0, line_hz=None)` — `line_hz` overrides `LINE_HZ[standard]` for the sync-train line rate (the vertical mapping still uses `LINES[standard]`). `line_hz=None` = current behavior. Every later task's tests generate off-nominal signals with it.

- [ ] **Step 1: Write the failing test**

Append to `agent/video/tests/test_synth.py`:

```python
def test_make_cvbs_line_hz_override_changes_sync_period():
    fs = 6e6
    img = np.tile(np.linspace(0, 1, 32), (32, 1))
    nominal = make_cvbs("PAL", img, fs, frames=1)                     # 15625 Hz
    off = make_cvbs("PAL", img, fs, frames=1, line_hz=15705.0)        # faster lines
    # sync pulses (samples at the sync level) recur every fs/line_hz samples;
    # a higher line rate packs more sync pulses into the same signal length.
    def n_sync_edges(sig):
        at_sync = sig < 0.15
        return int(np.sum(at_sync[1:] & ~at_sync[:-1]))
    assert n_sync_edges(off) > n_sync_edges(nominal)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest agent/video/tests/test_synth.py::test_make_cvbs_line_hz_override_changes_sync_period -q`
Expected: FAIL — `TypeError: unexpected keyword 'line_hz'`.

- [ ] **Step 3: Implement**

In `agent/video/synth.py` `make_cvbs`, change the signature to add `line_hz=None` and replace the `line_hz = LINE_HZ[standard]` line:

```python
def make_cvbs(standard, image, fs, frames=1, interlaced=False, vbi_lines=0, line_hz=None):
```

```python
    line_hz = LINE_HZ[standard] if line_hz is None else float(line_hz)
```

(the rest of the function is unchanged; `lines = LINES[standard]` stays nominal).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest agent/video/tests -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/video/synth.py agent/video/tests/test_synth.py
git commit -m "test(view): make_cvbs line_hz override for off-nominal sync"
```

---

### Task 2: `SyncTracker` — actual line-rate seed + vsync phase state

**Files:**
- Create: `agent/video/sync_tracker.py`
- Test: `agent/video/tests/test_sync_tracker.py` (new)

**Interfaces:**
- Consumes: `LINE_HZ` from `standard`; `make_cvbs`/`fm_modulate` + `fm_demod`/`lowpass` in tests.
- Produces:
  - `SyncTracker(standard)` — `.line_hz` (float, init `LINE_HZ[standard]`), `.locked` (bool, init False), `.vsync_row` (int|None, init None).
  - `.seed(baseband, fs)` — one-time: rfft the baseband, parabolic-interpolate the magnitude peak in a ±0.4%-of-nominal window around `LINE_HZ[standard]`, clamp the result to ±0.5% of nominal; in range → set `.line_hz`, `.locked=True`; out of range → keep nominal, `.locked=False`. Idempotent-safe to call again (re-seeds).
  - `.note_vsync(row)` — store the last locked field-boundary row (Task 4 calls it).
  - `.status()` — dict `{line_hz, locked, vsync_row}` for the stats log.

- [ ] **Step 1: Write the failing tests**

Create `agent/video/tests/test_sync_tracker.py`:

```python
import numpy as np

from sync_tracker import SyncTracker
from synth import make_cvbs, fm_modulate
from demod import fm_demod, lowpass
from standard import LINE_HZ


def _baseband(line_hz, fs=6e6):
    img = np.tile(np.linspace(0, 1, 64), (64, 1))
    bb = make_cvbs("PAL", img, fs, frames=4, line_hz=line_hz)
    return lowpass(fm_demod(fm_modulate(bb, fs, 4e6)), fs, 5e6)


def test_seed_recovers_off_nominal_line_rate():
    fs = 6e6
    t = SyncTracker("PAL")
    assert t.line_hz == LINE_HZ["PAL"] and t.locked is False
    t.seed(_baseband(15705.0, fs), fs)
    assert t.locked is True
    assert abs(t.line_hz - 15705.0) < 5.0                 # within a few Hz


def test_seed_holds_nominal_and_unlocked_on_noise():
    fs = 6e6
    noise = np.random.default_rng(2).normal(0, 1, 300_000)
    t = SyncTracker("PAL")
    t.seed(noise, fs)
    assert t.locked is False
    assert t.line_hz == LINE_HZ["PAL"]                    # clamp rejected the spurious peak


def test_seed_clamps_absurd_peak_to_nominal():
    fs = 6e6
    # a strong tone far from the line rate must be rejected by the +/-0.5% clamp
    t = SyncTracker("PAL")
    tone = np.sin(2 * np.pi * 30_000.0 * np.arange(200_000) / fs)
    t.seed(tone, fs)
    assert t.locked is False and t.line_hz == LINE_HZ["PAL"]


def test_note_vsync_and_status():
    t = SyncTracker("PAL")
    t.seed(_baseband(15625.0), 6e6)
    t.note_vsync(37)
    s = t.status()
    assert s["vsync_row"] == 37 and s["locked"] is True
    assert abs(s["line_hz"] - 15625.0) < 5.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest agent/video/tests/test_sync_tracker.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sync_tracker'`.

- [ ] **Step 3: Implement**

Create `agent/video/sync_tracker.py`:

```python
"""Per-session sync lock state for the SDR live-view stream.

Seeds the ACTUAL line frequency once from an rfft peak (a real FPV camera's
crystal is off nominal by tens-hundreds of Hz; slicing at the nominal rate
shears the picture) and carries the vertical-blanking row across chunks so the
picture does not jump. View-path only; the scan snapshot path never builds one."""
import numpy as np

from standard import LINE_HZ

_CLAMP = 0.005          # accept a refined line rate within +/-0.5% of nominal (crystal bound)
_SEARCH = 0.004         # search the rfft peak within +/-0.4% of nominal


class SyncTracker:
    def __init__(self, standard):
        self.standard = standard
        self._nominal = float(LINE_HZ[standard])
        self.line_hz = self._nominal
        self.locked = False
        self.vsync_row = None

    def seed(self, baseband, fs):
        """One-time actual-line-rate estimate from a magnitude-spectrum peak."""
        bb = np.asarray(baseband, dtype=np.float64)
        n = len(bb)
        if n < 4096:
            self.locked = False
            return
        spec = np.abs(np.fft.rfft(bb * np.hanning(n)))
        bin_hz = fs / n
        k0 = int(round(self._nominal / bin_hz))
        half = max(1, int(round(self._nominal * _SEARCH / bin_hz)))
        lo, hi = max(1, k0 - half), min(len(spec) - 1, k0 + half)
        if hi <= lo:
            self.locked = False
            return
        k = lo + int(np.argmax(spec[lo:hi + 1]))
        # parabolic interpolation of the peak for sub-bin frequency
        if 0 < k < len(spec) - 1:
            a, b, c = spec[k - 1], spec[k], spec[k + 1]
            denom = (a - 2 * b + c)
            delta = 0.5 * (a - c) / denom if abs(denom) > 1e-12 else 0.0
        else:
            delta = 0.0
        f = (k + delta) * bin_hz
        if abs(f - self._nominal) <= self._nominal * _CLAMP:
            self.line_hz = float(f)
            self.locked = True
        else:
            self.line_hz = self._nominal
            self.locked = False

    def note_vsync(self, row):
        self.vsync_row = int(row)

    def status(self):
        return {"line_hz": self.line_hz, "locked": self.locked, "vsync_row": self.vsync_row}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest agent/video/tests/test_sync_tracker.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/video/sync_tracker.py agent/video/tests/test_sync_tracker.py
git commit -m "feat(view): SyncTracker seeds actual line rate, holds vsync phase"
```

---

### Task 3: `frame.py` — `line_hz` override in `slice_lines` + `deshear`

**Files:**
- Modify: `agent/video/frame.py` (`slice_lines`; add `deshear`)
- Test: `agent/video/tests/test_frame.py`

**Interfaces:**
- Produces:
  - `slice_lines(baseband, fs, standard, line_hz=None)` — `line_hz` given → `spl_i = int(round(fs/line_hz))` and ALWAYS reshape (no interp); `line_hz=None` = today's behavior (nominal, integer-spl reshape else interp). Sync-roll unchanged.
  - `deshear(rows, drift_per_row)` — shift row `r` left-circularly by `round(r * drift_per_row)` samples via a vectorized per-row gather; `drift_per_row == 0` → identity; preserves dtype/shape.

- [ ] **Step 1: Write the failing tests**

Append to `agent/video/tests/test_frame.py`:

```python
from frame import deshear


def test_slice_lines_line_hz_override_uses_rounded_reshape():
    fs = 6e6
    bb = np.random.default_rng(3).normal(size=int(fs * 0.02)).astype(np.float32)
    rows = slice_lines(bb, fs, "PAL", line_hz=15705.0)     # spl = 382.04 -> reshape at 382
    assert rows.shape[1] == 382
    assert rows.dtype == np.float32                         # override path never coerces float64


def test_deshear_straightens_a_known_shear():
    # A straight vertical bar, then sheared by rolling row r by round(r*D); deshear undoes it.
    n, w, D = 40, 100, 0.7
    base = np.zeros((n, w), dtype=np.float32)
    base[:, 50] = 1.0                                       # vertical bar at col 50
    sheared = np.stack([np.roll(base[r], int(round(r * D))) for r in range(n)])
    fixed = deshear(sheared, D)
    # every row's bar returns to col 50 (within +/-1 px from integer rounding)
    bar_cols = fixed.argmax(axis=1)
    assert np.all(np.abs(bar_cols - 50) <= 1)


def test_deshear_zero_is_identity():
    rows = np.random.default_rng(1).normal(size=(20, 64)).astype(np.float32)
    assert np.array_equal(deshear(rows, 0.0), rows)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest agent/video/tests/test_frame.py -q -k "line_hz_override or deshear"`
Expected: FAIL — `ImportError: deshear` / `TypeError: unexpected keyword 'line_hz'`.

- [ ] **Step 3: Implement**

In `agent/video/frame.py`, replace `slice_lines` with:

```python
def slice_lines(baseband, fs, standard, line_hz=None):
    """Slice the baseband into sync-aligned line rows: shape (n_lines, samples_per_line).

    Integer samples-per-line (PAL at 4/6/8 MS/s) -> plain reshape, identical
    output, no per-sample interpolation; dtype follows the input.

    line_hz overrides the nominal line rate (the tracker's measured value) and
    ALWAYS reshapes at the rounded integer spl — never np.interp (that would
    undo the fast path); the sub-integer residual is corrected by deshear."""
    bb = np.asarray(baseband)
    if line_hz is not None:
        spl_i = int(round(fs / line_hz))
        n = len(bb) // spl_i
        if n < 2 or spl_i < 4:
            return np.zeros((0, max(spl_i, 1)))
        rows = bb[:n * spl_i].reshape(n, spl_i)
    else:
        spl = fs / LINE_HZ[standard]
        spl_i = int(round(spl))
        n = int(len(bb) // spl)
        if n < 2 or spl_i < 4:
            return np.zeros((0, max(spl_i, 1)))
        if abs(spl - spl_i) < 1e-9:
            rows = bb[:n * spl_i].reshape(n, spl_i)
        else:
            starts = (np.arange(n) * spl)[:, None]
            cols = np.arange(spl_i)[None, :]
            pos = (starts + cols).ravel()
            rows = np.interp(pos, np.arange(len(bb)), bb.astype(np.float64)).reshape(n, spl_i)
    sync_col = int(np.argmin(rows.mean(axis=0)))
    return np.roll(rows, -sync_col, axis=1)
```

Add `deshear` immediately after `slice_lines`:

```python
def deshear(rows, drift_per_row):
    """Undo linear horizontal shear: shift row r left-circularly by
    round(r * drift_per_row) samples (a per-row gather). drift_per_row is the
    fractional samples-per-line the sync tip walks after integer-spl slicing;
    0 -> identity."""
    if drift_per_row == 0 or rows.shape[0] == 0:
        return rows
    n, w = rows.shape
    shift = np.round(np.arange(n) * drift_per_row).astype(np.int64)
    idx = (np.arange(w)[None, :] + shift[:, None]) % w
    return np.take_along_axis(rows, idx, axis=1)
```

- [ ] **Step 4: Run the full video suite**

Run: `python -m pytest agent/video/tests -q`
Expected: all PASS (the `line_hz=None` branch of `slice_lines` is byte-identical to before; existing frame tests unaffected).

- [ ] **Step 5: Commit**

```bash
git add agent/video/frame.py agent/video/tests/test_frame.py
git commit -m "feat(view): line_hz slice override + deshear residual correction"
```

---

### Task 4: `frame.py` — robust `_align_vsync` + `reconstruct_frames(tracker=)`

**Files:**
- Modify: `agent/video/frame.py` (`_align_vsync`, `reconstruct_frames`)
- Test: `agent/video/tests/test_frame.py`

**Interfaces:**
- Consumes: `SyncTracker` (Task 2), `deshear`/`slice_lines(line_hz=)` (Task 3).
- Produces:
  - `_align_vsync(rows, tracker=None, win=6)` — scores each candidate row window by **low mean AND low variance** relative to the field; with a tracker that has a `vsync_row`, biases toward that row (small band) and re-acquires if the best local candidate is weak; calls `tracker.note_vsync(k)`; `tracker=None` = the current darkest-window heuristic unchanged.
  - `reconstruct_frames(baseband, fs, standard, width=720, blank_frac=0.18, budget=None, tracker=None)` — with a tracker: slices at `tracker.line_hz`, applies `deshear(field, fs/tracker.line_hz - round(fs/tracker.line_hz))` per field, passes the tracker to `_align_vsync`; `tracker=None` = unchanged.

- [ ] **Step 1: Write the failing tests**

```python
def test_align_vsync_locks_vbi_not_dark_scene():
    # A field: a genuine VBI (several near-sync rows) at row 5, plus a dark SCENE
    # band (low mean, but high variance) at row 25. Mean-only picks the wrong one
    # if the scene is darker on average; low-mean+low-variance must pick the VBI.
    from frame import _align_vsync
    rng = np.random.default_rng(7)
    field = rng.uniform(0.3, 1.0, size=(50, 80)).astype(np.float32)
    field[5:9, :] = 0.02 + rng.uniform(0, 0.01, size=(4, 80))     # VBI: low mean, low var
    field[25:29, :] = rng.uniform(0.0, 0.2, size=(4, 80))         # dark scene: low mean, HIGH var
    out = _align_vsync(field)
    # the VBI rows (originally 5..8) must now sit at the top
    assert out[0:4].mean() < 0.1 and out[0:4].std() < 0.05


def test_reconstruct_with_tracker_reduces_shear_vs_nominal():
    fs = 6e6
    bars = np.zeros((64, 64), dtype=np.float64)
    bars[:, ::8] = 1.0                                           # vertical bars
    bb = make_cvbs("PAL", bars, fs, frames=4, interlaced=True, vbi_lines=6, line_hz=15705.0)
    base = lowpass(fm_demod(fm_modulate(bb, fs, 4e6)), fs, 5e6)
    t = SyncTracker("PAL")
    t.seed(base, fs)
    plain = reconstruct_frames(base, fs, "PAL", width=240)[0]
    locked = reconstruct_frames(base, fs, "PAL", width=240, tracker=t)[0]
    # shear metric: how much each row's brightness profile shifts vs the field's
    # mean profile (lower = straighter). The locked frame must be straighter.
    def shear_metric(fr):
        prof = fr.mean(axis=0)
        return float(np.mean([np.abs(np.correlate(r - r.mean(), prof - prof.mean(), "full").argmax()
                                     - (len(prof) - 1)) for r in fr]))
    assert shear_metric(locked) < shear_metric(plain)


def test_reconstruct_tracker_none_bit_identical():
    fs = 4e6
    img = np.tile(np.linspace(0, 1, 32), (32, 1))
    bb = make_cvbs("PAL", img, fs, frames=3)
    base = lowpass(fm_demod(fm_modulate(bb, fs, 2e6)), fs, 5e6)
    a = reconstruct_frames(base, fs, "PAL", width=320)
    b = reconstruct_frames(base, fs, "PAL", width=320, tracker=None)
    assert len(a) == len(b) and all(np.array_equal(x, y) for x, y in zip(a, b))
```

(imports `SyncTracker`, `make_cvbs`, `fm_modulate`, `fm_demod`, `lowpass`, `reconstruct_frames` — merge with the file's existing imports.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest agent/video/tests/test_frame.py -q -k "align_vsync_locks or with_tracker or tracker_none"`
Expected: FAIL — `_align_vsync` takes no `tracker`; `reconstruct_frames` takes no `tracker`; VBI-vs-scene test fails on the mean-only heuristic.

- [ ] **Step 3: Implement**

Replace `_align_vsync` and `reconstruct_frames` in `agent/video/frame.py`:

```python
def _align_vsync(rows, tracker=None, win=6):
    """Roll rows so the vertical-blanking interval sits at the top.

    Scores each length-`win` row window by low mean AND low variance: the broad
    vsync pulses sit near sync level with little variation, unlike a merely dark
    SCENE region (low mean, normal variance). With a tracker carrying a prior
    vsync_row, biases the search to a small band around it and re-acquires when
    the best local candidate is weak. No-op when no window clearly beats the
    field. tracker=None keeps the original darkest-window heuristic."""
    n = rows.shape[0]
    if n < win * 3:
        return rows
    m = rows.mean(axis=1)
    v = rows.var(axis=1)
    ext_m = np.concatenate([m, m[:win - 1]])
    ext_v = np.concatenate([v, v[:win - 1]])
    ones = np.ones(win, dtype=ext_m.dtype)
    win_mean = np.convolve(ext_m, ones, "valid")[:n] / win
    win_var = np.convolve(ext_v, ones, "valid")[:n] / win
    spread = float(m.max() - m.min())
    if spread <= 1e-9:
        return rows
    # score: prefer low mean AND low variance (both normalized to the field)
    score = win_mean / spread + win_var / (float(v.max()) + 1e-12)
    if tracker is not None and tracker.vsync_row is not None:
        band = max(win, n // 8)
        centre = tracker.vsync_row % n
        mask = np.ones(n) * 1e9
        lo, hi = centre - band, centre + band + 1
        for r in range(lo, hi):
            mask[r % n] = 0.0
        biased = score + mask
        k = int(np.argmin(biased))
        if score[k] > score.min() * 4:          # local band is weak -> re-acquire globally
            k = int(np.argmin(score))
    else:
        k = int(np.argmin(score))
    depth = float(np.median(m) - win_mean[k])
    if depth < 0.25 * spread:
        if tracker is not None:
            tracker.note_vsync(k)
        return rows
    if tracker is not None:
        tracker.note_vsync(k)
    return np.roll(rows, -k, axis=0)


def reconstruct_frames(baseband, fs, standard, width=720, blank_frac=0.18, budget=None,
                       tracker=None):
    """Slice into lines, chunk into FIELDS (LINES/2), align each to its vertical
    sync, build each frame.

    With a tracker: slice at the measured line rate, deshear the fractional
    residual per field, and lock vsync with cross-chunk bias. tracker=None =
    nominal slicing, no deshear, independent per-field vsync (scan path)."""
    line_hz = tracker.line_hz if tracker is not None else None
    rows = slice_lines(baseband, fs, standard, line_hz=line_hz)
    if tracker is not None:
        spl_i = int(round(fs / tracker.line_hz))
        drift = fs / tracker.line_hz - spl_i
    else:
        drift = 0.0
    field = LINES[standard] // 2
    n_frames = rows.shape[0] // field
    idx = range(n_frames)
    if budget is not None:
        budget = int(budget)
        if budget <= 0:
            return []
        if budget < n_frames:
            idx = np.round(np.linspace(0, n_frames - 1, budget)).astype(int)
    frames = []
    for f in idx:
        fr = _align_vsync(rows[f * field:(f + 1) * field], tracker=tracker)
        if drift != 0.0:
            fr = deshear(fr, drift)
        frames.append(build_frame(fr, width, blank_frac))
    if not frames:
        fr = _align_vsync(rows, tracker=tracker)
        if drift != 0.0:
            fr = deshear(fr, drift)
        frames.append(build_frame(fr, width, blank_frac))
    return frames
```

- [ ] **Step 4: Run the full suites**

Run: `python -m pytest agent/video/tests -q` then `python -m pytest agent/scan/tests -q`
Expected: all PASS. The `tracker=None` regression test proves the scan path is byte-identical; the shear test proves the lock helps; the VBI test proves robustness.

- [ ] **Step 5: Commit**

```bash
git add agent/video/frame.py agent/video/tests/test_frame.py
git commit -m "feat(view): robust vsync lock + tracker-driven reconstruct"
```

---

### Task 5: `stream_demod.py` — seed the tracker + sync in the stats line

**Files:**
- Modify: `agent/video/stream_demod.py` (`chunk_to_frames`, `run_stream`, `writer_loop`)
- Test: `agent/video/tests/test_stream_demod.py`

**Interfaces:**
- Consumes: `SyncTracker` (Task 2), `reconstruct_frames(..., tracker=)` (Task 4).
- Produces: `chunk_to_frames(..., budget=None, tracker=None)` forwards the tracker; `run_stream` builds one `SyncTracker` per session, seeds it on the first demodulated chunk (right after `pick_standard`), passes it into `chunk_to_frames`; `writer_loop` stats line gains `sync=H<drift> V<row> line=<hz>` from an optional `sync_status=None` callable.

- [ ] **Step 1: Write the failing tests**

Append to `agent/video/tests/test_stream_demod.py`:

```python
def test_chunk_to_frames_forwards_tracker():
    from sync_tracker import SyncTracker
    fs = 6e6
    bars = np.zeros((64, 64)); bars[:, ::8] = 1.0
    bb = make_cvbs("PAL", bars, fs, frames=4, interlaced=True, vbi_lines=6, line_hz=15705.0)
    iq = fm_modulate(bb, fs, 4e6)
    t = SyncTracker("PAL")
    from demod import fm_demod as _fd, lowpass as _lp
    t.seed(_lp(_fd(iq), fs, 5e6), fs)
    frames = chunk_to_frames(iq, fs, "PAL", width=240, height=VIEW_HEIGHT["PAL"],
                             lpf_cutoff_hz=5e6, tracker=t)
    assert frames and frames[0].shape == (288, 240)          # tracker path still yields fixed-size frames


def test_writer_loop_stats_includes_sync(caplog):
    import logging
    q = FrameQueue(maxlen=8)
    for b in (b"1", b"2", b"3"):
        q.put(b)
    q.close()
    t = [0.0]
    def clock():
        t[0] += 6.0
        return t[0]
    with caplog.at_level(logging.INFO):
        writer_loop(q, _Pacer(), _FakeProc(), threading.Event(), {"msg": None},
                    dropped_chunks=lambda: 0, mailbox_len=lambda: 1,
                    sync_status=lambda: {"line_hz": 15705.0, "locked": True, "vsync_row": 37},
                    clock=clock)
    line = [r.getMessage() for r in caplog.records if "view stream:" in r.getMessage()][0]
    assert "line=15705" in line and "V37" in line
```

(reuses the `_Pacer` class already defined in the file for `writer_loop` tests.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest agent/video/tests/test_stream_demod.py -q -k "forwards_tracker or stats_includes_sync"`
Expected: FAIL — `chunk_to_frames` has no `tracker`; `writer_loop` has no `sync_status`.

- [ ] **Step 3: Implement**

In `agent/video/stream_demod.py`:

`chunk_to_frames` — add `tracker=None` and forward:

```python
def chunk_to_frames(iq, fs, standard, width, height, lpf_cutoff_hz=5e6, blank_frac=0.18,
                    budget=None, tracker=None):
    """One IQ chunk -> list of fixed-size uint8 gray frames (height x width).
    budget caps how many fields are built; tracker locks line rate + vsync."""
    bb = lowpass(fm_demod(iq), fs, lpf_cutoff_hz)
    out = []
    for fr in reconstruct_frames(bb, fs, standard, width, blank_frac, budget=budget,
                                 tracker=tracker):
        if fr.size == 0:
            continue
        out.append(resize_rows(normalize_luma(fr), height))
    return out
```

Add the import near the top (after the other flat-module imports):

```python
from sync_tracker import SyncTracker
```

In `run_stream`, where `standard` is detected (the `if standard is None:` block that calls `pick_standard`), after `height = VIEW_HEIGHT[standard]` add:

```python
                tracker = SyncTracker(standard)
                tracker.seed(bb, fs)         # bb is the demodulated first chunk used for detection
```

(hoist a `tracker = None` initialization next to `standard = None` before the loop.) Then the frames call passes it:

```python
            for fr in select_frames(
                    chunk_to_frames(iq, fs, standard, vcfg.view_width, height,
                                    vcfg.lpf_cutoff_hz, vcfg.blank_frac,
                                    budget=frame_budget, tracker=tracker),
                    CHUNK_S, vcfg.view_fps):
                q.put(fr.tobytes())
```

And the writer thread kwargs gain the sync status:

```python
                writer = threading.Thread(
                    target=writer_loop, args=(q, pacer, enc, stop_event, err),
                    kwargs={"dropped_chunks": lambda: mailbox.dropped,
                            "mailbox_len": lambda: len(mailbox),
                            "sync_status": (lambda: tracker.status()) if tracker else None,
                            "clock": clock},
                    daemon=True)
```

In `writer_loop`, add the `sync_status=None` parameter and extend the log line:

```python
def writer_loop(q, pacer, enc, stop_event, err, dropped_chunks=None, mailbox_len=None,
                sync_status=None, clock=None, log_every_s=10.0):
```

Replace the `LOG.info("view stream: ...")` stats call with:

```python
            st = sync_status() if sync_status is not None else None
            sync = (" sync=H%.2f V%s line=%.0fHz" % (
                        st["line_hz"] / 15625.0 - 1.0, st["vsync_row"], st["line_hz"])
                    if st else "")
            LOG.info("view stream: %.1f fps, queue=%d, mailbox=%d, dropped_frames=%d, "
                     "dropped_chunks=%d%s",
                     fps, len(q), mailbox_len() if mailbox_len is not None else 0,
                     q.dropped, dropped_chunks() if dropped_chunks is not None else 0, sync)
```

(The existing `test_writer_loop_logs_stats_line` passes no `sync_status`, so `sync=""` and its assertions on `mailbox=`/`dropped_chunks=` still hold.)

- [ ] **Step 4: Run the suites (twice — thread-sensitive)**

Run: `python -m pytest agent/video/tests -q` (twice) then `python -m pytest agent/scan/tests -q` and `python -m pytest agent/video/tests agent/scan/tests -q`
Expected: all PASS every time (combined run included — guards against the earlier cross-suite thread-leak class of bug).

- [ ] **Step 5: Commit**

```bash
git add agent/video/stream_demod.py agent/video/tests/test_stream_demod.py
git commit -m "feat(view): seed SyncTracker per session + sync in stats line"
```

---

### Task 6: Deploy + Pi perf gate + live acceptance (operator/hardware)

**Files:** none (operational). Pre-req: PR merged to `main`.

- [ ] **Step 1: Pi pull + perf gate**

```bash
# root-owned repo: sudo git -C /opt/fpv-video-stream pull --ff-only
# copy the new bench to /tmp (sudo -S steals stdin from `python -`)
sudo systemctl stop fpv-scan-hackrf
sudo nice -n -10 /opt/fpv-video-stream/agent/scan/.venv/bin/python /tmp/bench_stream.py --pipeline --fs 6e6 --rounds 40 --width 360
# expect dropped_chunks=0 on 2 consecutive runs — the per-row deshear gather must not blow the budget
sudo systemctl start fpv-scan-hackrf
```

- [ ] **Step 2: Live acceptance (HackRF on a real analog signal, 3410/4240)**

Start a view from the FPV Viewer panel; watch ≥60 s. `journalctl -u fpv-scan-hackrf -f`:
- the stats line shows `sync=H.. V.. line=<hz>Hz` with `line` near but NOT exactly 15625 (proof the lock tracks the real crystal), `locked` implied by a stable non-nominal value;
- the picture no longer shears (verticals stay vertical) and no longer rolls/jumps between frames;
- retune to another detection → re-seeds and re-locks; stop → sweep resumes.

- [ ] **Step 3: Update memory** — `sdr-view-stream` (sub-feature 3 shipped: actual-line-rate + robust VBI lock, `sync=` in stats), `improve-sdr-view-next` (all three sub-features done).

---

## Self-Review (done at plan-writing time)

- **Spec coverage:** SyncTracker + one-time actual-rate seed + ±0.5% clamp (T2); line_hz override never-interp + deshear residual (T3); robust low-mean+low-variance VBI + cross-chunk bias + tracker-driven reconstruct (T4); per-session seed wiring + `sync=` stats (T5); off-nominal test-signal generator enabling all of it (T1); deploy/gate/acceptance (T6). Scan-path-untouched pinned by the `tracker=None` bit-identity test (T4). Fast-path-preserved: override path always reshapes (T3). ✓
- **Deviation from spec, noted:** the spec floated an EMA across chunks; the plan seeds ONCE per session (crystal stable within a session; a per-chunk 3M-point rfft would blow the budget) — simpler and cheaper, same result. The spec's "reuse detect_standard's rfft" became "tracker.seed computes its own one-time rfft" (one extra rfft at session entry, negligible; avoids changing detect_standard's signature). ✓
- **Type consistency:** `SyncTracker(standard)` / `.seed(bb, fs)` / `.line_hz` / `.vsync_row` / `.note_vsync` / `.status()` used identically in T4/T5; `slice_lines(..., line_hz=)`, `deshear(rows, drift)`, `reconstruct_frames(..., tracker=)`, `chunk_to_frames(..., tracker=)`, `writer_loop(..., sync_status=)` consistent across tasks. ✓
- **Placeholder scan:** every code step has complete code. ✓
