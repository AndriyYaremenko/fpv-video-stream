import random

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


def _ctrl(pub, backend=None, rng=None):
    return RC.Rx5808Controller(
        backend or FakeBackend(), pub, "hackrf", RX5808_CHANNELS,
        dwell_s=0, settle_ms=0, clock=lambda: 1000, sleep=lambda s: None, rng=rng,
    )


def test_auto_mode_cycles_all_channels():
    pub = FakePub(); c = _ctrl(pub)
    for _ in range(3):
        c.run_once()
    assert [t[2] for t in pub.tunes] == ["auto", "auto", "auto"]
    assert [t[0] for t in pub.tunes] == [f for _, f in RX5808_CHANNELS[:3]]


def test_auto_mode_round_robins_targets():
    pub = FakePub(); c = _ctrl(pub)
    c.update_targets([5865.3, 5800.0])         # -> A1(5865), F4(5800)
    for _ in range(4):
        c.run_once()
    assert [t[2] for t in pub.tunes] == ["auto"] * 4
    assert [t[0] for t in pub.tunes] == [5865, 5800, 5865, 5800]
    assert pub.tunes[0][3] == (5865, 5800)     # targets carried in payload


def test_auto_mode_out_of_range_targets_cycle_all():
    pub = FakePub(); c = _ctrl(pub)
    c.update_targets([5500.0])                  # outside tolerance -> no channels -> all 40
    c.run_once()
    assert pub.tunes[0][2] == "auto"


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


def test_set_command_scan_cycles_all():
    pub = FakePub(); c = _ctrl(pub)
    c.update_targets([5865.0])           # has a target, but scan ignores it
    c.set_command("scan")
    for _ in range(2):
        c.run_once()
    assert [t[2] for t in pub.tunes] == ["scan", "scan"]
    assert [t[0] for t in pub.tunes] == [f for _, f in RX5808_CHANNELS[:2]]


def test_set_command_random_uses_injected_rng():
    pub = FakePub(); c = _ctrl(pub, rng=random.Random(0))
    c.set_command("random")
    c.run_once()
    expected = random.Random(0).choice(RX5808_CHANNELS)
    assert pub.tunes[0][0] == expected[1]
    assert pub.tunes[0][2] == "random"


def test_set_command_manual_holds_channel():
    pub = FakePub(); c = _ctrl(pub)
    c.set_command("manual", "A1")
    for _ in range(2):
        c.run_once()
    assert [t[0] for t in pub.tunes] == [5865, 5865]    # A1, held
    assert [t[1] for t in pub.tunes] == ["A1", "A1"]
    assert [t[2] for t in pub.tunes] == ["manual", "manual"]


def test_set_command_unknown_mode_ignored():
    pub = FakePub(); c = _ctrl(pub)
    c.set_command("bogus")
    c.run_once()
    assert pub.tunes[0][2] == "auto"     # unchanged


def test_set_command_manual_unknown_channel_keeps_previous():
    pub = FakePub(); c = _ctrl(pub)
    c.set_command("manual", "A1")        # valid
    c.set_command("manual", "BOGUS")     # unknown -> keep A1
    c.run_once()
    assert pub.tunes[0][1] == "A1"       # still A1
    assert pub.tunes[0][2] == "manual"
