import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pool_status import analyze_pool, render_pool  # noqa: E402

NOW = datetime(2026, 10, 16, 21, 0, 0, tzinfo=timezone.utc)


def agent(name, status="online", busy=False, version="5.280.0", enabled=True):
    return {"name": name, "status": status, "enabled": enabled, "version": version,
            "assignedRequest": {"definition": {"name": "p"}} if busy else None}


def queued(seconds_ago, name="p"):
    t = datetime.fromtimestamp(NOW.timestamp() - seconds_ago, timezone.utc).isoformat()
    return {"queueTime": t, "definition": {"name": name}}


def done(wait_s, name="p"):
    q = NOW.timestamp() - 600
    return {"queueTime": datetime.fromtimestamp(q, timezone.utc).isoformat(),
            "assignTime": datetime.fromtimestamp(q + wait_s, timezone.utc).isoformat(),
            "finishTime": datetime.fromtimestamp(q + wait_s + 30, timezone.utc).isoformat(),
            "result": "succeeded", "definition": {"name": name}}


def codes(s):
    return [f["code"] for f in s["findings"]]


def test_healthy_pool():
    s = analyze_pool([agent("a1"), agent("a2")], [done(2), done(4)], NOW)
    assert s["healthy"] and s["findings"] == []
    assert s["agents_online"] == 2 and s["avg_wait_s"] == 3.0


def test_queue_with_idle_agent_is_the_parallelism_case():
    s = analyze_pool([agent("a1", busy=True), agent("a2")], [queued(120), queued(90)], NOW)
    assert "queue-with-idle-agents" in codes(s)
    assert s["jobs_queued"] == 2 and s["oldest_wait_s"] == 120
    msg = next(f for f in s["findings"] if f["code"] == "queue-with-idle-agents")
    assert "parallel" in msg["suggestion"].lower() and "a2" in msg["message"]


def test_saturated_pool_asks_for_more_agents():
    s = analyze_pool([agent("a1", busy=True), agent("a2", busy=True)], [queued(60)], NOW)
    assert "capacity-saturated" in codes(s) and "queue-with-idle-agents" not in codes(s)


def test_no_agents_online_is_critical():
    s = analyze_pool([agent("a1", status="offline"), agent("a2", status="offline")], [], NOW)
    assert "no-agents-online" in codes(s)
    assert next(f for f in s["findings"] if f["code"] == "no-agents-online")["level"] == "critical"
    assert codes(s).count("agent-offline") == 2


def test_one_offline_agent_is_flagged():
    s = analyze_pool([agent("a1"), agent("a2", status="offline")], [], NOW)
    assert codes(s) == ["agent-offline"] and not s["healthy"]


def test_disabled_agent_counts_as_unavailable():
    s = analyze_pool([agent("a1"), agent("a2", enabled=False)], [], NOW)
    assert s["agents_online"] == 1 and "agent-offline" in codes(s)


def test_very_long_wait_is_critical():
    s = analyze_pool([agent("a1", busy=True)], [queued(400)], NOW)
    assert "long-queue-wait" in codes(s)


def test_short_queue_with_idle_agent_is_not_flagged():
    s = analyze_pool([agent("a1", busy=True), agent("a2")], [queued(5)], NOW)
    assert "queue-with-idle-agents" not in codes(s)


def test_version_skew_and_update_available_are_informational():
    s = analyze_pool([agent("a1", version="5.279.0"), agent("a2", version="5.280.0")], [], NOW, latest_version="5.281.0")
    assert "version-skew" in codes(s) and "update-available" in codes(s)
    assert s["healthy"]  # informational findings do not make the pool unhealthy


def test_render_mentions_findings():
    s = analyze_pool([agent("a1", busy=True), agent("a2")], [queued(120)], NOW)
    text = render_pool(s)
    assert "needs attention" in text and "queue-with-idle" not in text and "idle" in text
