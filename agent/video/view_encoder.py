"""Persistent view encoder: ONE ffmpeg RTSP push that outlives view sessions.

The writer paces frames at a fixed fps forever: live frames while a session
feeds it, a freeze of the last live frame through retunes/short stalls, black
after idle() (session over / sweeping). The rawvideo timeline never stops, so
the MediaMTX path — and the dashboard's WHEP session — survive everything.
If ffmpeg dies it is respawned with backoff."""
import logging
import os
import subprocess
import threading
import time

from osd import IDLE_TEXT
from stream_demod import FrameQueue, FramePacer, build_encode_cmd, VIEW_CANVAS_HEIGHT

LOG = logging.getLogger("video.viewenc")


class ViewEncoder:
    def __init__(self, vcfg, popen=None, clock=None, sleep=None, log_every_s=10.0):
        self._vcfg = vcfg
        self._osd_file = vcfg.view_osd_file
        self._osd_font = vcfg.view_osd_font
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
        self._write_osd(IDLE_TEXT)

    def set_session_stats(self, fn):
        """fn() -> {'mailbox': int, 'dropped_chunks': int, 'sync': dict|None}."""
        self._stats = fn

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
            tmp = f"{self._osd_file}.{threading.get_ident()}.tmp"   # per-thread: concurrent writers never share a tmp
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, self._osd_file)      # atomic: drawtext never reads a partial line
        except Exception:
            LOG.exception("view OSD write failed")

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
            self._write_osd(IDLE_TEXT)           # textfile must exist before drawtext opens it
            enc = None
            try:
                enc = self._popen(
                    build_encode_cmd(self._vcfg.view_push_url, self._vcfg.view_width,
                                     VIEW_CANVAS_HEIGHT, self._vcfg.view_fps,
                                     osd_file=self._osd_file, osd_font=self._osd_font),
                    stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
            except Exception:
                LOG.exception("view encoder spawn failed")
            if enc is not None:
                t0 = self._clock()
                try:
                    self._run_writer(enc)
                except Exception:
                    LOG.exception("view writer crashed; treating as encoder death")
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
                st_fn = self._stats
                st = st_fn() if st_fn is not None else None
                if st is not None:
                    sync = ""
                    if st.get("sync"):
                        s = st["sync"]
                        vrow = s["vsync_row"] if s["vsync_row"] is not None else "-"
                        sync = (" sync=H%.2f V%s line=%.0fHz"
                                % (s["line_hz"] / s["nominal"] - 1.0, vrow, s["line_hz"]))
                    LOG.info("view stream: %.1f fps, queue=%d, mailbox=%d, dropped_frames=%d, "
                             "dropped_chunks=%d, repeats=%d%s",
                             (written - last_written) / max(now - last_log, 1e-9), len(self._q),
                             st["mailbox"], self._q.dropped,
                             st["dropped_chunks"], self.repeats, sync)
                last_log = now
                last_written = written
