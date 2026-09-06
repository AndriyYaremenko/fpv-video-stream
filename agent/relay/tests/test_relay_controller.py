from relayconfig import RelayConfig
from relay_controller import RelayController


class _FakePublisher:
    def __init__(self): self.states = []
    def publish_relaystate(self, ts, state): self.states.append((ts, dict(state)))


class _FakeRadio:
    def __init__(self): self.closed = False; self.args = None
    def close(self): self.closed = True


def _cfg(**kw):
    base = dict(relay_enabled=True, armed=True, dst_mhz=5771.0, max_s=100.0, guard_mhz=25.0,
                lost_db=12.0, lost_s=5.0)
    base.update(kw)
    return RelayConfig(**base)


def _mk(cfg=None, levels=None, manual=None, clock=None, open_fail=False):
    """Controller on fakes. loop_fn feeds `levels` (raw RMS per block) through on_level until
    stop_check() trips or the list runs out; `consumed` tells how many blocks were fed."""
    radios, consumed = [], {"n": 0}
    def open_fn(src_hz, dst_hz, fs_hz, rx_gain, tx_gain, block):
        if open_fail: raise RuntimeError("no device")
        r = _FakeRadio(); r.args = (src_hz, dst_hz, fs_hz, rx_gain, tx_gain, block); radios.append(r); return r
    def loop_fn(radio, block, stop_check, on_level, target):
        seq = list(levels if levels is not None else [300.0] * 3)
        while not stop_check():
            if not seq: break
            on_level(seq.pop(0)); consumed["n"] += 1
    pub = _FakePublisher()
    ctl = RelayController(cfg or _cfg(), pub, open_fn=open_fn, loop_fn=loop_fn, reset=lambda: None,
                          manual_pending=manual or (lambda: False), clock=clock or (lambda: 1000.0))
    return ctl, pub, radios, consumed


def _stepper(step):
    t = [1000.0]
    def clk(): t[0] += step; return t[0]
    return clk


# ---- source selection ----
def test_update_hits_picks_strongest_outside_guard_band():
    ctl, pub, _, _ = _mk()
    ctl.update_hits([("2.4G", 2465.0, 20.0), ("3.3G", 3470.0, 34.5), ("5.8G", 5780.0, 60.0)])
    req = ctl.pending()
    assert req["src_mhz"] == 3470.0 and req["band"] == "3.3G" and req["dst_mhz"] == 5771.0
    assert req["sync_snr_db"] == 34.5              # 5780 sits inside 5771±25 -> excluded
    assert ctl.pending() is None                   # consumed once


def test_update_hits_none_when_empty_disarmed_or_manual_pending():
    ctl, _, _, _ = _mk(); ctl.update_hits([]); assert not ctl.has_pending()
    ctl, _, _, _ = _mk(cfg=_cfg(armed=False)); ctl.update_hits([("3.3G", 3470.0, 30.0)]); assert not ctl.has_pending()
    ctl, _, _, _ = _mk(manual=lambda: True); ctl.update_hits([("3.3G", 3470.0, 30.0)]); assert not ctl.has_pending()
    ctl, _, _, _ = _mk(); ctl.update_hits([("x", None, 1.0), ("y", "bad", 2.0)]); assert not ctl.has_pending()


def test_update_hits_respects_src_bands_allowlist_and_min_sync():
    ctl, _, _, _ = _mk(cfg=_cfg(src_bands="1.2G,2.4G,3.3G", min_sync_db=15.0))
    # 5.8G hit is the strongest but sits in the target's own band -> skipped; 2.4G hit too weak a lock
    ctl.update_hits([("5.8G", 5720.0, 43.0), ("2.4G", 2447.0, 10.5), ("3.3G", 3470.0, 34.5)])
    assert ctl.pending()["src_mhz"] == 3470.0
    ctl.update_hits([("5.8G", 5720.0, 43.0), ("2.4G", 2447.0, 10.5)])
    assert not ctl.has_pending()                   # nothing eligible
    ctl2, _, _, _ = _mk()                          # defaults: all bands, no sync floor -> 5.8G wins
    ctl2.update_hits([("5.8G", 5720.0, 43.0), ("3.3G", 3470.0, 34.5)])
    assert ctl2.pending()["src_mhz"] == 5720.0


