"""Render a video file into a loopable SC16_Q11 IQ .bin for bladeRF FPV-video TX.

Reuses agent/video/synth.py for the CVBS + FM-modulation DSP; the only new DSP here
is the SC16_Q11 quantizer (bladeRF TX wire format)."""
import logging
import os
import subprocess

import numpy as np

from synth import make_cvbs, fm_modulate       # agent/video (on sys.path via conftest / main)

LOG = logging.getLogger("tx.render")


def to_sc16q11(iq) -> bytes:
    """Complex IQ (unit-scale) -> bladeRF SC16_Q11 interleaved int16 bytes (×2047, clipped)."""
    iq = np.asarray(iq, dtype=np.complex128)
    i = np.clip(np.round(iq.real * 2047.0), -2048, 2047).astype(np.int16)
    q = np.clip(np.round(iq.imag * 2047.0), -2048, 2047).astype(np.int16)
    out = np.empty(2 * len(iq), dtype=np.int16)
    out[0::2] = i
    out[1::2] = q
    return out.tobytes()


def frame_to_iq(frame_gray, standard, fs, deviation_hz, interlaced=True, vbi_lines=0) -> bytes:
    """One grayscale frame (uint8 h×w) -> one frame of SC16_Q11 IQ (CVBS -> FM -> quantize)."""
    img = np.asarray(frame_gray, dtype=np.float64) / 255.0
    bb = make_cvbs(standard, img, fs, frames=1, interlaced=interlaced, vbi_lines=vbi_lines)
    iq = fm_modulate(bb, fs, deviation_hz)
    return to_sc16q11(iq)


def build_ffmpeg_decode_cmd(path, fps, width, height):
    """ffmpeg argv: decode <path> to raw gray frames (width×height @ fps) on stdout."""
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", path,
            "-vf", f"fps={fps},scale={width}:{height},format=gray",
            "-f", "rawvideo", "-"]


def render(path, out_bin, standard="PAL", fs=20_000_000.0, deviation_hz=4_000_000.0,
           width=640, height=512, fps=25, max_secs=3.0, interlaced=True, vbi_lines=6,
           popen=None):
    """Decode `path` with ffmpeg and write frame-by-frame SC16_Q11 IQ into `out_bin`,
    up to max_secs of the clip. Returns (frames_written, bytes_written)."""
    popen = popen or subprocess.Popen
    out_dir = os.path.dirname(out_bin)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)      # cache dir (e.g. /var/lib/fpv/tx/.cache) may not exist yet
    frame_bytes = width * height
    max_frames = int(round(max_secs * fps))
    cap = popen(build_ffmpeg_decode_cmd(path, fps, width, height),
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    frames = 0
    written = 0
    try:
        with open(out_bin, "wb") as out:
            while frames < max_frames:
                raw = cap.stdout.read(frame_bytes)
                if not raw or len(raw) < frame_bytes:
                    break                                    # EOF / short clip
                frame = np.frombuffer(raw, dtype=np.uint8).reshape(height, width)
                iqb = frame_to_iq(frame, standard, fs, deviation_hz, interlaced, vbi_lines)
                out.write(iqb)
                frames += 1
                written += len(iqb)
    finally:
        try:
            cap.kill(); cap.wait(timeout=5)
        except Exception:
            pass
    LOG.info("render: %d frames, %d bytes -> %s", frames, written, out_bin)
    return frames, written
