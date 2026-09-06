"""bladeRF full-duplex relay core: RX(src) -> per-block AGC -> TX(dst), no demodulation.

Mirrors agent/tx/bladerf_tx.py: open_bladerf_relay_radio is the ONLY bladeRF-touching code;
agc_block/relay_loop are pure and take an injected radio (read/write/close) so they test on fakes.
Live-verified 2026-09-06 (orange_pi6, FPGA v0.16.0): 20 MS/s full-duplex holds 1.0x realtime with
this numpy AGC; without amplitude normalization the RX5808-class receiver sees no carrier at all."""
import logging

import numpy as np

LOG = logging.getLogger("relay.bladerf")

RELAY_STREAM_TIMEOUT_MS = 3500
AGC_MAX_GAIN = 64.0


def agc_block(iq, gain, target):
    """One block of interleaved int16 I/Q -> (int16 out scaled toward `target` peak, new gain,
    RMS of the RAW input). Slow one-pole AGC: gain = 0.9*gain + 0.1*(target/peak), capped."""
    x = np.asarray(iq, dtype=np.float32)
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    if peak <= 0.0:
        peak = 1.0                                   # silent block: no division by zero
    gain = min(gain * 0.9 + (target / peak) * 0.1, AGC_MAX_GAIN)
    out = np.clip(x * gain, -2047, 2047).astype(np.int16)
    rms = float(np.sqrt(np.mean(x * x))) if x.size else 0.0
    return out, gain, rms


def relay_loop(radio, block_samples, stop_check, on_level, agc_target):
    """read -> agc -> write until stop_check() is True; on_level(rms_raw) after every block."""
    gain = 1.0
    while not stop_check():
        iq = radio.read(block_samples)
        out, gain, rms = agc_block(iq, gain, agc_target)
        radio.write(out.tobytes())
        on_level(rms)


class BladeRfRelayRadio:
    """Open full-duplex handle: read(n)->int16 array (sync_rx), write(bytes) (sync_tx), close().
    Radio/channels injected so this class imports nothing from `bladerf`."""

    def __init__(self, radio, rx_ch, tx_ch, block_samples):
        self._radio = radio
        self._rx = rx_ch
        self._tx = tx_ch
        self._buf = bytearray(block_samples * 4)     # SC16_Q11: 4 bytes per complex sample

    def read(self, n):
        self._radio.sync_rx(self._buf, n)
        return np.frombuffer(self._buf, dtype=np.int16)[: 2 * n].copy()

    def write(self, buf):
        self._radio.sync_tx(bytearray(buf), len(buf) // 4)

    def close(self):
        for ch in (self._rx, self._tx):
            try:
                self._radio.enable_module(ch, False)
            except Exception:
                LOG.exception("bladeRF relay disable failed")
        try:
            self._radio.close()
        except Exception:
            LOG.exception("bladeRF relay close failed")


def open_bladerf_relay_radio(src_hz, dst_hz, fs_hz, rx_gain_db, tx_gain_db, block_samples) -> BladeRfRelayRadio:
    """Open the first bladeRF full-duplex: RX ch0 @ src_hz (manual gain), TX ch0 @ dst_hz,
    both SC16_Q11 at fs_hz. Only bladeRF-touching fn (mirrors open_bladerf_tx_radio)."""
    import bladerf
    from bladerf import _bladerf
    radio = bladerf.BladeRF()
    try:
        rx = bladerf.CHANNEL_RX(0)
        tx = bladerf.CHANNEL_TX(0)
        for ch in (rx, tx):
            radio.set_sample_rate(ch, int(fs_hz))
            radio.set_bandwidth(ch, int(fs_hz))
        radio.set_frequency(rx, int(src_hz))
        radio.set_frequency(tx, int(dst_hz))
        radio.set_gain_mode(rx, _bladerf.GainMode.Manual)
        radio.set_gain(rx, int(rx_gain_db))
        radio.set_gain(tx, int(tx_gain_db))
        for layout in (_bladerf.ChannelLayout.RX_X1, _bladerf.ChannelLayout.TX_X1):
            radio.sync_config(
                layout=layout, fmt=_bladerf.Format.SC16_Q11,
                num_buffers=32, buffer_size=16384, num_transfers=16, stream_timeout=RELAY_STREAM_TIMEOUT_MS,
            )
        radio.enable_module(rx, True)
        radio.enable_module(tx, True)
        return BladeRfRelayRadio(radio, rx, tx, block_samples)
    except Exception:
        radio.close()
        raise
