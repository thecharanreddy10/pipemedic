import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from predict import analyze_health, render_alerts  # noqa: E402

NOW = datetime(2026, 10, 16, 22, 0, 0, tzinfo=timezone.utc)


def series(host, field, values, base=None, step_min=1.0, other=None):
    """Samples ending at NOW, one per step, with `field` taking the given values."""
    out = []
    n = len(values)
    for i, v in enumerate(values):
        t = NOW - timedelta(minutes=(n - 1 - i) * step_min)
        r = {"hostname": host, "timestamp_utc": t.isoformat(), "cpu_percent": 5.0,
             "memory_percent": 20.0, "disk_percent": 12.0}
        r[field] = v
        r.update(other or {})
        out.append(r)
    return out


def codes(alerts):
    return [(a["host"], a["metric"], a["level"]) for a in alerts]


def test_steady_values_raise_nothing():
    assert analyze_health(series("vm1", "disk_percent", [12.0] * 10), NOW) == []


def test_fast_disk_growth_is_critical_with_eta():
    # 40% rising to 60% now at +2 points per minute: (95 - 60) / 2 = 17.5 minutes left
    vals = [40 + 2 * i for i in range(11)]
    alerts = analyze_health(series("vm1", "disk_percent", vals), NOW)
    assert codes(alerts) == [("vm1", "disk", "critical")]
    assert 16 <= alerts[0]["eta_minutes"] <= 19


def test_slow_growth_far_from_limit_is_not_flagged():
    vals = [20 + 0.05 * i for i in range(20)]  # 0.05 points/min is noise
    assert analyze_health(series("vm1", "disk_percent", vals), NOW) == []


def test_moderate_growth_is_a_warning():
    vals = [50 + 0.5 * i for i in range(20)]  # 59.5% now, +0.5/min: about 71 min left
    alerts = analyze_health(series("vm1", "disk_percent", vals), NOW)
    assert codes(alerts) == [("vm1", "disk", "warning")]


def test_growth_that_just_started_after_a_long_flat_stretch_is_caught():
    vals = [11.0] * 30 + [11 + 3 * i for i in range(1, 8)]  # flat for 30 min, then +3 points/min
    alerts = analyze_health(series("vm1", "disk_percent", vals), NOW)
    assert codes(alerts) == [("vm1", "disk", "critical")]


def test_noisy_data_is_ignored():
    vals = [30, 70, 31, 69, 32, 68, 33, 67, 34, 66]
    assert analyze_health(series("vm1", "disk_percent", vals), NOW) == []


def test_too_few_samples_are_ignored():
    assert analyze_health(series("vm1", "disk_percent", [50, 60, 70]), NOW) == []


def test_memory_leak_is_detected():
    vals = [40 + 1.5 * i for i in range(12)]
    alerts = analyze_health(series("vm1", "memory_percent", vals), NOW)
    assert ("vm1", "memory", "critical") in codes(alerts) or ("vm1", "memory", "warning") in codes(alerts)


def test_sustained_cpu_warns():
    alerts = analyze_health(series("vm1", "cpu_percent", [97.0] * 8), NOW)
    assert codes(alerts) == [("vm1", "cpu", "warning")]


def test_one_cpu_spike_does_not_warn():
    vals = [5.0] * 7 + [99.0]
    assert analyze_health(series("vm1", "cpu_percent", vals), NOW) == []


def test_runner_that_stopped_reporting_is_critical():
    old = NOW + timedelta(minutes=10)
    alerts = analyze_health(series("vm1", "cpu_percent", [5.0] * 8), old)
    assert codes(alerts) == [("vm1", "reporting", "critical")]


def test_each_host_is_judged_separately_and_sorted_by_severity():
    recs = series("vm1", "cpu_percent", [97.0] * 8) + series("vm2", "disk_percent", [40 + 2 * i for i in range(11)])
    alerts = analyze_health(recs, NOW)
    assert codes(alerts)[0] == ("vm2", "disk", "critical")
    assert ("vm1", "cpu", "warning") in codes(alerts)


def test_render_when_clear():
    assert "No runner is trending" in render_alerts([])
