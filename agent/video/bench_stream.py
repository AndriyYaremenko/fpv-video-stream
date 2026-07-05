"""Benchmark the chunked live-demod chain at the view sample rate.

Local sanity:   python agent/video/bench_stream.py --fs 8e6
On the Pi 5 (no checkout change — the script travels over stdin):
    ssh andriy@192.168.1.204 \
      '/opt/fpv-video-stream/agent/scan/.venv/bin/python - --fs 8e6' \
      < agent/video/bench_stream.py
Gate: "x realtime" <= ~1.0 means the Pi keeps up with live streaming.
"""
import argparse
import os
import sys
import time

for base in ("agent/video", "/opt/fpv-video-stream/agent/video",
             os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else "."):
    if os.path.isdir(base) and base not in sys.path:
        sys.path.insert(0, base)

import numpy as np
from demod import fm_demod, lowpass
from frame import reconstruct_frames
from render import normalize_luma
from synth import make_cvbs, fm_modulate, to_int8


def iq_from_int8(raw):
    data = np.frombuffer(raw, dtype=np.int8).astype(np.float32)
    return (data[0::2] + 1j * data[1::2]) / 128.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fs", type=float, default=8e6)
    ap.add_argument("--chunk-s", type=float, default=0.5)
    ap.add_argument("--rounds", type=int, default=10)
    ap.add_argument("--width", type=int, default=480)
    args = ap.parse_args()
    fs, chunk_s = args.fs, args.chunk_s

    img = (np.indices((64, 64)).sum(axis=0) % 2).astype(float)      # checkerboard
    bb = make_cvbs("PAL", img, fs, frames=max(1, int(round(chunk_s * 25))))
    raw = to_int8(fm_modulate(bb, fs, 4e6), noise_std=0.05)
    n_bytes = int(fs * 2 * chunk_s)
    raw = (raw * (n_bytes // len(raw) + 1))[:n_bytes]               # tile to one exact chunk

    t_total, frames_out = 0.0, 0
    for _ in range(args.rounds):
        t0 = time.perf_counter()
        iq = iq_from_int8(raw)
        base = lowpass(fm_demod(iq), fs, 5e6)
        frs = reconstruct_frames(base, fs, "PAL", args.width, 0.18)
        for fr in frs:
            normalize_luma(fr)
        t_total += time.perf_counter() - t0
        frames_out += len(frs)

    per_chunk = t_total / args.rounds
    print(f"fs={fs / 1e6:.1f}MS/s chunk={chunk_s}s rounds={args.rounds} width={args.width}")
    print(f"avg chunk time {per_chunk:.3f}s -> {per_chunk / chunk_s:.2f}x realtime (<=1.0 OK); "
          f"frames/chunk={frames_out / args.rounds:.1f}")


if __name__ == "__main__":
    main()