# ---- commands ----
def test_arm_sets_dst_and_publishes_disarm_clears_pending():
    ctl, pub, _, _ = _mk(cfg=_cfg(armed=False))
    ctl.set_command({"relay": {"action": "arm", "dst_mhz": 5865}})
    assert ctl.armed is True and ctl.dst_mhz == 5865.0
    assert pub.states[-1][1]["armed"] is True and pub.states[-1][1]["dst_mhz"] == 5865.0
    ctl.set_command({"relay": {"action": "arm", "dst_mhz": 99999}})   # out of range -> keep 5865
    assert ctl.dst_mhz == 5865.0
    ctl.update_hits([("3.3G", 3470.0, 30.0)]); assert ctl.has_pending()
    ctl.set_command({"relay": {"action": "disarm"}})
    assert ctl.armed is False and not ctl.has_pending()
    assert pub.states[-1][1]["armed"] is False and pub.states[-1][1]["status"] == "idle"
    for bad in (None, {}, {"relay": "arm"}, {"relay": {"action": "fly"}}):
        ctl.set_command(bad)                        # never raises


# ---- session ----
def test_run_relay_opens_full_duplex_publishes_relaying_then_idle():
    ctl, pub, radios, consumed = _mk()
    ctl.update_hits([("3.3G", 3470.0, 30.0)])
    err = ctl.run_relay(ctl.pending())
    assert err is None and len(radios) == 1 and radios[0].closed is True
    assert radios[0].args == (int(3470e6), int(5771e6), 20_000_000, 40, 60, 32768)
    on = next(s for _, s in pub.states if s["status"] == "relaying")
    assert on["active"] is True and on["src_mhz"] == 3470.0 and on["band"] == "3.3G"
    assert on["dst_mhz"] == 5771.0 and on["until_ts"] == on["since_ts"] + 100
    assert pub.states[-1][1]["active"] is False and pub.states[-1][1]["status"] == "idle"
    assert pub.states[-1][1]["error"] is None and pub.states[-1][1]["armed"] is True


def test_run_relay_stops_when_source_lost():
    # clock +1s per call: reference window (1s) closes on the first feeds; then RMS 20 dB down
    # (300 -> 30) for well over lost_s=5s -> loop exits long before the 60 levels run out.
    ctl, pub, _, consumed = _mk(levels=[300.0] * 3 + [30.0] * 57, clock=_stepper(1.0))
    ctl.update_hits([("3.3G", 3470.0, 30.0)])
    ctl.run_relay(ctl.pending())
    assert 0 < consumed["n"] <= 10                  # empirically stops at 6 blocks
    assert pub.states[-1][1]["status"] == "idle" and pub.states[-1][1]["error"] is None


def test_run_relay_keeps_going_while_level_holds_then_deadline():
    # constant level, clock +30s per call -> the max_s=100 deadline trips within a few checks
    ctl, pub, _, consumed = _mk(levels=[300.0] * 100, clock=_stepper(30.0))
    ctl.update_hits([("3.3G", 3470.0, 30.0)])
    ctl.run_relay(ctl.pending())
    assert consumed["n"] <= 3                        # empirically stops after 2 blocks
    assert pub.states[-1][1]["status"] == "idle"


def test_run_relay_stops_immediately_when_reference_below_min_rms():
    # source already dead when the session opens: RMS 5.0 the whole way -> reference (median of the
    # first second) lands at 5.0, well under default min_rms=40 -> lost trips right when ref is set.
    ctl, pub, _, consumed = _mk(levels=[5.0] * 60, clock=_stepper(1.0))
    ctl.update_hits([("3.3G", 3470.0, 30.0)])
    ctl.run_relay(ctl.pending())
    assert consumed["n"] <= 3
    assert pub.states[-1][1]["status"] == "idle" and pub.states[-1][1]["error"] is None


def test_run_relay_healthy_source_unaffected_by_min_rms_floor():
    # reference well above min_rms=40 (300 and 200 used elsewhere) -> min-RMS floor never trips;
    # covered implicitly by test_run_relay_opens_full_duplex_publishes_relaying_then_idle (ref=300)
    # and test_run_relay_publishes_rx_level_periodically (ref=200); explicit guard here too.
    ctl, pub, _, consumed = _mk(levels=[200.0] * 20, clock=_stepper(1.0))
    ctl.update_hits([("3.3G", 3470.0, 30.0)])
    ctl.run_relay(ctl.pending())
    assert consumed["n"] == 20
    assert pub.states[-1][1]["status"] == "idle" and pub.states[-1][1]["error"] is None


