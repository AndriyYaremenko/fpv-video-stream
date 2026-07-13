import os
from txconfig import TxConfig
from tx_controller import TxController


class _FakePublisher:
    def __init__(self): self.states = []
    def publish_txstate(self, ts, state): self.states.append(dict(state))


class _FakeRadio:
    def __init__(self): self.freqs = []; self.gains = []; self.closed = False
    def set_frequency(self, hz): self.freqs.append(hz)
    def set_gain(self, db): self.gains.append(db)
    def close(self): self.closed = True


def _cfg(tmp): return TxConfig(tx_enabled=True, tx_dir=str(tmp),
                               tx_cache_bin=str(tmp / "current.bin"), tx_max_s=100.0)


def _mk(tmp, **kw):
    renders = []; radios = []
    def render_fn(path, out_bin, **k): renders.append((path, out_bin, k))
    def open_tx_fn(freq_hz, fs_hz, gain_db, bw_hz, **k):
        r = _FakeRadio(); r.freqs.append(freq_hz); r.gains.append(gain_db); radios.append(r); return r
    transmit_calls = []
    def transmit_fn(radio, path, block_bytes, stop_check):
        transmit_calls.append(path)
        if kw.get("on_transmit"): kw["on_transmit"](len(transmit_calls))
        # returns immediately (as if stop_check tripped) unless a fake clock forces the deadline path
    pub = _FakePublisher()
    ctl = TxController(_cfg(tmp), pub, render_fn=render_fn, open_tx_fn=open_tx_fn,
                       transmit_fn=transmit_fn, reset=kw.get("reset", lambda: None),
                       clock=kw.get("clock", lambda: 1000), exists_fn=lambda p: True)
    return ctl, pub, renders, radios, transmit_calls


def test_start_renders_opens_transmits_then_idle(tmp_path):
    ctl, pub, renders, radios, tx = _mk(tmp_path)
    ctl.set_command({"tx": {"action": "start", "file": "clip.mp4", "freq_mhz": 5800, "gain_db": 25}})
    req = ctl.pending()
    assert req is not None
    ctl.run_tx(req)
    assert len(renders) == 1                              # rendered once
    assert renders[0][0] == os.path.join(str(tmp_path), "clip.mp4")   # file_path from tx_dir
    assert renders[0][1] == str(tmp_path / "current.bin")             # -> cache bin
    assert len(radios) == 1 and radios[0].freqs[0] == int(5800e6) and radios[0].gains[0] == 25
    assert radios[0].closed is True
    statuses = [s["status"] for s in pub.states]
    assert "rendering" in statuses and "transmitting" in statuses
    assert pub.states[-1]["active"] is False and pub.states[-1]["status"] == "idle"


def test_render_reused_when_key_unchanged(tmp_path):
    ctl, pub, renders, radios, tx = _mk(tmp_path)
    for _ in range(2):
        ctl.set_command({"tx": {"action": "start", "file": "clip.mp4", "freq_mhz": 5800}})
        ctl.run_tx(ctl.pending())
    assert len(renders) == 1                              # second start reused the cached .bin


def test_retune_freq_no_rerender(tmp_path):
    def inject(n):
        if n == 1:
            ctl_ref[0].set_command({"tx": {"action": "retune", "freq_mhz": 5760}})
    ctl_ref = [None]
    ctl, pub, renders, radios, tx = _mk(tmp_path, on_transmit=inject)
    ctl_ref[0] = ctl
    ctl.set_command({"tx": {"action": "start", "file": "clip.mp4", "freq_mhz": 5800}})
    ctl.run_tx(ctl.pending())
    assert len(renders) == 1                              # retune did NOT re-render
    assert len(tx) == 2                                   # transmit re-entered once after retune
    assert int(5760e6) in radios[0].freqs                 # live set_frequency applied


def test_deadline_auto_stops(tmp_path):
    now = [1000]
    def clk(): now[0] += 60; return now[0]               # each call advances 60s
    ctl, pub, renders, radios, tx = _mk(tmp_path, clock=clk)
    ctl.set_command({"tx": {"action": "start", "file": "clip.mp4", "freq_mhz": 5800}})
    ctl.run_tx(ctl.pending())
    # until_ts must be since_ts + tx_max_s(100); after the clock passes it, run_tx exits to idle
    tstate = next(s for s in pub.states if s["status"] == "transmitting")
    assert tstate["until_ts"] == tstate["since_ts"] + 100
    assert pub.states[-1]["status"] == "idle"


def test_bad_commands_never_throw_and_set_nothing(tmp_path):
    ctl, pub, renders, radios, tx = _mk(tmp_path)
    ctl.set_command({"tx": {"action": "start", "file": "clip.mp4", "freq_mhz": 99999}})  # bad freq
    ctl.set_command({"tx": {"action": "start", "freq_mhz": 5800}})                        # no file
    ctl.set_command({"tx": {}})                                                           # no action
    assert ctl.pending() is None
    # stop while idle just clears; start then stop -> nothing pending
    ctl.set_command({"tx": {"action": "start", "file": "clip.mp4", "freq_mhz": 5800}})
    ctl.set_command({"tx": {"action": "stop"}})
    assert ctl.pending() is None


