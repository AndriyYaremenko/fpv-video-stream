import rx5808_controller as RC
from rx5808 import RX5808_CHANNELS


class FakeBackend:
    def __init__(self):
        self.clk, self.data, self.le = 5, 6, 13
        self.writes = []

    def write(self, pin, level):
        self.writes.append((pin, level))


class FakePub:
    def __init__(self):
        self.tunes = []      # (freq, channel, mode, targets-tuple, ts)

    def publish_rxtune(self, ts, freq_mhz, channel, mode, targets):
        self.tunes.append((freq_mhz, channel, mode, tuple(targets), ts))


def _ctrl(pub, backend=None):
    return RC.Rx5808Controller(
        backend or FakeBackend(), pub, "hackrf", RX5808_CHANNELS,
        dwell_s=0, settle_ms=0, clock=lambda: 1000, sleep=lambda s: None,
    )


def test_scan_mode_cycles_all_channels_in_order():
    pub = FakePub(); c = _ctrl(pub)
    for _ in range(3):
        c.run_once()
    assert [t[2] for t in pub.tunes] == ["scan", "scan", "scan"]
    assert [t[0] for t in pub.tunes] == [f for _, f in RX5808_CHANNELS[:3]]


def test_detected_mode_round_robins_targets():
    pub = FakePub(); c = _ctrl(pub)
    c.update_targets([5865.3, 5800.0])         # -> A1(5865), F4(5800)
    for _ in range(4):
        c.run_once()
    assert [t[2] for t in pub.tunes] == ["detected"] * 4
    assert [t[0] for t in pub.tunes] == [5865, 5800, 5865, 5800]
    assert pub.tunes[0][3] == (5865, 5800)     # targets carried in payload


def test_out_of_range_targets_fall_back_to_scan():
    pub = FakePub(); c = _ctrl(pub)
    c.update_targets([5500.0])                  # outside tolerance -> no channels
    c.run_once()
    assert pub.tunes[0][2] == "scan"


def test_published_ts_is_wall_clock_from_injected_clock():
    pub = FakePub()
    c = RC.Rx5808Controller(FakeBackend(), pub, "hackrf", RX5808_CHANNELS,
                            dwell_s=0, settle_ms=0, clock=lambda: 1718700000, sleep=lambda s: None)
    c.run_once()
    assert pub.tunes[0][4] == 1718700000     # ts is the wall-clock epoch (matches other topics)


def test_tune_error_is_swallowed_no_publish():
    class Boom(FakeBackend):
        def write(self, *a):
            raise RuntimeError("gpio")
    pub = FakePub()
    c = RC.Rx5808Controller(Boom(), pub, "hackrf", RX5808_CHANNELS,
                            dwell_s=0, settle_ms=0, clock=lambda: 1, sleep=lambda s: None)
    c.run_once()                                # must not raise
    assert pub.tunes == []
