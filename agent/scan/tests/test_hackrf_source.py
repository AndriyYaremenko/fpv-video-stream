from hackrf_source import IqRing


def test_ring_reads_exactly_n_in_arrival_order():
    r = IqRing(100)
    r.write(b"ab")
    r.write(b"cd")
    assert r.read(3, timeout_s=0.1) == b"abc"       # splits a buffer, keeps the tail
    assert r.read(1, timeout_s=0.1) == b"d"
    assert r.pending() == 0


def test_ring_times_out_on_underrun():
    r = IqRing(100)
    r.write(b"ab")
    assert r.read(3, timeout_s=0.05) is None        # watchdog signal
    assert r.read(2, timeout_s=0.1) == b"ab"        # data not lost by the timeout


def test_ring_overflow_drops_oldest_and_counts():
    r = IqRing(4)
    r.write(b"ab")
    r.write(b"cd")
    r.write(b"ef")                                   # cap 4 -> "ab" dropped
    assert r.dropped_bytes == 2
    assert r.read(4, timeout_s=0.1) == b"cdef"


def test_ring_clear_flushes_pending():
    r = IqRing(100)
    r.write(b"abcd")
    r.clear()
    assert r.pending() == 0
    assert r.read(1, timeout_s=0.05) is None


from hackrf_source import HackrfSource


class _FakeRadio:
    def __init__(self):
        self.freqs = []
        self.sink = None
        self.fs = None
        self.closed = False

    def start_rx(self, sink, sample_rate_hz):
        self.sink = sink
        self.fs = sample_rate_hz

    def set_freq(self, hz):
        self.freqs.append(hz)

    def close(self):
        self.closed = True


def _source(radios):
    def factory():
        r = _FakeRadio()
        radios.append(r)
        return r
    return HackrfSource(factory, 1e6)


def test_tune_opens_lazily_and_flushes_the_transient():
    radios = []
    s = _source(radios)
    s.tune(5865e6)
    assert len(radios) == 1 and radios[0].fs == 1e6
    assert radios[0].freqs == [5865000000]
    radios[0].sink(b"stale-air")                    # pre-retune samples
    s.tune(5905e6)                                  # live retune: same open radio
    assert len(radios) == 1 and radios[0].freqs == [5865000000, 5905000000]
    assert s.read_chunk(4, timeout_s=0.05) is None  # transient flushed
    radios[0].sink(b"good")
    assert s.read_chunk(4, timeout_s=0.2) == b"good"


def test_recover_reopens_and_retunes():
    radios = []
    s = _source(radios)
    s.tune(5865e6)
    s.recover()
    assert radios[0].closed
    assert len(radios) == 2 and radios[1].freqs == [5865000000]


def test_close_is_idempotent_and_reopens_on_next_tune():
    radios = []
    s = _source(radios)
    s.tune(5865e6)
    s.close()
    s.close()                                        # second close: no-op
    assert radios[0].closed
    s.tune(5905e6)
    assert len(radios) == 2 and radios[1].freqs == [5905000000]
