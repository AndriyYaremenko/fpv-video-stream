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
