"""bladeRF TX for the FPV-video generator: loop-stream a pre-rendered SC16_Q11 IQ .bin.

open_bladerf_tx_radio is the only bladeRF-touching code (mirrors open_bladerf_view_radio,
but CHANNEL_TX + sync_tx; plumbing from feat/bladerf-video-relay:relay_spike.py)."""
import logging

LOG = logging.getLogger("tx.bladerf")

TX_STREAM_TIMEOUT_MS = 3500


def transmit_loop(radio, iq_path, block_bytes, stop_check):
    """Stream iq_path to radio.write() in block_bytes chunks, looping seamlessly at EOF
    (a short tail is filled from the file start — no gap). Runs until stop_check() is True.
    An empty file returns immediately."""
    with open(iq_path, "rb") as f:
        while not stop_check():
            buf = f.read(block_bytes)
            if not buf:
                f.seek(0)
                buf = f.read(block_bytes)
                if not buf:
                    return                       # empty file
            if len(buf) < block_bytes:
                f.seek(0)
                buf = buf + f.read(block_bytes - len(buf))   # seamless wrap fill
            radio.write(buf)


class BladeRfTxRadio:
    """Open TX handle: write(bytes) -> sync_tx one block; set_frequency; close. Radio/channel
    injected (open_bladerf_tx_radio) so this class imports nothing from `bladerf`."""

    def __init__(self, radio, channel):
        self._radio = radio
        self._ch = channel

    def write(self, buf):
        # buf is block_samples*4 SC16_Q11 bytes; sync_tx wants a mutable buffer + sample count.
        self._radio.sync_tx(bytearray(buf), len(buf) // 4)

    def set_frequency(self, hz):
        self._radio.set_frequency(self._ch, int(hz))

    def set_gain(self, db):
        self._radio.set_gain(self._ch, int(db))

    def close(self):
        try:
            self._radio.enable_module(self._ch, False)
        except Exception:
            LOG.exception("bladeRF TX disable failed")
        finally:
            try:
                self._radio.close()
            except Exception:
                LOG.exception("bladeRF TX close failed")


def open_bladerf_tx_radio(freq_hz, fs_hz, gain_db, bandwidth_hz) -> BladeRfTxRadio:
    """Open the first bladeRF configured for continuous TX (SC16_Q11). Only bladeRF-touching fn."""
    import bladerf
    from bladerf import _bladerf
    radio = bladerf.BladeRF()
    ch = bladerf.CHANNEL_TX(0)
    radio.set_sample_rate(ch, int(fs_hz))
    radio.set_bandwidth(ch, int(bandwidth_hz))
    radio.set_frequency(ch, int(freq_hz))
    radio.set_gain(ch, int(gain_db))
    radio.sync_config(
        layout=_bladerf.ChannelLayout.TX_X1, fmt=_bladerf.Format.SC16_Q11,
        num_buffers=16, buffer_size=8192, num_transfers=8, stream_timeout=TX_STREAM_TIMEOUT_MS,
    )
    radio.enable_module(ch, True)
    return BladeRfTxRadio(radio, ch)
