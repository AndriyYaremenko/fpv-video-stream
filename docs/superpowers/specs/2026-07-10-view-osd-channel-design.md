# View OSD channel/frequency overlay — Design

**Date:** 2026-07-10
**Status:** Approved design, ready to implement (own branch off `main`: `feat/view-osd-channel`).
**Target:** scanner Pi (hackrf view agent `fpv-scan-hackrf.service`).
**Context:** follows the view fast-start work (spec `2026-07-10-view-fast-start`, PRs #27/#28/#29).
The persistent view encoder is live; the operator now wants the frequency/channel actually being
streamed burned into the view video, mirroring the existing RX5808 grabber OSD.

## Problem
The SDR view stream shows the demodulated picture but no indication of which frequency it is
tuned to. On the dashboard the frequency lives only in a side badge; the video itself is
context-free. When the operator flips between detections they cannot tell from the picture alone
what they are looking at.

The codebase already solves the identical problem for the RX5808 grabber: `Rx5808Controller`
atomically writes the current channel to `/run/fpv/rx5808.txt` and its ffmpeg pushes it with
`drawtext=…:textfile=…:reload=1`. This design reuses that pattern for the view stream.

## Goal & non-goals
- **Goal:** burn a top-right OSD label into the persistent view stream showing the frequency being
  streamed, the mapped FPV channel when the frequency falls on one, and the detected video
  standard — e.g. `3470 MHz · PAL` or `5800 MHz F4 · PAL`. Always present: during a live session the
  label shows the tuned freq; while idle/sweeping it shows `—`. Updates on every session start and
  retune with no encoder restart.
- **Non-goals:** OSD on the `legacy` engine (rollback-only, out of scope — no drawtext there);
  OSD on the scan-side snapshot/grabber paths (unchanged); colour/positioning UI controls on the
  dashboard; per-viewer OSD toggling; changing the demod or capture pipeline.

## Design

### Rendering — ffmpeg `drawtext` + reload textfile
The persistent encoder's ffmpeg never restarts, so the frequency cannot be baked into argv (it
changes per session/retune). A `drawtext` filter reading a `reload=1` textfile is exactly the
mechanism the RX5808 grabber already uses. The agent rewrites the textfile atomically
(`tmp` + `os.replace`) so `drawtext` never reads a partial line.

### `agent/video/osd.py` (new) — `osd_text(freq_mhz, standard=None, channel=None) -> str`
Pure formatter (unit-tested):
- `osd_text(3470)` → `"3470 MHz"`
- `osd_text(3470, "PAL")` → `"3470 MHz · PAL"`
- `osd_text(5800, "PAL", "F4")` → `"5800 MHz F4 · PAL"`
- `osd_text(5800, None, "F4")` → `"5800 MHz F4"`

Frequency is rounded to a whole MHz. `channel` (when non-empty) follows the frequency; `standard`
(when non-empty) is appended after a ` · ` separator. The idle label `"—"` is a constant, not
produced by this helper.

### `agent/video/stream_demod.py` — `build_encode_cmd(..., osd_file=None, osd_font=DEFAULT_OSD_FONT)`
When `osd_file` is set, insert a video filter before the codec options:

```
-vf drawtext=fontfile=<osd_font>:textfile=<osd_file>:reload=1:x=w-tw-10:y=10:fontsize=18:fontcolor=white:box=1:boxcolor=black@0.5:boxborderw=6
```

`x=w-tw-10:y=10` = top-right, matching the RX5808 grabber. `fontsize=18` is proportional to the
360×288 canvas (the grabber uses 24 on 720×576). `box=1:boxcolor=black@0.5` keeps the text readable
over any picture. `osd_file=None` (the default) adds no filter, so the legacy `run_stream` encoder
and every existing test are byte-for-byte unchanged. `DEFAULT_OSD_FONT =
"/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"` — the exact font the RX5808 grabber already
uses on this Pi.

### `agent/video/view_encoder.py` — `ViewEncoder` owns the OSD file
The encoder owns ffmpeg and the `drawtext` textfile reference, so it owns the file:
- `__init__` takes `osd_file` and `osd_font` from `vcfg`; passes them to `build_encode_cmd`.
- `_write_osd(text)`: atomic `tmp` + `os.replace` write (same helper shape as
  `Rx5808Controller._write_osd`); no-op when `osd_file` is empty.
- Before **each** ffmpeg spawn in `_supervise`, write the idle label `"—"` so the textfile always
  exists before `drawtext` opens it (a missing textfile makes ffmpeg fail at startup → respawn
  loop). Idempotent.
