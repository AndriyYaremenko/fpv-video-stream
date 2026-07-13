"""FPV video-from-file TX generator (bladeRF), Phase-0 spike CLI.

  render:   python -m main render <file> <out.bin> [--standard PAL] [--fs 20e6] [--dev 4e6]
                                  [--w 640] [--h 512] [--fps 25] [--secs 3] [--vbi 6]
  transmit: python -m main transmit <iq.bin> <freq_mhz> [--fs 20e6] [--gain 30]  (Ctrl-C stops)

Reuses agent/video/synth via render.py. TX loops the .bin seamlessly on <freq_mhz>."""
import argparse
import logging
import os
import signal
import sys

# tx_render needs ../video for `synth`; keep agent/tx importable for `tx_render`/`bladerf_tx`.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
_VIDEO = os.path.abspath(os.path.join(_HERE, "..", "video"))
if _VIDEO not in sys.path:
    sys.path.append(_VIDEO)

from tx_render import render                     # noqa: E402
from bladerf_tx import open_bladerf_tx_radio, transmit_loop   # noqa: E402


def _render(a):
    frames, nbytes = render(a.file, a.out, standard=a.standard, fs=a.fs, deviation_hz=a.dev,
                            width=a.w, height=a.h, fps=a.fps, max_secs=a.secs, vbi_lines=a.vbi)
    secs = (nbytes / 4) / a.fs if a.fs else 0
    print(f"rendered {frames} frames, {nbytes} bytes ({secs:.2f}s of IQ @ {a.fs/1e6:.1f} MS/s) -> {a.out}")


def _transmit(a):
    print(f"⚠ TX on {a.freq_mhz} MHz @ {a.fs/1e6:.1f} MS/s gain={a.gain} — ensure you are authorized "
          f"(power/antenna/shielded bench). Ctrl-C to stop.")
    radio = open_bladerf_tx_radio(int(a.freq_mhz * 1e6), int(a.fs), a.gain, int(a.fs))
    stop = {"v": False}
    signal.signal(signal.SIGINT, lambda *_: stop.__setitem__("v", True))
    try:
        transmit_loop(radio, a.iq, block_bytes=32768 * 4, stop_check=lambda: stop["v"])
    finally:
        radio.close()
    print("TX stopped.")


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="fpv-tx")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("render")
    r.add_argument("file"); r.add_argument("out")
    r.add_argument("--standard", default="PAL"); r.add_argument("--fs", type=float, default=20e6)
    r.add_argument("--dev", type=float, default=4e6); r.add_argument("--w", type=int, default=640)
    r.add_argument("--h", type=int, default=512); r.add_argument("--fps", type=int, default=25)
    r.add_argument("--secs", type=float, default=3.0); r.add_argument("--vbi", type=int, default=6)
    r.set_defaults(fn=_render)

    t = sub.add_parser("transmit")
    t.add_argument("iq"); t.add_argument("freq_mhz", type=float)
    t.add_argument("--fs", type=float, default=20e6); t.add_argument("--gain", type=int, default=30)
    t.set_defaults(fn=_transmit)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
