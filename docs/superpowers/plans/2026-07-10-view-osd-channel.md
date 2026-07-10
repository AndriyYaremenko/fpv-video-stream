# View OSD Channel/Frequency Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Burn a top-right OSD label (`3470 MHz · PAL`, or `5800 MHz F4 · PAL` when the frequency maps to an FPV channel) into the persistent view stream, updating on every session start and retune with no encoder restart.

**Architecture:** Reuse the RX5808 grabber's proven OSD pattern — the agent atomically rewrites a small text file, ffmpeg draws it via `drawtext=…:textfile=…:reload=1`. A pure formatter builds the label; `build_encode_cmd` gains the filter; `ViewEncoder` owns the file; the session demod loops set the text.

**Tech Stack:** Python 3.13 (numpy, stdlib), pytest; ffmpeg/libx264 drawtext filter.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-10-view-osd-channel-design.md`.
- OSD label format: `"<freq> MHz"`, then ` <channel>` when a channel is given, then ` · <standard>` when a standard is given. Frequency is rounded to a whole MHz. Idle label is the constant `"—"` (em dash, U+2014).
- drawtext placement: `x=w-tw-10:y=10` (top-right), `fontsize=18`, `fontcolor=white`, `box=1:boxcolor=black@0.5:boxborderw=6`.
- `DEFAULT_OSD_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"` (the exact font the RX5808 grabber uses on the Pi).
- Default OSD file `/run/fpv/view-osd.txt`; `VIEW_OSD_FILE=""` disables the feature entirely.
- `build_encode_cmd(..., osd_file=None)` with `osd_file` falsy → NO `-vf` filter added → the legacy `run_stream` encoder and every existing test are byte-for-byte unchanged.
- Only the persistent engine gets OSD; legacy `run_stream` is untouched. The demod/capture pipeline is untouched.
- File writes are atomic: write `<file>.tmp` then `os.replace` (mirror `agent/scan/rx5808_controller.py::Rx5808Controller._write_osd`) so `drawtext` never reads a partial line.
- Python tests: `cd agent/video && python -m pytest tests -q` and `cd agent/scan && python -m pytest tests -q`. Run the full relevant suite before every commit.
- Branch: `feat/view-osd-channel` (off `main`, already checked out). Open one PR against `main` at the end.
- Commit messages end with the trailer:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01AoKgEkvkesR7Y1KjQWekB8`

---

### Task 1: `osd_text` formatter + constants

**Files:**
- Create: `agent/video/osd.py`
- Test: `agent/video/tests/test_osd.py`

**Interfaces:**
- Produces (used by Tasks 2, 3, 4, 5):
  - `osd.DEFAULT_OSD_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"`
  - `osd.IDLE_TEXT = "—"`
  - `osd.osd_text(freq_mhz, standard=None, channel=None) -> str`

- [ ] **Step 1: Write the failing test**

Create `agent/video/tests/test_osd.py`:

```python
from osd import osd_text, IDLE_TEXT, DEFAULT_OSD_FONT


def test_osd_text_formats():
    assert osd_text(3470) == "3470 MHz"
    assert osd_text(3470.4) == "3470 MHz"          # rounds to whole MHz
    assert osd_text(3470, "PAL") == "3470 MHz · PAL"
    assert osd_text(5800, "PAL", "F4") == "5800 MHz F4 · PAL"
    assert osd_text(5800, None, "F4") == "5800 MHz F4"


def test_osd_constants():
    assert IDLE_TEXT == "—"
    assert DEFAULT_OSD_FONT.endswith("DejaVuSans-Bold.ttf")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent/video && python -m pytest tests/test_osd.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'osd'`

- [ ] **Step 3: Write the implementation**

Create `agent/video/osd.py`:

```python
"""OSD label formatting for the view stream (drawn by ffmpeg drawtext).

The persistent encoder's ffmpeg reads a reload=1 textfile the agent rewrites
per session/retune; this module builds that one line."""

IDLE_TEXT = "—"
DEFAULT_OSD_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def osd_text(freq_mhz, standard=None, channel=None):
    """One-line label: '<freq> MHz [<channel>] [· <standard>]'.
    e.g. osd_text(5800, 'PAL', 'F4') -> '5800 MHz F4 · PAL'."""
    label = f"{int(round(freq_mhz))} MHz"
    if channel:
        label += f" {channel}"
    if standard:
        label += f" · {standard}"
    return label
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd agent/video && python -m pytest tests/test_osd.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/video/osd.py agent/video/tests/test_osd.py
git commit -m "feat(view): osd_text label formatter + OSD constants"
```