- `set_osd(text)`: public, atomic write — called by the session loop.
- `idle()`: already clears the freeze frame + queue; also writes the idle label `"—"`.

### Session loops set the text
`run_stream_persistent` and `run_stream_source` know `freq_mhz` immediately and `standard` after the
first chunk. Both gain an optional `channel_of=None` parameter (a `freq_mhz -> name|None` callable):
- At session entry: `encoder.set_osd(osd_text(freq_mhz, None, channel_of and channel_of(freq_mhz)))`
  — the frequency (and channel if mapped) appears at once, before the standard is known.
- Immediately after standard detection (the existing `if standard is None:` block): re-write with
  the full `osd_text(freq_mhz, standard, channel)`.

On retune the session function is re-entered with the new `freq_mhz` (both engines — `run_view`
loops and calls the run lambda again), so the OSD updates on every retune with no extra wiring.
`channel_of=None` → no channel segment (keeps `agent/video` free of any `agent/scan` import).

### `agent/scan/main.py` — wiring
The view-init block already builds the `run` lambda and constructs `ViewEncoder`. Wire:
- `channel_of=nearest_channel` (imported from `channel_map`, already in `agent/scan`) into the
  `run_stream_persistent` / `run_stream_source` calls.
- `ViewEncoder(viewcfg)` reads the OSD config off `viewcfg` (no signature change at the call site).

### `agent/video/vconfig.py` — config
- `view_osd_file: str = "/run/fpv/view-osd.txt"`, env `VIEW_OSD_FILE`. Empty string disables OSD.
- `view_osd_font: str = DEFAULT_OSD_FONT`, env `VIEW_OSD_FONT`.

## Data flow
```
session start / retune (freq_mhz, later standard)
      │  channel_of(freq_mhz)  ─┐
      ▼                         ▼
run_stream_persistent/source → encoder.set_osd(osd_text(...))
                                     │  atomic tmp+replace
                                     ▼
                            /run/fpv/view-osd.txt  ──reload=1──►  ffmpeg drawtext (top-right)
idle() / session end ─────────────► "—"
```

## Testing
- `osd_text`: the four format cases above (freq-only, freq+standard, freq+channel+standard,
  freq+channel).
- `build_encode_cmd`: with `osd_file` → argv contains `-vf` with `drawtext`, `textfile=<file>`,
  `reload=1`, `x=w-tw-10`, the font path; without `osd_file` → no `drawtext` (legacy argv unchanged).
- `ViewEncoder`: `set_osd` writes the exact bytes atomically; `_supervise` writes `"—"` before the
  first spawn (textfile exists); `idle()` writes `"—"`; empty `osd_file` → `_write_osd` is a no-op
  and no `-vf` in the spawned argv.
- `vconfig`: `VIEW_OSD_FILE` / `VIEW_OSD_FONT` env parsing incl. empty-string disable.
- `run_stream_persistent` / `run_stream_source`: a fake encoder records `set_osd` calls — assert the
  freq-only text at entry and the full freq+standard(+channel) text after standard detection, and
  that `channel_of` is consulted.

## Deploy
The persistent encoder's ffmpeg argv changes (adds `-vf`), so the encoder must restart to pick it
up: `systemctl restart fpv-scan-hackrf`. Default `VIEW_OSD_FILE=/run/fpv/view-osd.txt` needs no env
change (`/run/fpv/` already exists — the RX5808 grabber writes there). Verify on the Pi that the
DejaVu font path exists; if not, set `VIEW_OSD_FONT` or disable with `VIEW_OSD_FILE=""`.

## Risks
- **Missing font → ffmpeg respawn loop.** The default DejaVu-Bold is already used by the live RX5808
  grabber on this Pi, so it is present; the path is configurable and `VIEW_OSD_FILE=""` fully
  disables the feature as an escape hatch. Beyond that, the encoder now degrades gracefully: if the
  configured font file is absent, `_supervise` logs a one-time `LOG.warning` and skips the overlay
  (no `-vf`) for that run, so a missing font can never loop the stream. Separately, the pre-spawn
  textfile write uses the current session label (`self._osd_text`), not the idle placeholder, so a
  mid-session ffmpeg respawn preserves the live label instead of reverting to `"—"`.
- **`/run/fpv/` not writable at boot.** Same directory the RX5808 OSD already uses successfully; the
  atomic writer `makedirs(exist_ok=True)` first, matching `Rx5808Controller._write_osd`.
