from collector import parse_throttled, parse_meminfo, parse_loadavg, parse_uptime, millideg_to_c


def test_parse_throttled_flags():
    # bit0 undervolt-now, bit2 throttled-now, bit16 undervolt-ever, bit18 throttled-ever
    assert parse_throttled("throttled=0x0") == {"throttled": False, "throttled_ever": False, "throttle_flags": "0x0"}
    now = parse_throttled("0x5")            # bits 0 and 2 set -> throttled now
    assert now["throttled"] is True and now["throttled_ever"] is False
    ever = parse_throttled("0x50000")       # bits 16 and 18 set -> happened before
    assert ever["throttled"] is False and ever["throttled_ever"] is True
    assert parse_throttled("garbage") is None
    assert parse_throttled("") is None


def test_parse_meminfo():
    text = "MemTotal:        4096000 kB\nMemFree: 100000 kB\nMemAvailable:    3096000 kB\n"
    out = parse_meminfo(text)
    assert out["mem_total_mb"] == 4000
    assert out["mem_used_mb"] == 977          # (4096000-3096000)/1024 = 976.56 -> round -> 977
    assert out["mem_used_pct"] == 24
    assert parse_meminfo("nonsense") is None


def test_parse_loadavg_normalized():
    assert parse_loadavg("2.00 1.5 1.0 1/200 1234", 4) == 50    # 2.0/4 cores = 50%
    assert parse_loadavg("4.0 0 0", 4) == 100
    assert parse_loadavg("", 4) is None


def test_parse_uptime():
    assert parse_uptime("123456.78 1000.0") == 123456
    assert parse_uptime("bad") is None


def test_millideg_to_c():
    assert millideg_to_c("62400\n") == 62.4
    assert millideg_to_c("bad") is None
