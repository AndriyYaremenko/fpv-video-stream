import argparse
import logging
import os
import sys
import time

# Allow running as a script: expose agent/scan's flat modules (config, publisher).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scan")))

from config import load_config                              # noqa: E402  (reused scan config)
from publisher import build_video_payload, publish_video_once  # noqa: E402
from iqio import load_iq                                    # noqa: E402
from demod import fm_demod, lowpass                         # noqa: E402
from standard import detect_standard                        # noqa: E402
from frame import reconstruct_frames, pick_sharpest         # noqa: E402
from render import normalize_luma, save_full_png, thumbnail_b64  # noqa: E402
from vconfig import load_video_config                       # noqa: E402

LOG = logging.getLogger("video")

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOT_VIDEO = 2


def process(iq_path, fs, center_hz, std, vcfg, scfg, now_ts):
    iq = load_iq(iq_path)
    bb = lowpass(fm_demod(iq), fs, vcfg.lpf_cutoff_hz)
    forced = None if std == "auto" else std
    res = detect_standard(bb, fs, forced=forced,
                          line_snr_db=vcfg.line_snr_db, harm_snr_db=vcfg.harm_snr_db)
    center_mhz = center_hz / 1e6
    if res.standard is None:
        LOG.info("status=not_video center_mhz=%.3f sync_snr_db=%.1f",
                 center_mhz, res.sync_snr_db)
        return EXIT_NOT_VIDEO

    frame = pick_sharpest(
        reconstruct_frames(bb, fs, res.standard, vcfg.frame_width, vcfg.blank_frac)
    )
    if frame.size == 0:
        # Sync gate passed but too few samples to slice even one line — nothing to render.
        LOG.warning("status=error center_mhz=%.3f standard=%s reason=no_lines_reconstructed",
                    center_mhz, res.standard)
        return EXIT_ERROR
    luma = normalize_luma(frame)
    frame_path = os.path.join(vcfg.frames_dir, f"{now_ts}.png")
    save_full_png(luma, frame_path)
    thumb = thumbnail_b64(luma, vcfg.thumb_max_width)

    payload = build_video_payload(
        scfg.scanner_id, float(now_ts), center_mhz, res.standard, res.line_hz,
        round(float(res.sync_snr_db), 1), thumb,
    )
    ok = publish_video_once(
        scfg.mqtt_host, scfg.mqtt_port, scfg.mqtt_user, scfg.mqtt_pass,
        scfg.scanner_id, payload, scfg.mqtt_keepalive,
    )
    LOG.info("status=%s center_mhz=%.3f standard=%s sync_snr_db=%.1f frame_path=%s mqtt=%s",
             "published" if ok else "local_only", center_mhz, res.standard,
             res.sync_snr_db, frame_path, ok)
    return EXIT_OK if ok else EXIT_ERROR


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Analog FPV IQ -> luma frame -> MQTT")
    ap.add_argument("--iq", required=True, help="path to HackRF int8 IQ capture")
    ap.add_argument("--fs", type=float, default=None, help="sample rate Hz (default: config)")
    ap.add_argument("--center", type=float, required=True, help="center frequency Hz (metadata)")
    ap.add_argument("--std", choices=["auto", "pal", "ntsc"], default="auto")
    args = ap.parse_args(argv)

    vcfg = load_video_config()
    scfg = load_config()
    fs = args.fs if args.fs is not None else vcfg.default_fs
    now_ts = int(time.time())
    try:
        return process(args.iq, fs, args.center, args.std, vcfg, scfg, now_ts)
    except Exception:
        LOG.exception("processing failed for %s", args.iq)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
