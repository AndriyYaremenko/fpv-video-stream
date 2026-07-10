"""Continuous IQ -> grayscale frames for the SDR live-view stream.

Pure pieces (unit-tested): command builders, standard pick with PAL fallback,
row resize, chunk->frames. The subprocess pipeline (run_stream) is added on top
and kept as thin as possible."""
import logging
from collections import deque

import numpy as np

from demod import fm_demod, lowpass
from standard import detect_standard
from frame import reconstruct_frames
from render import normalize_luma
from sync_tracker import SyncTracker

LOG = logging.getLogger("video.stream")

VIEW_HEIGHT = {"PAL": 288, "NTSC": 240}
VIEW_CANVAS_HEIGHT = 288   # fixed encoder canvas (PAL field height); NTSC is row-resampled up


def build_capture_cmd(freq_hz, sample_rate_hz, lna=40, vga=20, amp=0):
    """hackrf_transfer argv streaming int8 IQ to stdout (no -n: runs until killed)."""
    return ["hackrf_transfer", "-r", "-", "-f", str(int(freq_hz)),
            "-s", str(int(sample_rate_hz)),
            "-l", str(int(lna)), "-g", str(int(vga)), "-a", str(int(amp))]


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


def pick_standard(baseband, fs, forced="auto", line_snr_db=10.0, harm_snr_db=6.0):
    """'pal'/'ntsc' forced -> that standard; otherwise detect, falling back to
    PAL so the stream still shows *something* on pure noise."""
    if forced in ("pal", "ntsc"):
        return forced.upper()
    res = detect_standard(baseband, fs, line_snr_db=line_snr_db, harm_snr_db=harm_snr_db)
    return res.standard or "PAL"


def resize_rows(img, height):
    """Nearest-row resample of a (rows, w) image to (height, w)."""
    if img.shape[0] == 0:
        return np.zeros((height, img.shape[1]), dtype=img.dtype)
    idx = np.clip(np.round(np.linspace(0, img.shape[0] - 1, height)).astype(int),
                  0, img.shape[0] - 1)
    return img[idx, :]


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


import subprocess
import threading
import time

CHUNK_S = 0.5


def iq_from_int8_fast(raw):
    """int8 IQ -> complex64 WITHOUT the /128 scale: one fewer pass over the chunk.
    FM demod, standard detection and per-frame luma normalization are all
    scale-invariant. The SCAN path keeps dweller.iq_from_int8 — its dBm
    features are calibrated to the /128 scale."""
    data = np.frombuffer(raw, dtype=np.int8).astype(np.float32)
    return data.view(np.complex64)


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

    def __len__(self):
        with self._lock:
            return len(self._d)


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

    def clear(self):
        with self._cond:
            self._d.clear()


def select_frames(frames, chunk_s, fps):
    """Even subsample so one chunk emits at most chunk_s*fps frames (pacing budget)."""
    want = max(1, int(round(chunk_s * fps)))
    if len(frames) <= want:
        return list(frames)
    idx = np.round(np.linspace(0, len(frames) - 1, want)).astype(int)
    return [frames[i] for i in idx]


class FramePacer:
    """Writes frames at a fixed fps so ffmpeg's rawvideo timeline stays real-time."""

    def __init__(self, fps, write, clock=None, sleep=None):
        self._period = 1.0 / fps
        self._write = write
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._next = None

    def tick(self, frame_bytes):
        now = self._clock()
        if self._next is None:
            self._next = now
        if self._next > now:
            self._sleep(self._next - now)
        self._write(frame_bytes)
        self._next = max(self._next + self._period, self._clock() - self._period)


def writer_loop(q, pacer, enc, stop_event, err, dropped_chunks=None, mailbox_len=None,
                sync_status=None, clock=None, log_every_s=10.0):
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
            st = sync_status() if sync_status is not None else None
            if st:
                vrow = st["vsync_row"] if st["vsync_row"] is not None else "-"
                sync = (" sync=H%.2f V%s line=%.0fHz"
                        % (st["line_hz"] / st["nominal"] - 1.0, vrow, st["line_hz"]))
            else:
                sync = ""
            LOG.info("view stream: %.1f fps, queue=%d, mailbox=%d, dropped_frames=%d, "
                     "dropped_chunks=%d%s",
                     fps, len(q), mailbox_len() if mailbox_len is not None else 0,
                     q.dropped, dropped_chunks() if dropped_chunks is not None else 0, sync)
            last_log = now
            last_written = written


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
                return                                   # EOF: capture died
            mailbox.put(buf)

    threading.Thread(target=_reader, daemon=True).start()
    t_end = clock() + max_s
    frame_budget = max(1, int(round(CHUNK_S * vcfg.view_fps)))   # never 0: a legal low fps must still stream
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
            iq = iq_from_int8_fast(buf)
            if standard is None:
                bb = lowpass(fm_demod(iq), fs, vcfg.lpf_cutoff_hz)
                standard = pick_standard(bb, fs, vcfg.view_standard,
                                         vcfg.line_snr_db, vcfg.harm_snr_db)
                height = VIEW_HEIGHT[standard]
                tracker = SyncTracker(standard)
                tracker.seed(bb, fs)         # bb is the demodulated first chunk used for detection
                enc = popen(build_encode_cmd(vcfg.view_push_url, vcfg.view_width,
                                             height, vcfg.view_fps),
                            stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
                q = FrameQueue(maxlen=int(vcfg.view_fps * 1.0))
                pacer = FramePacer(vcfg.view_fps, enc.stdin.write, clock=clock, sleep=sleep)
                writer = threading.Thread(
                    target=writer_loop, args=(q, pacer, enc, stop_event, err),
                    kwargs={"dropped_chunks": lambda: mailbox.dropped,
                            "mailbox_len": lambda: len(mailbox),
                            "sync_status": lambda: tracker.status(),
                            "clock": clock},
                    daemon=True)
                writer.start()
                LOG.info("view stream: %s %dx%d @%.0ffps", standard, vcfg.view_width,
                         height, vcfg.view_fps)
            for fr in select_frames(
                    chunk_to_frames(iq, fs, standard, vcfg.view_width, height,
                                    vcfg.lpf_cutoff_hz, vcfg.blank_frac,
                                    budget=frame_budget, tracker=tracker),
                    CHUNK_S, vcfg.view_fps):
                q.put(fr.tobytes())
        if error is None and err["msg"]:
            error = err["msg"]                           # writer failure surfaced after the loop
    finally:
        try:
            cap.kill()      # stop the reader FIRST: teardown must not inflate dropped_chunks
            cap.wait(timeout=5)
        except Exception:
            pass
        if q is not None:
            q.close()
        if writer is not None and err["msg"] is None and not stop_event.is_set():
            # Clean end (timeout / capture death): let the writer drain the tail.
            # NOTE: the reader/writer threads exit via EOF / closed-queue / EPIPE — NOT via
            # stop_event (the view controller may clear the shared event right after we
            # return); that guarantee holds only because cap/enc are killed before returning.
            writer.join(timeout=q.maxlen / vcfg.view_fps + 1.0)
        if error is None and not stop_event.is_set() and err["msg"]:
            error = err["msg"]                       # writer failed during/after the drain
        if enc is not None:
            try:
                enc.kill()
                enc.wait(timeout=5)
            except Exception:
                pass
    return error


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
