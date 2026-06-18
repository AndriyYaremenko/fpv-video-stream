import logging
import os
import sys

# Expose the agent/video flat modules (pipeline, render, vconfig) to this scan-side bridge.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "video")))

from pipeline import extract_frame                  # noqa: E402  (agent/video core)
from render import save_full_png, thumbnail_b64     # noqa: E402

LOG = logging.getLogger("scan.video")


class VideoEmitter:
    """Bridge: run the video pipeline on an already-captured candidate IQ and publish a
    frame over the live MqttPublisher, throttled per channel. Never raises into the caller."""

    def __init__(self, publisher, vcfg, cooldown_s):
        self.publisher = publisher
        self.vcfg = vcfg
        self.cooldown_s = cooldown_s
        self._last = {}              # round(center_mhz, 1) -> last attempt ts

    def maybe_emit(self, iq, fs, center_mhz, now_ts):
        key = round(center_mhz, 1)
        last = self._last.get(key)
        if last is not None and now_ts - last < self.cooldown_s:
            return "cooldown"
        self._last[key] = now_ts     # throttle reprocessing regardless of outcome
        try:
            vf = extract_frame(iq, fs, center_mhz * 1e6, self.vcfg)
        except Exception:
            LOG.exception("video extract failed for %.1f MHz", center_mhz)
            return "error"
        if vf.standard is None:
            return "not_video"
        if vf.luma is None:
            return "no_lines"
        try:
            path = os.path.join(self.vcfg.frames_dir, f"{int(now_ts)}_{int(round(center_mhz))}.png")
            save_full_png(vf.luma, path)
        except Exception:
            LOG.exception("video frame save failed for %.1f MHz", center_mhz)
        try:
            thumb = thumbnail_b64(vf.luma, self.vcfg.thumb_max_width)
            if self.publisher is not None:
                self.publisher.publish_video(now_ts, center_mhz, vf.standard, vf.line_hz,
                                             round(float(vf.sync_snr_db), 1), thumb)
        except Exception:
            LOG.exception("video publish failed for %.1f MHz", center_mhz)
            return "error"
        LOG.info("video status=published center_mhz=%.1f standard=%s sync_snr_db=%.1f",
                 center_mhz, vf.standard, vf.sync_snr_db)
        return "published"
