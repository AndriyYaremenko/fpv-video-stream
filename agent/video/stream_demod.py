"""Continuous IQ -> grayscale frames for the SDR live-view stream.

Pure pieces (unit-tested): command builders, standard pick with PAL fallback,
row resize, chunk->frames. The subprocess pipeline (run_stream) is added on top
and kept as thin as possible."""
import logging

import numpy as np

from demod import fm_demod, lowpass
from standard import detect_standard
from frame import reconstruct_frames
from render import normalize_luma

LOG = logging.getLogger("video.stream")

VIEW_HEIGHT = {"PAL": 288, "NTSC": 240}


def build_capture_cmd(freq_hz, sample_rate_hz, lna=40, vga=20, amp=0):
    """hackrf_transfer argv streaming int8 IQ to stdout (no -n: runs until killed)."""
    return ["hackrf_transfer", "-r", "-", "-f", str(int(freq_hz)),
            "-s", str(int(sample_rate_hz)),
            "-l", str(int(lna)), "-g", str(int(vga)), "-a", str(int(amp))]


def build_encode_cmd(push_url, width, height, fps):
    """ffmpeg argv: raw gray frames on stdin -> low-latency H.264 RTSP push."""
    return ["ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{width}x{height}",
            "-r", str(fps), "-i", "-",
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
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


def chunk_to_frames(iq, fs, standard, width, height, lpf_cutoff_hz=5e6, blank_frac=0.18):
    """One IQ chunk -> list of fixed-size uint8 gray frames (height x width)."""
    bb = lowpass(fm_demod(iq), fs, lpf_cutoff_hz)
    out = []
    for fr in reconstruct_frames(bb, fs, standard, width, blank_frac):
        if fr.size == 0:
            continue
        out.append(resize_rows(normalize_luma(fr), height))
    return out
