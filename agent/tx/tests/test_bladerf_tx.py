from bladerf_tx import transmit_loop


class _FakeRadio:
    def __init__(self):
        self.writes = []
    def write(self, b):
        self.writes.append(bytes(b))


def test_transmit_loop_streams_blocks_then_wraps_and_stops(tmp_path):
    p = tmp_path / "iq.bin"
    p.write_bytes(bytes(range(12)))              # 12 bytes = 3 blocks of 4
    radio = _FakeRadio()
    calls = {"n": 0}
    def stop():
        calls["n"] += 1
        return calls["n"] > 5                    # allow 5 blocks
    transmit_loop(radio, str(p), block_bytes=4, stop_check=stop)
    assert len(radio.writes) == 5
    assert radio.writes[0] == bytes([0, 1, 2, 3])
    assert radio.writes[1] == bytes([4, 5, 6, 7])
    assert radio.writes[2] == bytes([8, 9, 10, 11])
    assert radio.writes[3] == bytes([0, 1, 2, 3])   # wrapped to file start (seamless loop)
    assert radio.writes[4] == bytes([4, 5, 6, 7])


def test_transmit_loop_seamless_wrap_across_a_block(tmp_path):
    p = tmp_path / "iq.bin"
    p.write_bytes(bytes(range(10)))              # 10 bytes, block 4 -> tail of 2 wraps
    radio = _FakeRadio()
    calls = {"n": 0}
    def stop():
        calls["n"] += 1
        return calls["n"] > 3
    transmit_loop(radio, str(p), block_bytes=4, stop_check=stop)
    assert radio.writes[0] == bytes([0, 1, 2, 3])
    assert radio.writes[1] == bytes([4, 5, 6, 7])
    assert radio.writes[2] == bytes([8, 9, 0, 1])   # tail(8,9) + wrap fill(0,1): no gap
    assert len(radio.writes) == 3


def test_transmit_loop_empty_file_returns(tmp_path):
    p = tmp_path / "iq.bin"; p.write_bytes(b"")
    radio = _FakeRadio()
    transmit_loop(radio, str(p), block_bytes=4, stop_check=lambda: False)
    assert radio.writes == []                    # empty file: no writes, no infinite loop
