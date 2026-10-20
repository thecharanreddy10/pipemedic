import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pool_alerts  # noqa: E402
from notify import render_pool_email  # noqa: E402
from pool_status import analyze_pool  # noqa: E402
from watcher import Config, Watcher  # noqa: E402

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def finding(level="warning", msg="a1 is offline."):
    return {"level": level, "message": msg, "suggestion": "Start it."}


# ---------------- pure logic ----------------
def test_collect_keeps_only_warning_and_critical_and_keys_offline_agents_by_name():
    pool = {"findings": [
        {"level": "warning", "code": "agent-offline", "agent": "a1", "message": "a1 is offline.", "suggestion": "s"},
        {"level": "warning", "code": "agent-offline", "agent": "a2", "message": "a2 is offline.", "suggestion": "s"},
        {"level": "info", "code": "version-skew", "message": "m", "suggestion": "s"},
    ]}
    health = [{"level": "critical", "host": "vm1", "metric": "disk", "message": "vm1 disk", "suggestion": "s"}]
    assert set(pool_alerts.collect(pool, health)) == {"pool:agent-offline:a1", "pool:agent-offline:a2", "health:disk:vm1"}


def test_a_problem_is_announced_on_the_second_check_and_then_stays_quiet():
    cur = {"k": finding()}
    state, raised, _ = pool_alerts.update({}, cur, "t1")
    assert raised == []                                  # one check is not enough
    state, raised, _ = pool_alerts.update(state, cur, "t2")
    assert [r["key"] for r in raised] == ["k"]
    state, raised, resolved = pool_alerts.update(state, cur, "t3")
    assert raised == [] and resolved == []               # same problem, no repeat


def test_a_one_check_blip_is_never_announced_or_resolved():
    state, raised, _ = pool_alerts.update({}, {"k": finding()}, "t1")
    state, raised, resolved = pool_alerts.update(state, {}, "t2")
    assert raised == [] and resolved == [] and state == {}


def test_a_problem_resolves_after_two_clear_checks_and_can_come_back():
    cur = {"k": finding()}
    state, _, _ = pool_alerts.update({}, cur, "t1")
    state, raised, _ = pool_alerts.update(state, cur, "t2")
    state, _, resolved = pool_alerts.update(state, {}, "t3")
    assert resolved == [] and "k" in state               # one clear check: still open
    state, _, resolved = pool_alerts.update(state, {}, "t4")
    assert [r["key"] for r in resolved] == ["k"] and state == {}
    state, _, _ = pool_alerts.update(state, cur, "t5")
    state, raised, _ = pool_alerts.update(state, cur, "t6")
    assert [r["key"] for r in raised] == ["k"]           # a recurrence is announced again


def test_a_flap_during_the_clear_window_does_not_announce_twice():
    cur = {"k": finding()}
    state, _, _ = pool_alerts.update({}, cur, "t1")
    state, _, _ = pool_alerts.update(state, cur, "t2")
    state, _, _ = pool_alerts.update(state, {}, "t3")
    state, raised, resolved = pool_alerts.update(state, cur, "t4")
    assert raised == [] and resolved == []


def test_a_warning_that_becomes_critical_is_announced_again_once():
    state, _, _ = pool_alerts.update({}, {"k": finding("warning")}, "t1")
    state, raised, _ = pool_alerts.update(state, {"k": finding("warning")}, "t2")
    assert len(raised) == 1
    state, raised, _ = pool_alerts.update(state, {"k": finding("critical")}, "t3")
    assert len(raised) == 1 and raised[0]["escalated"]
    state, raised, _ = pool_alerts.update(state, {"k": finding("critical")}, "t4")
    assert raised == []
    state, raised, _ = pool_alerts.update(state, {"k": finding("warning")}, "t5")
    assert raised == []                                  # easing off is silent


def test_pool_status_names_the_offline_agent():
    agents = [{"name": "a1", "status": "online", "enabled": True}, {"name": "a2", "status": "offline", "enabled": True}]
    f = next(f for f in analyze_pool(agents, [], now=NOW)["findings"] if f["code"] == "agent-offline")
    assert f["agent"] == "a2"


