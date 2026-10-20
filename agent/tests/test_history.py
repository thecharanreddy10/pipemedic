import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from history import availability, bucket_minutes, duration_text, silent_periods  # noqa: E402

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def rec(host, minutes_ago):
    return {"hostname": host, "timestamp_utc": (NOW - timedelta(minutes=minutes_ago)).isoformat()}


def test_a_steady_runner_has_no_silent_period():
    records = [rec("vm1", m) for m in range(0, 60)]
    assert silent_periods(records, NOW, NOW - timedelta(minutes=60)) == []


def test_a_gap_in_the_middle_is_found_with_its_length():
    records = [rec("vm1", m) for m in list(range(0, 20)) + list(range(50, 60))]
    gaps = silent_periods(records, NOW, NOW - timedelta(minutes=60))
    assert len(gaps) == 1 and gaps[0]["host"] == "vm1"
    assert round(gaps[0]["seconds"] / 60) == 31 and not gaps[0]["ongoing"] and not gaps[0]["before_window"]


def test_a_runner_that_stopped_reporting_has_an_ongoing_gap():
    gaps = silent_periods([rec("vm1", m) for m in range(30, 60)], NOW, NOW - timedelta(minutes=60))
    assert len(gaps) == 1 and gaps[0]["ongoing"] and round(gaps[0]["seconds"] / 60) == 30


def test_a_runner_whose_first_report_comes_late_has_a_gap_before_the_window_data():
    gaps = silent_periods([rec("vm1", m) for m in range(0, 20)], NOW, NOW - timedelta(minutes=60))
    assert len(gaps) == 1 and gaps[0]["before_window"] and round(gaps[0]["seconds"] / 60) == 41


def test_runners_are_judged_separately():
    records = [rec("vm1", m) for m in range(0, 60)] + [rec("vm2", m) for m in range(30, 60)]
    gaps = silent_periods(records, NOW, NOW - timedelta(minutes=60))
    assert [g["host"] for g in gaps] == ["vm2"]


def test_availability_is_the_share_of_the_window_with_reports():
    start = NOW - timedelta(minutes=60)
    gaps = silent_periods([rec("vm1", m) for m in range(30, 60)], NOW, start)
    assert availability(gaps, "vm1", start, NOW) == 50.0
    assert availability([], "vm1", start, NOW) == 100.0
    assert availability([], "vm1", NOW, NOW) is None


def test_chart_buckets_get_coarser_for_longer_ranges():
    assert [bucket_minutes(m) for m in (30, 60, 360, 1440, 4320, 10080)] == [1, 1, 5, 15, 60, 120]


def test_durations_read_naturally():
    assert [duration_text(s) for s in (300, 3600, 5400, 86400, 90000)] == ["5 min", "1 h", "1 h 30 min", "1 d", "1 d 1 h"]
