"""Past runner health: how long each runner was reporting, and the stretches when it was silent.

Pure functions over health records (dicts with "hostname" and "timestamp_utc"), so they can be tested with fixtures.
"""
from datetime import datetime

SILENT_AFTER_S = 180  # the same threshold predict.py uses to call a runner "not reporting"


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def bucket_minutes(range_minutes: int) -> int:
    """How coarse to average the chart so a long range stays readable (and a short one stays detailed)."""
    for limit, step in ((60, 1), (360, 5), (1440, 15), (4320, 60)):
        if range_minutes <= limit:
            return step
    return 120


def silent_periods(records: list[dict], now: datetime, window_start: datetime, after_s: int = SILENT_AFTER_S) -> list[dict]:
    """Stretches longer than after_s with no report, per runner. Includes one that is still going on now and one
    that began before the window (the runner's first report in the window came late)."""
    by_host: dict[str, list[datetime]] = {}
    for r in records:
        by_host.setdefault(r["hostname"], []).append(_ts(r["timestamp_utc"]))
    out = []
    for host, times in sorted(by_host.items()):
        times.sort()
        edges = [window_start] + times + [now]
        for i, (a, b) in enumerate(zip(edges, edges[1:])):
            seconds = (b - a).total_seconds()
            if seconds > after_s:
                out.append({"host": host, "start": a, "end": b, "seconds": seconds,
                            "ongoing": i == len(edges) - 2, "before_window": i == 0})
    return out


def availability(periods: list[dict], host: str, window_start: datetime, now: datetime) -> float | None:
    """Percent of the window in which the runner was reporting."""
    total = (now - window_start).total_seconds()
    if total <= 0:
        return None
    silent = sum(p["seconds"] for p in periods if p["host"] == host)
    return round(100 * max(0.0, total - silent) / total, 1)


def duration_text(seconds: float) -> str:
    minutes = int(round(seconds / 60))
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} h {rest} min" if rest else f"{hours} h"
    days, hours = divmod(hours, 24)
    return f"{days} d {hours} h" if hours else f"{days} d"
