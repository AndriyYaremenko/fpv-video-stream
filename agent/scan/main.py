import logging
import os
import threading
import time
from typing import List

import numpy as np

from config import Config, load_config
from sweeper import parse_sweep_output, sweep_live, sweep_replay
from detector import find_candidates
from dweller import compute_features, dwell_live, dwell_replay
from classifier import classify
from channel_map import nearest_channel
from reporter import build_payload, write_state, Holder, make_local_server
from publisher import MqttPublisher
from device import reset_hackrf
from models import Spectrum, Candidate, Detection

LOG = logging.getLogger("scan")

# Per-band cap on video-demod attempts for narrow carriers the strict detector missed
# (bounds the extra dwell time added to each sweep cycle).
_MAX_CARRIER_DEMODS_PER_BAND = 3


def _get_spectrum(cfg: Config, band: str, brange) -> Spectrum:
    if cfg.source == "replay":
        path = os.path.join(cfg.fixtures_dir, f"sweep_{band}.csv")
        return sweep_replay(path, band)
    lines = sweep_live(brange[0], brange[1], cfg.sweep_bin_hz, cfg.lna_gain, cfg.vga_gain, cfg.amp_enable)
    return parse_sweep_output(lines, band)


def _get_iq(cfg: Config, cand: Candidate) -> np.ndarray:
    if cfg.source == "replay":
        path = os.path.join(cfg.fixtures_dir, f"iq_{cand.band}.bin")
        return dwell_replay(path)
    return dwell_live(cand.center_mhz, cfg.dwell_sample_rate_hz, cfg.dwell_num_samples,
                      cfg.lna_gain, cfg.vga_gain, cfg.amp_enable)


def _occupancy(spec: Spectrum, cfg: Config) -> float:
    if len(spec.power_dbm) == 0:
        return 0.0
    noise = float(np.percentile(spec.power_dbm, 50.0))
    busy = spec.power_dbm > (noise + cfg.thresholds.occupancy_snr_db)
    return round(float(np.sum(busy)) / len(busy), 3)


def _downsample(spec: Spectrum, points: int = 64) -> list:
    p = spec.power_dbm
    if len(p) <= points:
        return [round(float(x), 1) for x in p]
    idx = np.linspace(0, len(p) - 1, points).astype(int)
    return [round(float(p[i]), 1) for i in idx]