def test_missing_file_rejected(tmp_path):
    ctl, pub, renders, radios, tx = _mk(tmp_path)
    ctl._exists = lambda p: False                          # simulate file gone
    ctl.set_command({"tx": {"action": "start", "file": "nope.mp4", "freq_mhz": 5800}})
    assert ctl.pending() is None


def test_transmit_stops_when_deadline_trips(tmp_path):
    now = [1000]
    def clk(): now[0] += 10; return now[0]        # advances so clock() eventually >= deadline
    polled = {"n": 0}
    class _R:
        def __init__(self): self.closed = False
        def set_frequency(self, hz): pass
        def set_gain(self, db): pass
        def close(self): self.closed = True
    def transmit_fn(radio, path, block, stop_check):
        while not stop_check():                    # real transmit_loop polls stop_check
            polled["n"] += 1
            if polled["n"] > 10000: raise AssertionError("deadline never tripped")
    states = []
    class _Pub:
        def publish_txstate(self, ts, s): states.append(dict(s))
    cfg = TxConfig(tx_enabled=True, tx_dir=str(tmp_path),
                   tx_cache_bin=str(tmp_path / "b.bin"), tx_max_s=50.0)
    ctl = TxController(cfg, _Pub(), render_fn=lambda *a, **k: None,
                       open_tx_fn=lambda *a, **k: _R(), transmit_fn=transmit_fn,
                       clock=clk, exists_fn=lambda p: True)
    ctl.set_command({"tx": {"action": "start", "file": "c.mp4", "freq_mhz": 5800}})
    ctl.run_tx(ctl.pending())
    assert polled["n"] > 0                          # stop_check was actually polled
    assert states[-1]["status"] == "idle" and states[-1]["active"] is False


def test_setup_failure_is_captured_not_raised(tmp_path):
    states = []
    class _Pub:
        def publish_txstate(self, ts, s): states.append(dict(s))
    def boom(*a, **k): raise RuntimeError("bladeRF busy")
    cfg = TxConfig(tx_enabled=True, tx_dir=str(tmp_path), tx_cache_bin=str(tmp_path / "b.bin"))
    ctl = TxController(cfg, _Pub(), render_fn=lambda *a, **k: None,
                       open_tx_fn=boom, transmit_fn=lambda *a, **k: None,
                       clock=lambda: 1000, exists_fn=lambda p: True)
    ctl.set_command({"tx": {"action": "start", "file": "c.mp4", "freq_mhz": 5800}})
    ctl.run_tx(ctl.pending())                        # must NOT raise
    assert states[-1]["status"] == "idle"
    assert states[-1]["error"] and "bladeRF busy" in states[-1]["error"]


def test_stop_during_render_skips_transmit(tmp_path):
    from txconfig import TxConfig
    from tx_controller import TxController
    states = []; tx_called = []
    class _Pub:
        def publish_txstate(self, ts, s): states.append(dict(s))
    class _R:
        def set_frequency(self, hz): pass
        def set_gain(self, db): pass
        def close(self): pass
    ctl_ref = [None]
    def render_fn(*a, **k):
        ctl_ref[0].set_command({"tx": {"action": "stop"}})   # Stop arrives DURING the (blocking) render
    def transmit_fn(radio, path, block, stop_check): tx_called.append(1)
    cfg = TxConfig(tx_enabled=True, tx_dir=str(tmp_path), tx_cache_bin=str(tmp_path / "b.bin"))
    ctl = TxController(cfg, _Pub(), render_fn=render_fn, open_tx_fn=lambda *a, **k: _R(),
                       transmit_fn=transmit_fn, clock=lambda: 1000, exists_fn=lambda p: True)
    ctl_ref[0] = ctl
    ctl.set_command({"tx": {"action": "start", "file": "c.mp4", "freq_mhz": 5800}})
    ctl.run_tx(ctl.pending())
    assert tx_called == []                                    # Stop during render -> never transmitted
    assert states[-1]["status"] == "idle" and states[-1]["active"] is False


def test_changed_param_triggers_rerender(tmp_path):
    ctl, pub, renders, radios, tx = _mk(tmp_path)
    ctl.set_command({"tx": {"action": "start", "file": "clip.mp4", "freq_mhz": 5800}})
    ctl.run_tx(ctl.pending())
    ctl.set_command({"tx": {"action": "start", "file": "clip.mp4", "freq_mhz": 5800,
                             "deviation_mhz": 6}})
    ctl.run_tx(ctl.pending())
    assert len(renders) == 2                              # changed deviation -> re-rendered
