import time

# 40 standard 5.8 GHz FPV channels (Band A, B, E, F=Airwave, R=RaceBand) — from the ESP sketch.
_BANDS = {
    "A": [5865, 5845, 5825, 5805, 5785, 5765, 5745, 5725],
    "B": [5733, 5752, 5771, 5790, 5809, 5828, 5847, 5866],
    "E": [5705, 5685, 5665, 5645, 5885, 5905, 5925, 5945],
    "F": [5740, 5760, 5780, 5800, 5820, 5840, 5860, 5880],
    "R": [5658, 5695, 5732, 5769, 5806, 5843, 5880, 5917],
}
RX5808_CHANNELS = [(f"{b}{i + 1}", f) for b, freqs in _BANDS.items() for i, f in enumerate(freqs)]


def freq_to_register(mhz):
    """RTC6715 synthesizer register value for a frequency (MHz). 20-bit payload."""
    tf = (int(round(mhz)) - 479) // 2
    n = tf // 32
    a = tf % 32
    return (n << 7) | (a & 0x7F)


def encode_word(mhz):
    """The 25-bit word in send order (LSB-first): address 0x1, R/W=write, 20 data bits."""
    reg = freq_to_register(mhz)
    bits = [1, 0, 0, 0, 1]                       # addr 0x1 (LSB-first) + R/W=1 (write)
    bits += [(reg >> i) & 1 for i in range(20)]
    return bits


def set_frequency(backend, mhz, settle_ms=35, sleep=time.sleep):
    """Bit-bang the tune word to the RX5808 via `backend` (clk/data/le pins + write()).

    Data is set before each CLK rising edge (the latch). LE low frames the transfer,
    LE high latches it; then wait settle_ms for PLL lock.
    """
    backend.write(backend.le, 0)
    for bit in encode_word(mhz):
        backend.write(backend.clk, 0)
        backend.write(backend.data, 1 if bit else 0)
        backend.write(backend.clk, 1)            # rising edge latches `data`
        backend.write(backend.clk, 0)
    backend.write(backend.le, 1)
    if settle_ms:
        sleep(settle_ms / 1000.0)


def nearest_rx_channel(center_mhz, tol=10.0):
    """Nearest standard 5.8 channel (name, freq) within tol MHz, or None. First wins on ties."""
    best = None
    best_d = tol + 1e-9
    for name, f in RX5808_CHANNELS:
        d = abs(f - center_mhz)
        if d < best_d:
            best_d = d
            best = (name, f)
    return best


class LgpioBackend:
    """Real GPIO backend (Raspberry Pi, lgpio). Lazy-imports lgpio so non-Pi hosts don't need it."""

    def __init__(self, chip=0, clk=5, data=6, le=13):
        import lgpio
        self._lg = lgpio
        self.clk, self.data, self.le = clk, data, le
        self._h = lgpio.gpiochip_open(chip)
        for pin in (clk, data, le):
            lgpio.gpio_claim_output(self._h, pin, 0)
        lgpio.gpio_write(self._h, le, 1)         # LE idle high

    def write(self, pin, level):
        self._lg.gpio_write(self._h, pin, level)

    def close(self):
        try:
            self._lg.gpiochip_close(self._h)
        except Exception:
            pass
