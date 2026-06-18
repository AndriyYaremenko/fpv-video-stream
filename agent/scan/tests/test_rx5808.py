import rx5808


class FakeBackend:
    def __init__(self, clk=5, data=6, le=13):
        self.clk, self.data, self.le = clk, data, le
        self.writes = []          # (pin, level)

    def write(self, pin, level):
        self.writes.append((pin, level))


def _decode(fake):
    # Reconstruct the word: sample DATA at each CLK rising edge (0->1).
    data = 0
    prev = 0
    bits = []
    for pin, lvl in fake.writes:
        if pin == fake.data:
            data = lvl
        elif pin == fake.clk:
            if prev == 0 and lvl == 1:
                bits.append(data)
            prev = lvl
    return bits


def test_freq_to_register_matches_esp_formula():
    # tf=(5865-479)/2=2693, N=84, A=5 -> (84<<7)|5
    assert rx5808.freq_to_register(5865) == 10757


def test_encode_word_is_25_bits_lsb_first():
    w = rx5808.encode_word(5865)
    assert len(w) == 25
    assert w[:5] == [1, 0, 0, 0, 1]          # addr 0x1 LSB-first + R/W=write
    reg = rx5808.freq_to_register(5865)
    assert w[5:] == [(reg >> i) & 1 for i in range(20)]


def test_set_frequency_bitbangs_word_with_le_framing():
    fake = FakeBackend()
    rx5808.set_frequency(fake, 5865, settle_ms=0)
    assert fake.writes[0] == (fake.le, 0)    # LE low to start the transfer
    assert fake.writes[-1] == (fake.le, 1)   # LE high to latch
    assert _decode(fake) == rx5808.encode_word(5865)


def test_nearest_rx_channel():
    assert rx5808.nearest_rx_channel(5865.3) == ("A1", 5865)
    assert rx5808.nearest_rx_channel(5800.0) == ("F4", 5800)
    assert rx5808.nearest_rx_channel(5500.0) is None    # outside tolerance


def test_channel_table_is_complete():
    assert len(rx5808.RX5808_CHANNELS) == 40
    assert ("A1", 5865) in rx5808.RX5808_CHANNELS
    assert ("R8", 5917) in rx5808.RX5808_CHANNELS
