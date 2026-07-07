"""Benchmark the chunked live-demod chain at the view sample rate.

Local sanity:   python agent/video/bench_stream.py --fs 8e6
On the Pi 5 (no checkout change — the script travels over stdin):
    ssh andriy@192.168.1.204 \
      '/opt/fpv-video-stream/agent/scan/.venv/bin/python - --fs 8e6' \
      < agent/video/bench_stream.py
Gate: "x realtime" <= ~1.0 means the Pi keeps up with live streaming.
--pipeline gate: dropped_chunks=0 (chunks fed at the real-time rate must all be demodulated).
"""
import argparse
import os
import sys
import threading
import time

for base in ("agent/video", "/opt/fpv-video-stream/agent/video",
             "agent/scan", "/opt/fpv-video-stream/agent/scan",
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


def bench_pipeline(fs, chunk_s, rounds, width, fps):
    """Feed synthetic chunks at the REAL-TIME rate through the restructured
    pipeline (mailbox -> demod -> queue -> paced writer) and report drops."""
    from stream_demod import (ChunkMailbox, FrameQueue, FramePacer, writer_loop,
                              chunk_to_frames, select_frames, VIEW_HEIGHT)
    from dweller import iq_from_int8 as iq_from_int8_dweller
    img = (np.indices((64, 64)).sum(axis=0) % 2).astype(float)
    bb = make_cvbs("PAL", img, fs, frames=max(1, int(round(chunk_s * 25))))
    raw = to_int8(fm_modulate(bb, fs, 4e6), noise_std=0.05)
    n_bytes = int(fs * 2 * chunk_s)
    raw = bytes((raw * (n_bytes // len(raw) + 1))[:n_bytes])

    mailbox = ChunkMailbox()
    q = FrameQueue(maxlen=int(fps))
    stop = threading.Event()
    err = {"msg": None}
    written = [0]
    first_write = [None]

    class _Enc:
        def poll(self):
            return None

    def _sink(fr):
        if first_write[0] is None:
            first_write[0] = time.perf_counter()
        written[0] += 1

    pacer = FramePacer(fps, _sink)
    writer = threading.Thread(target=writer_loop, args=(q, pacer, _Enc(), stop, err),
                              kwargs={"dropped_chunks": lambda: mailbox.dropped},
                              daemon=True)
    writer.start()

    done = threading.Event()

    def feeder():
        for _ in range(rounds):
            time.sleep(chunk_s)                     # chunks arrive at the air rate
            mailbox.put(raw)
        done.set()

    threading.Thread(target=feeder, daemon=True).start()

    t0 = time.perf_counter()
    height = VIEW_HEIGHT["PAL"]
    while True:
        buf = mailbox.take()
        if buf is None:
            if done.is_set():
                break
            time.sleep(0.005)
            continue
        iq = iq_from_int8_dweller(buf)
        for fr in select_frames(chunk_to_frames(iq, fs, "PAL", width, height, 5e6),
                                chunk_s, fps):
            q.put(fr.tobytes())
    q.close()
    writer.join(timeout=int(fps) / fps + 2.0)
    stop.set()
    dur = time.perf_counter() - (first_write[0] if first_write[0] is not None else t0)
    print(f"pipeline fs={fs / 1e6:.1f}MS/s rounds={rounds} width={width} fps={fps}")
    print(f"dropped_chunks={mailbox.dropped} dropped_frames={q.dropped} "
          f"avg_fps={written[0] / dur:.1f} (gate: dropped_chunks=0)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fs", type=float, default=8e6)
    ap.add_argument("--chunk-s", type=float, default=0.5)
    ap.add_argument("--rounds", type=int, default=10)
    ap.add_argument("--width", type=int, default=480)
    ap.add_argument("--pipeline", action="store_true",
                    help="feed chunks at the real-time rate; gate: dropped_chunks=0")
    ap.add_argument("--fps", type=float, default=15.0)
    args = ap.parse_args()
    fs, chunk_s = args.fs, args.chunk_s

    if args.pipeline:
        bench_pipeline(args.fs, args.chunk_s, args.rounds, args.width, args.fps)
        return

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