def test_run_relay_stops_on_manual_command_and_on_disarm():
    flag = {"manual": False}
    ctl, pub, _, consumed = _mk(levels=[300.0] * 50, manual=lambda: flag["manual"])
    ctl.update_hits([("3.3G", 3470.0, 30.0)]); req = ctl.pending()
    # flip "manual pending" after the first block via a wrapped publisher hook
    orig = ctl._loop_fn
    def loop(radio, block, stop_check, on_level, target):
        def lvl(rms): on_level(rms); flag["manual"] = True
        orig(radio, block, stop_check, lvl, target)
    ctl._loop_fn = loop
    ctl.run_relay(req)
    assert consumed["n"] == 1
    # disarm mid-session
    ctl2, pub2, _, consumed2 = _mk(levels=[300.0] * 50)
    ctl2.update_hits([("3.3G", 3470.0, 30.0)]); req2 = ctl2.pending()
    orig2 = ctl2._loop_fn
    def loop2(radio, block, stop_check, on_level, target):
        def lvl(rms): on_level(rms); ctl2.set_command({"relay": {"action": "disarm"}})
        orig2(radio, block, stop_check, lvl, target)
    ctl2._loop_fn = loop2
    ctl2.run_relay(req2)
    assert consumed2["n"] == 1 and ctl2.armed is False
    assert pub2.states[-1][1]["status"] == "idle" and pub2.states[-1][1]["armed"] is False


def test_run_relay_publishes_rx_level_periodically():
    ctl, pub, _, _ = _mk(levels=[200.0] * 20, clock=_stepper(1.0))
    ctl.update_hits([("3.3G", 3470.0, 30.0)])
    ctl.run_relay(ctl.pending())
    lv = [s["rx_level_db"] for _, s in pub.states if s["status"] == "relaying" and s["rx_level_db"] is not None]
    assert lv and all(abs(v - round(20 * __import__("math").log10(200 / 2047.0), 1)) < 0.2 for v in lv)


def test_open_failure_is_captured_not_raised():
    ctl, pub, radios, _ = _mk(open_fail=True)
    ctl.update_hits([("3.3G", 3470.0, 30.0)])
    err = ctl.run_relay(ctl.pending())
    assert err == "no device" and radios == []
    assert pub.states[-1][1]["status"] == "idle" and pub.states[-1][1]["error"] == "no device"


def test_arm_after_open_failure_clears_stale_error():
    ctl, pub, _, _ = _mk(open_fail=True)
    ctl.update_hits([("3.3G", 3470.0, 30.0)])
    ctl.run_relay(ctl.pending())
    assert pub.states[-1][1]["error"] == "no device"
    ctl.set_command({"relay": {"action": "arm", "dst_mhz": 5771}})
    assert pub.states[-1][1]["error"] is None


def test_run_relay_skips_open_when_disarmed_or_manual_before_start():
    # request was chosen while armed, but disarm landed before run_relay actually starts
    ctl, pub, radios, consumed = _mk()
    ctl.update_hits([("3.3G", 3470.0, 30.0)]); req = ctl.pending()
    ctl.armed = False
    n_before = len(pub.states)
    result = ctl.run_relay(req)
    assert result is None and radios == [] and consumed["n"] == 0
    assert len(pub.states) == n_before             # no "relaying" state published

    # request was chosen, then a manual (TX/view) command pended before run_relay starts
    flag = {"manual": True}
    ctl2, pub2, radios2, consumed2 = _mk(manual=lambda: flag["manual"])
    flag["manual"] = False
    ctl2.update_hits([("3.3G", 3470.0, 30.0)]); req2 = ctl2.pending()
    flag["manual"] = True
    n_before2 = len(pub2.states)
    result2 = ctl2.run_relay(req2)
    assert result2 is None and radios2 == [] and consumed2["n"] == 0
    assert len(pub2.states) == n_before2


def test_announce_republishes_last_state_and_survives_no_publisher():
    ctl, pub, _, _ = _mk()
    ctl.announce()
    assert pub.states[-1][1]["status"] == "idle" and pub.states[-1][1]["armed"] is True
    ctl2 = RelayController(_cfg(), None, open_fn=lambda *a: None, loop_fn=lambda *a: None)
    ctl2.announce(); ctl2.set_command({"relay": {"action": "disarm"}})    # no publisher: no raise
