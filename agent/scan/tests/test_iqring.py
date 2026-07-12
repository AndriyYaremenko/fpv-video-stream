from iqring import IqRing


def test_reads_exactly_n_in_arrival_order():
    r = IqRing(100)
    r.write(b"ab")
    r.write(b"cd")
    assert r.read(3, timeout_s=0.1) == b"abc"
    assert r.read(1, timeout_s=0.1) == b"d"
    assert r.pending() == 0


def test_overflow_drops_oldest_and_counts():
    r = IqRing(4)
    r.write(b"ab")
    r.write(b"cd")
    r.write(b"ef")                      # cap 4 -> "ab" dropped
    assert r.dropped_bytes == 2
    assert r.read(4, timeout_s=0.1) == b"cdef"


def test_hackrf_source_still_reexports_iqring():
    from hackrf_source import IqRing as HR
    assert HR is IqRing              # moved, not duplicated