---

### Task 2: `build_encode_cmd` drawtext filter

**Files:**
- Modify: `agent/video/stream_demod.py:30-38` (`build_encode_cmd`)
- Test: `agent/video/tests/test_stream_demod.py`

**Interfaces:**
- Consumes: `osd.DEFAULT_OSD_FONT` (Task 1).
- Produces: `build_encode_cmd(push_url, width, height, fps, osd_file=None, osd_font=DEFAULT_OSD_FONT)`. With falsy `osd_file`, argv is unchanged from today. With `osd_file` set, argv gains a `-vf drawtext=…` entry (Task 4 passes these from vcfg).

- [ ] **Step 1: Write the failing tests**

Append to `agent/video/tests/test_stream_demod.py` (near `test_encode_cmd_rawgray_to_rtsp`):

```python
def test_encode_cmd_no_osd_by_default():
    cmd = build_encode_cmd("rtsp://u:p@h:8554/s", 360, 288, 15)
    assert "-vf" not in cmd and not any("drawtext" in a for a in cmd)


def test_encode_cmd_with_osd_drawtext():
    cmd = build_encode_cmd("rtsp://u:p@h:8554/s", 360, 288, 15,
                           osd_file="/run/fpv/view-osd.txt", osd_font="/f/Font.ttf")
    vf = cmd[cmd.index("-vf") + 1]
    assert vf.startswith("drawtext=")
    assert "textfile=/run/fpv/view-osd.txt" in vf and "reload=1" in vf
    assert "fontfile=/f/Font.ttf" in vf
    assert "x=w-tw-10:y=10" in vf and "fontsize=18" in vf
    # filter sits between the input and the codec
    assert cmd.index("-vf") > cmd.index("-i") and cmd.index("-vf") < cmd.index("-c:v")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent/video && python -m pytest tests/test_stream_demod.py -k "osd" -v`
Expected: FAIL — `build_encode_cmd()` got an unexpected keyword argument `osd_file`.

- [ ] **Step 3: Implement**

In `agent/video/stream_demod.py`, add near the top imports (with the other `from … import …` lines):

```python
from osd import DEFAULT_OSD_FONT
```

Replace `build_encode_cmd`:

```python
def build_encode_cmd(push_url, width, height, fps, osd_file=None, osd_font=DEFAULT_OSD_FONT):
    """ffmpeg argv: raw gray frames on stdin -> low-latency H.264 RTSP push.
    -g fps = an IDR every ~1 s so a WHEP viewer joining mid-stream decodes
    within a second (libx264's default 250-frame GOP is ~17 s at 15 fps).
    osd_file (when set) burns a top-right reload=1 drawtext label into the video."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{width}x{height}",
           "-r", str(fps), "-i", "-"]
    if osd_file:
        cmd += ["-vf",
                (f"drawtext=fontfile={osd_font}:textfile={osd_file}:reload=1"
                 ":x=w-tw-10:y=10:fontsize=18:fontcolor=white"
                 ":box=1:boxcolor=black@0.5:boxborderw=6")]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
            "-g", str(max(1, int(round(fps)))),
            "-pix_fmt", "yuv420p", "-f", "rtsp", "-rtsp_transport", "tcp", push_url]
    return cmd
```

- [ ] **Step 4: Run the video suite**