def test_email_lists_new_and_cleared_problems_and_escapes_html():
    raised = [{"key": "k", "level": "critical", "message": "a1 <b>is offline</b>.", "suggestion": "Start it.", "since": "t", "escalated": False}]
    resolved = [{"key": "j", "message": "a2 is offline.", "since": "t0"}]
    subject, text, body = render_pool_email(raised, resolved, "pool-x")
    assert "1 new problem (critical)" in subject and "pool-x" in subject
    assert "Start it." in text and "Cleared" in text and "<b>is offline</b>" not in body
    assert "resolved (1)" in render_pool_email([], resolved, "pool-x")[0]


# ---------------- through the watcher ----------------
class Store:
    def __init__(self):
        self.state, self.saved = None, 0
        self.seen = {}

    def get_setting(self, *a, **k): return None
    def pool_alert_state(self, scope=None): return self.state
    def save_pool_alert_state(self, state, scope=None): self.state, self.saved = state, self.saved + 1
    def initialised(self): return True
    def seen_ids(self): return set()
    def mark_seen(self, i, note=""): self.seen[i] = note
    def claim(self, i, note="processing"):
        if i in self.seen:
            return False
        self.seen[i] = note
        return True


class Ado:
    def __init__(self):
        self.online = True

    def pool_agents(self, pool):
        return [{"name": "a1", "status": "online" if self.online else "offline", "enabled": True, "version": "5.1.0"}]

    def pool_requests(self, pool): return []
    def agent_hosts(self, pool): return {"a1": "vm1"}
    def recent_builds(self, n): return []


def make(**cfg):
    sent = []
    store, ado = Store(), Ado()
    cfg = Config(health_api="x", pool_alerts=True, notify_on=True, notify_default_to="boss@example.com", **cfg)
    w = Watcher(cfg, ado, store, health_fetch=lambda api, host, start: [], now=lambda: NOW,
                mailer=lambda to, s, t, h: sent.append((to, s)) or "Succeeded")
    return w, store, ado, sent


def test_watcher_emails_an_offline_agent_once_and_a_resolved_note_once():
    w, store, ado, sent = make()
    ado.online = False
    w.run_once()
    assert sent == []                                    # first sighting
    out = w.run_once()
    # the only agent being down is two findings: no agent online (critical) and that agent offline (warning), in one email
    assert out["pool"]["raised"] == 2 and len(sent) == 1 and "2 new problems (critical)" in sent[0][1]
    for _ in range(3):
        w.run_once()
    assert len(sent) == 1                                # no repeats while it stays down
    ado.online = True
    w.run_once()
    assert len(sent) == 1                                # one clear check is not enough
    w.run_once()
    assert len(sent) == 2 and "resolved" in sent[1][1]


def test_watcher_saves_state_before_sending_so_a_failed_email_is_not_repeated():
    w, store, ado, sent = make()
    ado.online = False
    w.run_once()
    w.mailer = lambda *a: (_ for _ in ()).throw(RuntimeError("smtp down"))
    out = w.run_once()
    assert "email failed" in out["pool"]["email"]
    w.mailer = lambda to, s, t, h: sent.append((to, s)) or "ok"
    w.run_once()
    assert sent == []


def test_a_pool_check_error_never_stops_the_failure_investigation():
    w, store, ado, sent = make()
    ado.pool_agents = lambda pool: (_ for _ in ()).throw(RuntimeError("devops down"))
    out = w.run_once()
    assert "pool" not in out and out["handled"] == 0


def test_pool_alerts_are_off_unless_enabled():
    w, store, ado, sent = make()
    w.cfg.pool_alerts = False
    ado.online = False
    for _ in range(3):
        out = w.run_once()
    assert "pool" not in out and sent == [] and store.saved == 0


def test_a_stale_runner_is_reported_from_health_history():
    w, store, ado, sent = make()
    old = (NOW - timedelta(minutes=30)).isoformat()
    w.health_fetch = lambda api, host, start: [{"hostname": host, "timestamp_utc": old, "cpu_percent": 1.0}]
    w.run_once()
    w.run_once()
    assert len(sent) == 1 and "critical" in sent[0][1]
