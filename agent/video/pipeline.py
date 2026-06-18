from dataclasses import dataclass
from typing import Optional

import numpy as np

from demod import fm_demod, lowpass
from standard import detect_standard
from frame import reconstruct_frames, pick_sharpest
from render import normalize_luma


@dataclass
class VideoFrame:
    standard: Optional[str]        # None => not_video
    line_hz: int
    sync_snr_db: float
    luma: Optional[np.ndarray]     # uint8 2D frame; None when not_video or empty reconstruction


def extract_frame(iq, fs, center_hz, vcfg, std="auto"):
    """IQ -> luma core shared by the CLI and the in-loop emitter.

    Returns a VideoFrame. standard=None means the sync gate rejected it (not_video);
    luma=None with a non-None standard means detected but too few lines to render.
    center_hz is accepted for symmetry with callers but not needed for reconstruction.
    """
    bb = lowpass(fm_demod(iq), fs, vcfg.lpf_cutoff_hz)
    forced = None if std == "auto" else std
    res = detect_standard(bb, fs, forced=forced,
                          line_snr_db=vcfg.line_snr_db, harm_snr_db=vcfg.harm_snr_db)
    if res.standard is None:
        return VideoFrame(None, res.line_hz, res.sync_snr_db, None)
    frame = pick_sharpest(
        reconstruct_frames(bb, fs, res.standard, vcfg.frame_width, vcfg.blank_frac)
    )
    if frame.size == 0:
        return VideoFrame(res.standard, res.line_hz, res.sync_snr_db, None)
    return VideoFrame(res.standard, res.line_hz, res.sync_snr_db, normalize_luma(frame))