Run: `cd agent/video && python -m pytest tests -q`
Expected: all PASS (existing `test_encode_cmd_rawgray_to_rtsp` and `test_encode_cmd_short_gop` still green — they don't pin argv length or the absence of `-vf`).

- [ ] **Step 5: Commit**

```bash
git add agent/video/stream_demod.py agent/video/tests/test_stream_demod.py
git commit -m "feat(view): optional drawtext OSD filter in build_encode_cmd"
```

---

### Task 3: OSD config knobs in `vconfig`

**Files:**
- Modify: `agent/video/vconfig.py`
- Test: `agent/video/tests/test_vconfig.py`

**Interfaces:**
- Consumes: `osd.DEFAULT_OSD_FONT` (Task 1).
- Produces: `VideoConfig.view_osd_file: str = "/run/fpv/view-osd.txt"` (env `VIEW_OSD_FILE`, empty disables) and `VideoConfig.view_osd_font: str = DEFAULT_OSD_FONT` (env `VIEW_OSD_FONT`). Tasks 4/5 read these.

- [ ] **Step 1: Write the failing test**

Append to `agent/video/tests/test_vconfig.py`:

```python
def test_view_osd_config_defaults_and_env():
    from vconfig import load_video_config
    from osd import DEFAULT_OSD_FONT
    c = load_video_config({})
    assert c.view_osd_file == "/run/fpv/view-osd.txt"
    assert c.view_osd_font == DEFAULT_OSD_FONT
    c2 = load_video_config({"VIEW_OSD_FILE": "/tmp/x.txt", "VIEW_OSD_FONT": "/f/A.ttf"})
    assert c2.view_osd_file == "/tmp/x.txt" and c2.view_osd_font == "/f/A.ttf"
    assert load_video_config({"VIEW_OSD_FILE": ""}).view_osd_file == ""   # disable
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent/video && python -m pytest tests/test_vconfig.py::test_view_osd_config_defaults_and_env -v`
Expected: FAIL — `VideoConfig` has no attribute `view_osd_file`.

- [ ] **Step 3: Implement**

In `agent/video/vconfig.py`, add the import at the top (after the existing `import os` / `from dataclasses import dataclass`):

```python
from osd import DEFAULT_OSD_FONT
```

Add the two fields to the dataclass, right after `view_engine`:

```python
    view_osd_file: str = "/run/fpv/view-osd.txt"   # reload=1 drawtext textfile; "" disables OSD
    view_osd_font: str = DEFAULT_OSD_FONT
```

Add to `load_video_config`, after the `VIEW_ENGINE` block:

```python
    c.view_osd_file = env.get("VIEW_OSD_FILE", c.view_osd_file)
    c.view_osd_font = env.get("VIEW_OSD_FONT", c.view_osd_font)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd agent/video && python -m pytest tests/test_vconfig.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/video/vconfig.py agent/video/tests/test_vconfig.py
git commit -m "feat(view): VIEW_OSD_FILE / VIEW_OSD_FONT config knobs"
```

---

### Task 4: `ViewEncoder` owns the OSD file

**Files:**
- Modify: `agent/video/view_encoder.py`
- Test: `agent/video/tests/test_view_encoder.py`

**Interfaces:**
- Consumes: `osd.IDLE_TEXT` (Task 1); `vcfg.view_osd_file`, `vcfg.view_osd_font` (Task 3); `build_encode_cmd(..., osd_file, osd_font)` (Task 2).
- Produces (used by Task 5): `ViewEncoder.set_osd(text)` — atomic write of `text` to the OSD file (no-op when the file is disabled). `idle()` also writes `IDLE_TEXT`. `_supervise` writes `IDLE_TEXT` before each ffmpeg spawn and passes `osd_file`/`osd_font` to `build_encode_cmd`.

- [ ] **Step 1: Write the failing tests**

In `agent/video/tests/test_view_encoder.py`, update the `_vcfg` helper to accept an OSD file (existing callers keep OSD disabled via the `""` default, so their behavior is unchanged):

```python
def _vcfg(width=4, fps=50.0, osd_file=""):
    c = VideoConfig()
    c.view_push_url = "rtsp://u:p@10.8.0.1:8554/hackrf-view"
    c.view_width = width
    c.view_fps = fps
    c.view_osd_file = osd_file
    return c
```

Append these tests:

```python
def test_set_osd_and_idle_write_the_file(tmp_path):
    osd = tmp_path / "osd.txt"
    ve = ViewEncoder(_vcfg(osd_file=str(osd)))
    ve.set_osd("947 MHz · PAL")
    assert osd.read_text(encoding="utf-8") == "947 MHz · PAL"
    ve.idle()
    assert osd.read_text(encoding="utf-8") == "—"


def test_supervise_writes_idle_osd_and_adds_vf_before_spawn(tmp_path):
    osd = tmp_path / "osd.txt"
    clk = _Clock()
    ve = ViewEncoder(_vcfg(osd_file=str(osd)), clock=clk, sleep=clk.sleep)

    def popen(cmd, **kw):
        assert "-vf" in cmd                      # OSD enabled -> drawtext filter in argv
        ve._stop.set()
        return _FakeEnc()
    ve._popen = popen
    ve._supervise()
    assert osd.read_text(encoding="utf-8") == "—"   # file exists before ffmpeg opens it


def test_osd_disabled_is_noop_and_no_vf(tmp_path):
    ve = ViewEncoder(_vcfg(osd_file=""))         # disabled
    ve.set_osd("947 MHz")                          # must not raise, must not create a file
    seen = {}

    def popen(cmd, **kw):
        seen["vf"] = "-vf" in cmd
        ve._stop.set()
        return _FakeEnc()
    ve._popen = popen
    ve._supervise()
    assert seen["vf"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent/video && python -m pytest tests/test_view_encoder.py -k "osd" -v`
Expected: FAIL — `ViewEncoder` has no attribute `set_osd`.

- [ ] **Step 3: Implement**

In `agent/video/view_encoder.py`:

Add imports at the top (with the existing `import logging` etc.):

```python
import os
```

and next to `from stream_demod import …`:

```python
from osd import IDLE_TEXT
```

In `__init__`, after `self._vcfg = vcfg`, capture the OSD config:

```python
        self._osd_file = vcfg.view_osd_file
        self._osd_font = vcfg.view_osd_font
```

Add the writer methods (after `set_session_stats`):

```python
    def set_osd(self, text):
        """Atomically publish the OSD label the drawtext filter reloads."""
        self._write_osd(text)

    def _write_osd(self, text):
        if not self._osd_file:
            return
        try:
            d = os.path.dirname(self._osd_file)
            if d:
                os.makedirs(d, exist_ok=True)
            tmp = self._osd_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, self._osd_file)      # atomic: drawtext never reads a partial line
        except Exception:
            LOG.exception("view OSD write failed")
```

In `idle()`, add the idle-label write (keep the existing three lines):

```python
    def idle(self):
        """Session over: drop the freeze frame and stale queue -> black."""
        self._last = None
        self._stats = None
        self._q.clear()
        self._write_osd(IDLE_TEXT)
```

In `_supervise`, inside the `while` loop before the `enc = None` / spawn, ensure the textfile exists, and pass the OSD args to `build_encode_cmd`:

```python
    def _supervise(self):
        backoff = 1.0
        while not self._stop.is_set():
            self._write_osd(IDLE_TEXT)           # textfile must exist before drawtext opens it
            enc = None
            try:
                enc = self._popen(
                    build_encode_cmd(self._vcfg.view_push_url, self._vcfg.view_width,
                                     VIEW_CANVAS_HEIGHT, self._vcfg.view_fps,
                                     osd_file=self._osd_file, osd_font=self._osd_font),
                    stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
```

(keep the rest of `_supervise` unchanged.)

- [ ] **Step 4: Run the video suite**

Run: `cd agent/video && python -m pytest tests -q`
Expected: all PASS (existing ViewEncoder tests use `osd_file=""` → `_write_osd` is a no-op, argv has no `-vf`).

- [ ] **Step 5: Commit**

```bash
git add agent/video/view_encoder.py agent/video/tests/test_view_encoder.py
git commit -m "feat(view): ViewEncoder owns the OSD textfile (idle label + spawn-time ensure)"
```

---

### Task 5: Session loops set the OSD text

**Files:**
- Modify: `agent/video/stream_demod.py` (`run_stream_persistent`, `run_stream_source`, add `_osd_for` helper)
- Test: `agent/video/tests/test_stream_demod.py`

**Interfaces:**
- Consumes: `osd.osd_text` (Task 1); `encoder.set_osd(text)` (Task 4).
- Produces: `run_stream_persistent(vcfg, freq_mhz, stop_event, max_s, encoder, lna=40, vga=20, amp=0, popen=None, clock=None, sleep=None, channel_of=None)` and `run_stream_source(vcfg, source, freq_mhz, stop_event, max_s, encoder, clock=None, channel_of=None)` — each sets the freq-only OSD at session entry and the full freq+standard(+channel) OSD after standard detection. `channel_of` is a `freq_mhz -> name|None` callable (Task 6 passes `nearest_channel`).

- [ ] **Step 1: Write the failing tests**

In `agent/video/tests/test_stream_demod.py`, extend the `_FakeEncoder` class (add the `osd_calls` list + `set_osd`):

```python
class _FakeEncoder:
    def __init__(self):
        self.frames = []
        self.stats_fn = None
        self.osd_calls = []
    def submit(self, fr):
        self.frames.append(fr)
    def set_session_stats(self, fn):
        self.stats_fn = fn
    def set_osd(self, text):
        self.osd_calls.append(text)
```

Append these tests:

```python
def test_run_stream_persistent_sets_osd_freq_then_full():
    fs = 4e6

    def popen(cmd, **kw):
        return _FakeProc(stdout=io.BytesIO(_chunk_bytes(fs) * 2))

    fenc = _FakeEncoder()
    t = [0.0]
    run_stream_persistent(_vcfg(), 947.0, threading.Event(), max_s=60.0, encoder=fenc,
                          popen=popen, clock=lambda: t[0],
                          sleep=lambda s: t.__setitem__(0, t[0] + max(s, 0.01)),
                          channel_of=lambda f: "F4")
    assert fenc.osd_calls[0] == "947 MHz F4"                 # freq (+channel) at entry
    assert "947 MHz F4 · PAL" in fenc.osd_calls              # full after standard detect


def test_run_stream_source_sets_osd_freq_then_full():
    fs = 4e6
    stop = threading.Event()
    src = _FakeSource([bytes(_chunk_bytes(fs))], stop=stop, stop_after=1)
    fenc = _FakeEncoder()
    run_stream_source(_vcfg(), src, 947.0, stop, max_s=60.0, encoder=fenc,
                      clock=lambda: 0.0, channel_of=None)
    assert fenc.osd_calls[0] == "947 MHz"                    # no channel_of -> freq only
    assert "947 MHz · PAL" in fenc.osd_calls                 # full after standard detect
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent/video && python -m pytest tests/test_stream_demod.py -k "sets_osd" -v`
Expected: FAIL — `run_stream_persistent()` got an unexpected keyword argument `channel_of`.

- [ ] **Step 3: Implement**

In `agent/video/stream_demod.py`, add to the imports near the top (with `from osd import DEFAULT_OSD_FONT` from Task 2):

```python
from osd import DEFAULT_OSD_FONT, osd_text
```

Add a small helper (place it just above `run_stream_persistent`):

```python
def _osd_for(freq_mhz, standard, channel_of):
    ch = channel_of(freq_mhz) if channel_of else None
    return osd_text(freq_mhz, standard, ch)
```

In `run_stream_persistent`, add `channel_of=None` to the signature (last parameter):

```python
def run_stream_persistent(vcfg, freq_mhz, stop_event, max_s, encoder,
                          lna=40, vga=20, amp=0, popen=None, clock=None, sleep=None,
                          channel_of=None):
```

After `frame_budget = max(1, int(round(CHUNK_S * vcfg.view_fps)))` and before `try:`, add the entry OSD:

```python
    encoder.set_osd(_osd_for(freq_mhz, None, channel_of))
```

Inside the `if standard is None:` block, after the `LOG.info("view stream: %s -> …")` line, add the full OSD:

```python
                encoder.set_osd(_osd_for(freq_mhz, standard, channel_of))
```

In `run_stream_source`, add `channel_of=None` to the signature (last parameter):

```python
def run_stream_source(vcfg, source, freq_mhz, stop_event, max_s, encoder, clock=None,
                      channel_of=None):
```

After `frame_budget = max(1, int(round(CHUNK_S * vcfg.view_fps)))` (which is right after the `try/except` around `source.tune`) and before the `while` loop, add the entry OSD:

```python
    encoder.set_osd(_osd_for(freq_mhz, None, channel_of))
```

Inside its `if standard is None:` block, after the `LOG.info("view stream: %s -> … (in-process capture)")` line, add the full OSD:

```python
            encoder.set_osd(_osd_for(freq_mhz, standard, channel_of))
```

- [ ] **Step 4: Run the video suite**

Run: `cd agent/video && python -m pytest tests -q`
Expected: all PASS (the extended `_FakeEncoder` now answers `set_osd`, so the pre-existing `run_stream_*` tests keep working).

- [ ] **Step 5: Commit**

```bash
git add agent/video/stream_demod.py agent/video/tests/test_stream_demod.py
git commit -m "feat(view): session loops publish freq/channel/standard to the OSD"
```

---

### Task 6: Wire `channel_of` in `main.py` + open PR

**Files:**
- Modify: `agent/scan/main.py:278-284` (the two persistent-engine `run` lambdas)

**Interfaces:**
- Consumes: `run_stream_persistent(..., channel_of=…)`, `run_stream_source(..., channel_of=…)` (Task 5); `nearest_channel` (already imported at the top of `main.py`).
- Produces: production view sessions pass `channel_of=nearest_channel`, so the OSD shows the FPV channel when the frequency maps to one.

- [ ] **Step 1: Implement (glue — covered by the suites + live acceptance, no new unit test)**

In `agent/scan/main.py`, in the `if cfg.sdr == "hackrf" and cfg.source == "live":` branch, update the source `run` lambda:

```python
                        run = lambda freq, stop, max_s: stream_demod.run_stream_source(
                            viewcfg, source, freq, stop, max_s, encoder,
                            channel_of=nearest_channel)
```

and in the `else:` branch under `if viewcfg.view_engine == "persistent":`, update the persistent `run` lambda:

```python
                        run = lambda freq, stop, max_s: stream_demod.run_stream_persistent(
                            viewcfg, freq, stop, max_s, encoder,
                            lna=cfg.lna_gain, vga=cfg.vga_gain, amp=cfg.amp_enable,
                            channel_of=nearest_channel)
```

(The legacy `run_stream` lambda is unchanged — no OSD there.)

- [ ] **Step 2: Sanity-run both suites + byte-compile**

Run: `cd agent/scan && python -m pytest tests -q && cd ../video && python -m pytest tests -q && python -m py_compile agent/scan/main.py`

Wait — run `py_compile` from the repo root:

Run: `cd agent/scan && python -m pytest tests -q && cd ../.. && python -m py_compile agent/scan/main.py && cd agent/video && python -m pytest tests -q`
Expected: all PASS, compile clean.

- [ ] **Step 3: Commit**

```bash
git add agent/scan/main.py
git commit -m "feat(view): pass channel_of=nearest_channel so the OSD shows the FPV channel"
```

- [ ] **Step 4: Push and open the PR**

```bash
git push -u origin feat/view-osd-channel
gh pr create --base main --title "feat(view): OSD channel/frequency overlay on the view stream" --body "docs/superpowers/specs/2026-07-10-view-osd-channel-design.md: burns a top-right label (e.g. \`3470 MHz · PAL\`, or \`5800 MHz F4 · PAL\`) into the persistent view stream via ffmpeg drawtext + reload textfile — the same pattern as the RX5808 grabber OSD. Updates on session start/retune with no encoder restart; \`VIEW_OSD_FILE=\"\"\` disables. Legacy engine untouched.

Deploy: restart fpv-scan-hackrf (encoder argv gains -vf); default /run/fpv/view-osd.txt needs no env change.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01AoKgEkvkesR7Y1KjQWekB8"
```

- [ ] **Step 5: Live acceptance (on the Pi, after merge + deploy)**

Deploy: `ssh andriy@192.168.1.204`, `sudo git -C /opt/fpv-video-stream pull`, `sudo systemctl restart fpv-scan-hackrf`. Then:
- [ ] Idle: `cat /run/fpv/view-osd.txt` → `—`; dashboard view panel (before a session) shows nothing burned in over black.
- [ ] Start a view session at a mapped freq (e.g. 5800) → OSD shows `5800 MHz F4 · PAL` (or `· NTSC`) top-right within ~1 s.
- [ ] Start at an off-band freq (e.g. 3470) → OSD shows `3470 MHz · <standard>` (no channel).
- [ ] Retune to another freq → OSD updates within ~1 s, no encoder restart (`journalctl -u fpv-scan-hackrf` shows no new "view encoder" spawn).
- [ ] Stop / return to sweep → OSD returns to `—`.
- [ ] Font sanity: no ffmpeg respawn loop in the journal (confirms the DejaVu font path resolved).

---

## Plan self-review notes (already applied)

- Spec coverage: rendering via drawtext+reload (T2), `osd_text` format incl. idle `—` (T1), config file/font + disable (T3), ViewEncoder file ownership + spawn-time ensure + idle write (T4), session-loop text on entry+detect with `channel_of` threading (T5), main.py wiring with `nearest_channel` (T6). Legacy untouched (T2 default `osd_file=None`). Deploy + risks in the spec map to T6 Step 5.
- Type consistency: `osd_text(freq_mhz, standard=None, channel=None)`, `set_osd(text)`, `_osd_for(freq_mhz, standard, channel_of)`, `channel_of(freq_mhz) -> str|None`, `build_encode_cmd(..., osd_file=None, osd_font=DEFAULT_OSD_FONT)` — consistent across T1–T6. `IDLE_TEXT`/`DEFAULT_OSD_FONT` sourced once in `osd.py`, imported by stream_demod/vconfig/view_encoder (no cycle — `osd.py` imports nothing).
- No placeholders: every code step shows complete code; every run step names the command and expected result.
