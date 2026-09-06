import numpy as np

from bladerf_relay import AGC_MAX_GAIN, agc_block, relay_loop


def test_agc_block_moves_toward_target_and_reports_raw_rms():
    iq = np.full(64, 100, dtype=np.int16)
    out, gain, rms = agc_block(iq, 1.0, 1600.0)
    assert abs(rms - 100.0) < 1e-3                    # RMS of the RAW input, before AGC
    assert 1.0 < gain < 16.0                          # smoothed step toward 1600/100 = 16
    assert int(out.max()) == int(round(100 * gain))
    for _ in range(300):
        out, gain, rms = agc_block(iq, gain, 1600.0)
    assert abs(gain - 16.0) < 0.05                    # converged
    assert abs(int(out.max()) - 1600) <= 5


def test_agc_block_clips_to_sc16_and_caps_gain_without_div_by_zero():
    hot = np.array([3000, -3000] * 8, dtype=np.int16)
    out, gain, _ = agc_block(hot, 1.0, 1600.0)
    assert out.dtype == np.int16 and out.max() <= 2047 and out.min() >= -2047
    zero = np.zeros(16, dtype=np.int16)
    _, g, rms = agc_block(zero, 1.0, 1600.0)          # peak 0 -> no ZeroDivisionError
    assert rms == 0.0 and g <= AGC_MAX_GAIN
    for _ in range(50):
        _, g, _ = agc_block(zero, g, 1600.0)
    assert g == AGC_MAX_GAIN


class _FakeRadio:
    def __init__(self, blocks):
        self.blocks = list(blocks)
        self.writes = []
    def read(self, n):
        return self.blocks.pop(0)
    def write(self, b):
        self.writes.append(bytes(b))


def test_relay_loop_reads_agcs_writes_until_stop_and_reports_level():
    radio = _FakeRadio([np.full(8, 200, dtype=np.int16)] * 5)
    levels = []
    n = {"c": 0}
    def stop():
        n["c"] += 1
        return n["c"] > 3                             # 3 iterations
    relay_loop(radio, 4, stop, levels.append, 1600.0)
    assert len(radio.writes) == 3 and len(levels) == 3
    assert all(abs(l - 200.0) < 1e-3 for l in levels)     # raw RMS, not the AGC'd one
    assert len(radio.writes[0]) == 16                 # 8 int16 -> 16 bytes
    assert len(radio.blocks) == 2                     # exactly 3 reads consumed
