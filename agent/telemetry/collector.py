"""Host telemetry collection. Pure parsers (take raw text) + fail-soft readers + payload builder.
Flat-import module (run from agent/telemetry/), mirroring agent/scan/."""


def parse_throttled(hex_str):
    """`vcgencmd get_throttled` -> {throttled, throttled_ever, throttle_flags}. None on garbage."""
    if not hex_str:
        return None
    s = hex_str.strip()
    if "=" in s:
        s = s.split("=", 1)[1].strip()
    try:
        bits = int(s, 16)
    except (ValueError, TypeError):
        return None
    UNDERVOLT_NOW, THROTTLED_NOW = 1 << 0, 1 << 2
    UNDERVOLT_EVER, THROTTLED_EVER = 1 << 16, 1 << 18
    return {
        "throttled": bool(bits & (UNDERVOLT_NOW | THROTTLED_NOW)),
        "throttled_ever": bool(bits & (UNDERVOLT_EVER | THROTTLED_EVER)),
        "throttle_flags": hex(bits),
    }


def parse_meminfo(text):
    """/proc/meminfo -> {mem_total_mb, mem_used_mb, mem_used_pct}. None if unparseable."""
    vals = {}
    for line in (text or "").splitlines():
        if ":" not in line:
            continue
        k, rest = line.split(":", 1)
        num = rest.strip().split()
        if num:
            try:
                vals[k.strip()] = int(num[0])   # kB
            except ValueError:
                pass
    total = vals.get("MemTotal")
    avail = vals.get("MemAvailable")
    if not total or avail is None:
        return None
    used_kb = max(0, total - avail)
    return {
        "mem_total_mb": round(total / 1024),
        "mem_used_mb": round(used_kb / 1024),
        "mem_used_pct": round(100 * used_kb / total),
    }


def parse_loadavg(text, ncpu):
    """/proc/loadavg 1-min field normalized to % of ncpu cores. None if unparseable."""
    try:
        one = float((text or "").split()[0])
    except (ValueError, IndexError):
        return None
    n = ncpu if ncpu and ncpu > 0 else 1
    return round(100 * one / n)


def parse_uptime(text):
    """/proc/uptime first field (seconds, int). None if unparseable."""
    try:
        return int(float((text or "").split()[0]))
    except (ValueError, IndexError):
        return None


def millideg_to_c(text):
    """/sys/.../temp millidegrees -> float °C (1 decimal). None if bad."""
    try:
        return round(int((text or "").strip()) / 1000.0, 1)
    except (ValueError, TypeError):
        return None
