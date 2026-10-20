"""Predictive alerts from runner health history.

Fits a straight line to recent disk and memory readings and warns when a runner is on course to run out,
before jobs start failing. Also flags sustained high CPU and runners that have stopped reporting.
"""
from datetime import datetime, timezone

LIMIT_PCT = 95.0
MIN_SAMPLES = 6
MIN_SPAN_MIN = 5.0
RECENT_SAMPLES = 10
MIN_SLOPE = 0.2          # percentage points per minute; smaller trends are noise
WARN_ETA_MIN = 120
CRIT_ETA_MIN = 30
CPU_SUSTAINED = 85.0
CPU_SAMPLES = 5
SILENT_AFTER_S = 180


def _ts(v: str) -> float:
    return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()


def _fit(points: list[tuple[float, float]]):
    """Least-squares slope (per minute) and r-squared for (t_seconds, value) points."""
    n = len(points)
    xs = [p[0] / 60 for p in points]
    ys = [p[1] for p in points]
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return 0.0, 0.0
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (my + slope * (x - mx))) ** 2 for x, y in zip(xs, ys))
    return slope, (1 - ss_res / ss_tot) if ss_tot else 0.0


def analyze_health(records: list[dict], now: datetime | None = None) -> list[dict]:
    now_s = (now or datetime.now(timezone.utc)).timestamp()
    by_host: dict[str, list[dict]] = {}
    for r in records:
        by_host.setdefault(r["hostname"], []).append(r)

    alerts = []
    for host, rs in sorted(by_host.items()):
        rs.sort(key=lambda r: _ts(r["timestamp_utc"]))
        last = _ts(rs[-1]["timestamp_utc"])

        if now_s - last > SILENT_AFTER_S:
            alerts.append({"level": "critical", "host": host, "metric": "reporting",
                           "message": f"{host} has not reported for {int((now_s - last) // 60)} min.",
                           "suggestion": "Check the runner VM is running and its health reporting timer is active.",
                           "eta_minutes": None})
            continue

        for field, label in (("disk_percent", "disk"), ("memory_percent", "memory")):
            pts = [(_ts(r["timestamp_utc"]), r[field]) for r in rs if isinstance(r.get(field), (int, float))]
            current = pts[-1][1] if pts else 0
            if current >= LIMIT_PCT:
                continue
            # Fit the whole window and the most recent samples, so a change that just started is not
            # diluted by a long flat stretch before it. Keep whichever gives the earlier warning.
            best = None
            for window in (pts, pts[-RECENT_SAMPLES:]):
                if len(window) < MIN_SAMPLES or (window[-1][0] - window[0][0]) / 60 < MIN_SPAN_MIN:
                    continue
                slope, r2 = _fit(window)
                if slope < MIN_SLOPE or r2 < 0.6:
                    continue
                eta = (LIMIT_PCT - current) / slope
                if eta <= WARN_ETA_MIN and (best is None or eta < best[0]):
                    best = (eta, slope)
            if best is None:
                continue
            eta, slope = best
            fix = ("Clean old job folders and caches, or enlarge the disk."
                   if label == "disk" else "Look for a leaking process, and restart it or the agent service.")
            alerts.append({
                "level": "critical" if eta <= CRIT_ETA_MIN else "warning", "host": host, "metric": label,
                "message": f"{host} {label} is at {current:.0f}% and rising {slope:.1f} points/min; "
                           f"about {eta:.0f} min until {LIMIT_PCT:.0f}%.",
                "suggestion": fix, "eta_minutes": round(eta, 1)})

        cpu = [r["cpu_percent"] for r in rs[-CPU_SAMPLES:] if isinstance(r.get("cpu_percent"), (int, float))]
        if len(cpu) == CPU_SAMPLES and min(cpu) >= CPU_SUSTAINED:
            alerts.append({"level": "warning", "host": host, "metric": "cpu",
                           "message": f"{host} CPU has stayed above {CPU_SUSTAINED:.0f}% for {CPU_SAMPLES} samples.",
                           "suggestion": "Find the busy process; jobs on this runner are likely to slow down or time out.",
                           "eta_minutes": None})
    order = {"critical": 0, "warning": 1}
    return sorted(alerts, key=lambda a: (order.get(a["level"], 2), a["host"]))


def render_alerts(alerts: list[dict]) -> str:
    if not alerts:
        return "=== Predictive alerts ===\nNo runner is trending towards trouble."
    lines = ["=== Predictive alerts ==="]
    for a in alerts:
        lines += [f"[{a['level']}] {a['message']}", f"    -> {a['suggestion']}"]
    return "\n".join(lines)