def run_cycle(cfg: Config, now_ts: int, publisher=None, emitter=None, controller=None) -> dict:
    detections: List[Detection] = []
    occupancy = {}
    spectrum_summary = {}
    rx_carrier_centers = []

    for band, brange in cfg.bands.items():
        spec = _get_spectrum(cfg, band, brange)
        occupancy[band] = _occupancy(spec, cfg)
        spectrum_summary[band] = _downsample(spec)
        if publisher is not None:
            publisher.publish_spectrum(now_ts, band, brange[0], brange[1], _downsample(spec, 128))

        cands = find_candidates(
            spec, cfg.thresholds.snr_threshold_db, cfg.thresholds.min_bandwidth_mhz
        )
        cands.sort(key=lambda c: c.power_dbm, reverse=True)

        # Looser carrier list for THIS band (SNR/bandwidth below the analog-video gate):
        # catches narrow real FPV carriers the strict detector misses.
        loose = find_candidates(spec, cfg.rx5808_carrier_snr_db, cfg.rx5808_carrier_min_bw_mhz)
        # RX5808 targeting: only carriers inside the 5.8 GHz receiver's tuning range (by
        # frequency, so it works whatever band covers 5.8 — including a wide full-spectrum band).
        rx_carrier_centers.extend(
            c.center_mhz for c in loose if 5645.0 <= c.center_mhz <= 5945.0
        )

        budget = cfg.max_dwells_per_cycle
        for i, c in enumerate(cands):
            if i >= budget:
                LOG.info("deferred %d candidates in %s (budget=%d)", len(cands) - budget, band, budget)
                break
            iq = _get_iq(cfg, c)
            feat = compute_features(iq, cfg.dwell_sample_rate_hz)
            cls, conf = classify(feat, cfg.thresholds)
            det = Detection(
                ts=now_ts,
                band=band,
                center_mhz=c.center_mhz,
                bandwidth_mhz=feat.occupied_bw_mhz if feat.occupied_bw_mhz > 0 else c.bandwidth_mhz,
                power_dbm=c.power_dbm,
                snr_db=c.snr_db,
                signal_class=cls,
                confidence=conf,
                channel=nearest_channel(c.center_mhz),
            )
            detections.append(det)

            frame = None
            if emitter is not None and cls == "analog":
                try:
                    if emitter.maybe_emit(iq, cfg.dwell_sample_rate_hz, c.center_mhz, now_ts) == "published":
                        frame = emitter.last_frame_path
                except Exception:
                    LOG.exception("video emit failed")

            LOG.info("detection band=%s center=%.1fMHz class=%s snr=%.1fdB bw=%.1fMHz ch=%s frame=%s",
                     det.band, det.center_mhz, det.signal_class, det.snr_db,
                     det.bandwidth_mhz, det.channel or "-", frame or "-")

        # Demod strong carriers the strict detector missed, in EVERY band: a narrow analog
        # video carrier fails the wide analog-video bandwidth gate, so feed the looser carrier
        # list to the emitter and let extract_frame's line-sync check decide (only real analog
        # video publishes; the per-channel cooldown throttles reprocessing). Capped per band.
        if emitter is not None:
            done = {round(c.center_mhz, 1) for c in cands[:budget]}
            extra = 0
            for c in loose:
                if extra >= _MAX_CARRIER_DEMODS_PER_BAND:
                    break
                key = round(c.center_mhz, 1)
                if key in done:
                    continue
                done.add(key)
                extra += 1
                try:
                    iq = _get_iq(cfg, Candidate(band, c.center_mhz, 0.0, 0.0, 0.0))
                    if emitter.maybe_emit(iq, cfg.dwell_sample_rate_hz, c.center_mhz, now_ts) == "published":
                        LOG.info("carrier video band=%s center=%.1fMHz frame=%s",
                                 band, c.center_mhz, emitter.last_frame_path)
                except Exception:
                    LOG.exception("carrier video demod failed @ %.1f MHz", c.center_mhz)

    if controller is not None:
        try:
            controller.update_targets(rx_carrier_centers)
        except Exception:
            LOG.exception("rx5808 update_targets failed")

    payload = build_payload(cfg.scanner_id, now_ts, detections, occupancy, spectrum_summary)
    write_state(cfg.state_path, payload)
    if publisher is not None:
        publisher.publish_detection(now_ts, detections, occupancy)
    return payload


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config()
    holder = Holder()
    if cfg.local_http_port:
        server = make_local_server(cfg.local_http_host, cfg.local_http_port, holder)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        LOG.info("local JSON endpoint on http://%s:%d/", cfg.local_http_host, cfg.local_http_port)
    publisher = None
    if cfg.mqtt_enabled:
        try:
            publisher = MqttPublisher(
                cfg.mqtt_host, cfg.mqtt_port, cfg.mqtt_user, cfg.mqtt_pass,
                cfg.scanner_id, cfg.mqtt_keepalive,
            )
        except Exception:
            LOG.exception("MQTT publisher init failed; continuing without publishing")
            publisher = None
    emitter = None
    try:
        import video_emit                       # adds ../video to sys.path as a side effect
        from vconfig import load_video_config
        vcfg = load_video_config()
        if vcfg.video_enabled:
            emitter = video_emit.VideoEmitter(publisher, vcfg, vcfg.emit_cooldown_s)
            LOG.info("video emitter enabled (cooldown=%.0fs)", vcfg.emit_cooldown_s)
    except Exception:
        LOG.exception("video emitter init failed; continuing without video")
    controller = None
    try:
        if cfg.rx5808_enabled:
            from rx5808 import LgpioBackend, RX5808_CHANNELS
            from rx5808_controller import Rx5808Controller
            backend = LgpioBackend(clk=cfg.rx5808_clk, data=cfg.rx5808_data, le=cfg.rx5808_le)
            controller = Rx5808Controller(
                backend, publisher, cfg.scanner_id, RX5808_CHANNELS,
                cfg.rx5808_dwell_s, cfg.rx5808_settle_ms, osd_file=cfg.rx5808_osd_file,
            )
            controller.start()
            LOG.info("rx5808 controller started (dwell=%.1fs clk/data/le=%d/%d/%d)",
                     cfg.rx5808_dwell_s, cfg.rx5808_clk, cfg.rx5808_data, cfg.rx5808_le)
    except Exception:
        LOG.exception("rx5808 controller init failed; continuing without it")
    view = None
    try:
        if publisher is not None:
            import video_emit                    # noqa: F401  (side effect: ../video on sys.path)
            from vconfig import load_video_config
            viewcfg = load_video_config()
            if viewcfg.view_enabled and viewcfg.view_push_url:
                import stream_demod
                from view_controller import ViewController
                view = ViewController(
                    publisher,
                    run_stream=lambda freq, stop, max_s: stream_demod.run_stream(
                        viewcfg, freq, stop, max_s,
                        lna=cfg.lna_gain, vga=cfg.vga_gain, amp=cfg.amp_enable),
                    max_s=viewcfg.view_max_s,
                    reset=reset_hackrf,
                )
                publisher.on_view_command = view.set_command
                LOG.info("SDR view mode enabled (push=%s max=%.0fs)",
                         viewcfg.view_push_url.split("@")[-1], viewcfg.view_max_s)
    except Exception:
        LOG.exception("view mode init failed; continuing without it")
    if controller is not None and publisher is not None:
        # Apply dashboard commands (fpv/<id>/rxcmd). Wire BEFORE connect so a retained command —
        # delivered when _on_connect subscribes — is dispatched, not dropped while on_command is unset.
        publisher.on_command = controller.set_command
    if publisher is not None:
        try:
            publisher.connect(int(time.time()))
            LOG.info("MQTT publisher connected to %s:%d", cfg.mqtt_host, cfg.mqtt_port)
        except Exception:
            LOG.exception("MQTT connect failed; continuing without publishing")
            publisher = None
    backoff = 1.0
    while True:
        try:
            req = view.pending() if view is not None else None
            if req is not None:
                LOG.info("entering SDR view @ %.1f MHz (sweep paused)", req)
                view.run_view(req)
                LOG.info("SDR view ended; sweep resumes")
                continue
            payload = run_cycle(cfg, now_ts=int(time.time()), publisher=publisher,
                                emitter=emitter, controller=controller)
            holder.payload = payload
            backoff = 1.0
        except Exception:
            LOG.exception("scan cycle failed; backing off %.0fs", backoff)
            # A killed sweep/dwell (e.g. subprocess timeout) can leave the HackRF
            # wedged on flaky USB hosts; re-enumerate it so the next cycle starts clean.
            if cfg.source == "live":
                try:
                    reset_hackrf()
                except Exception:
                    LOG.exception("device reset failed")
            time.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
            continue
        time.sleep(1.0)


if __name__ == "__main__":
    main()
