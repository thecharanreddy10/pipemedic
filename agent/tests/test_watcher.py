import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import remediate  # noqa: E402
from notify import recipient_for, render_email, run_url  # noqa: E402
from watcher import Config, Watcher  # noqa: E402


class FakeStore:
    def __init__(self):
        self.init, self.seen, self.diags, self.hist = False, {}, {}, []

    def initialised(self): return self.init
    def set_initialised(self): self.init = True
    def seen_ids(self): return set(self.seen)
    def mark_seen(self, i, note=""): self.seen[i] = note
    def claim(self, i, note="processing"):
        if i in self.seen:
            return False
        self.seen[i] = note
        return True
    def history(self): return list(self.hist)
    def append_history(self, e): self.hist.append({"time": datetime.now(timezone.utc).isoformat(), **e})
    def save_diagnosis(self, d): self.diags[d["build_id"]] = d


class FakeAdo:
    def __init__(self, builds):
        self.builds, self.queued = builds, []

    def recent_builds(self, n): return self.builds
    def agent_hosts(self, pool): return {"a1": "vm1"}
    def queue_run(self, pid, ref): self.queued.append(pid); return 900 + len(self.queued)
    def agent_busy(self, pool, name): return False
    def create_work_item(self, *a, **k): return 1


def b(i, result="failed", pipeline=3):
    return {"id": i, "result": result, "sourceVersion": "abc", "definition": {"id": pipeline, "name": "p"}}


FAIL_LOG = "ERROR: required config file config/orders.prod.json not found\n##[error]Bash exited with code '1'."


def collect(ado, build, hosts):
    return {"pipeline": "p", "pipeline_id": 3, "build_id": build["id"], "failed_step": "Orders - validate", "issues": [],
            "log_text": FAIL_LOG, "agent_name": "a1", "hostname": "vm1", "start": "2026-10-16T20:10:00Z",
            "finish": "2026-10-16T20:11:00Z", "requested_for": "owner@example.com", "source_branch": "refs/heads/main"}


def healthy(api, host, start):
    return [{"timestamp_utc": "2026-10-16T20:10:30+00:00", "cpu_percent": 4.0, "memory_percent": 20.0,
             "disk_percent": 12, "disk_free_gb": 25, "agent_service_running": True}]


def make(builds, **cfg):
    sent = []
    store = FakeStore()
    remediate.configure_store(store)
    cfg = Config(health_api="x", notify_on=True, notify_default_to="boss@example.com", **cfg)
    w = Watcher(cfg, FakeAdo(builds), store, health_fetch=healthy, collect=collect,
                mailer=lambda to, s, t, h: sent.append((to, s)) or "Succeeded")
    return w, store, sent


def test_first_run_only_records_a_baseline_and_sends_nothing():
    w, store, sent = make([b(1), b(2, "succeeded")])
    out = w.run_once()
    assert out["baseline"] == 1 and sent == [] and store.init and 1 in store.seen


def test_new_failure_is_investigated_and_emailed_once():
    w, store, sent = make([b(1)])
    w.run_once()  # baseline
    w.ado.builds = [b(1), b(2)]
    out = w.run_once()
    assert out["handled"] == 1 and len(sent) == 1
    assert sent[0][0] == "owner@example.com" and "run 2" in sent[0][1]
    assert 2 in store.diags and store.diags[2]["verdict"] == "code-or-config"
    w.run_once()  # nothing new
    assert len(sent) == 1


def test_a_run_is_marked_handled_before_side_effects():
    w, store, sent = make([b(1)])
    w.run_once()
    w.ado.builds = [b(1), b(2)]
    seen_at_send = []
    w.mailer = lambda to, s, t, h: seen_at_send.append(2 in store.seen) or "ok"
    w.run_once()
    assert seen_at_send == [True]


def test_a_crash_while_processing_does_not_cause_a_repeat():
    w, store, sent = make([b(1)])
    w.run_once()
    w.ado.builds = [b(1), b(2)]
    w.collect = lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))
    assert w.run_once()["handled"] == 0
    assert 2 in store.seen and store.seen[2].startswith("error")
    w.collect = collect
    assert w.run_once()["handled"] == 0  # not retried forever


def test_dry_run_by_default_changes_nothing():
    w, store, sent = make([b(1)])
    w.run_once()
    w.ado.builds = [b(1), b(2)]
    w.health_fetch = lambda api, host, start: [{"timestamp_utc": "2026-10-16T20:10:30+00:00", "cpu_percent": 99.0,
                                                "memory_percent": 20.0, "disk_percent": 12, "disk_free_gb": 25,
                                                "agent_service_running": True}]
    w.process(b(2))
    assert w.ado.queued == [] and store.hist == []


def test_act_mode_runs_the_work_item_action_and_logs_it():
    w, store, sent = make([b(1)], act=True)
    w.run_once()
    w.ado.builds = [b(1), b(2)]
    w.run_once()
    assert any(h["action"] == "create-work-item" and h["status"] == "done" for h in store.hist)


def test_failures_are_capped_per_invocation():
    w, store, sent = make([b(1)])
    w.run_once()
    w.ado.builds = [b(i) for i in range(1, 12)]
    assert w.run_once()["handled"] == 5


def test_email_failure_is_recorded_not_raised():
    w, store, sent = make([b(1)])
    w.run_once()
    w.ado.builds = [b(1), b(2)]
    w.mailer = lambda *a: (_ for _ in ()).throw(RuntimeError("smtp down"))
    out = w.run_once()
    assert out["handled"] == 1 and "email failed" in out["runs"][0]["email"]


# ---------------- email content ----------------
def diag():
    return {"pipeline": "azure-pipelines-timeout", "build_id": 9, "failed_step": "Payments - gateway", "verdict": "runner",
            "root_cause": "The step hung while the runner was at 100% CPU.", "hostname": "vm1",
            "evidence": ["waiting for payment gateway sandbox..."], "suggestions": ["Free resources.", "Re-run."],
            "runner_health": {"samples": 5, "cpu_max": 100.0, "memory_max": 22.0, "disk_max": 12.0, "flags": ["high-cpu"]}}


def test_email_has_verdict_cause_fix_and_link():
    subject, text, html = render_email(diag(), run_url("Org", "Proj", 9), ["retry-run: dry-run"])
    assert "Runner problem" in subject and "run 9" in subject
    for needle in ("100% CPU", "Free resources.", "buildId=9", "retry-run"):
        assert needle in text and needle in html


def test_email_escapes_html_in_logs():
    d = diag()
    d["evidence"] = ["<script>alert(1)</script>"]
    assert "<script>" not in render_email(d, "http://x", None)[2]


def test_ai_text_is_labelled_and_errors_are_left_out():
    d = diag()
    d["ai"] = {"model": "gpt-5-mini via Azure AI Foundry", "likely_cause": "starved CPU"}
    assert "AI second opinion" in render_email(d, "u", None)[1]
    d["ai"] = {"error": "not signed in"}
    assert "AI second opinion" not in render_email(d, "u", None)[1]


def test_recipient_is_the_person_who_triggered_the_run_else_the_default():
    assert recipient_for({"requested_for": "dev@corp.com"}, "owner@corp.com") == "dev@corp.com"
    assert recipient_for({"requested_for": "Build Service"}, "owner@corp.com") == "owner@corp.com"
    assert recipient_for({}, "owner@corp.com") == "owner@corp.com"


def test_override_address_wins_over_the_person_who_triggered_the_run():
    w, store, sent = make([b(1)], notify_override_to="mailbox@example.com")
    w.run_once()
    w.ado.builds = [b(1), b(2)]
    w.run_once()
    assert sent[0][0] == "mailbox@example.com"


def test_the_owners_stored_fix_preference_overrides_the_app_setting():
    from watcher import Watcher, Config

    class S:
        def __init__(self, v): self.v = v
        def get_setting(self, name, default="", scope=None): return self.v

    for stored, want in [("open", "open"), ("OFF", "off"), ("propose", "propose"), ("", "propose"), ("nonsense", "propose")]:
        w = Watcher.__new__(Watcher)
        w.cfg, w.store = Config(fix_mode="propose"), S(stored)
        w._apply_owner_preferences()
        assert w.cfg.fix_mode == want, stored

    class Broken:
        def get_setting(self, *a, **k): raise RuntimeError("table down")
    w = Watcher.__new__(Watcher)
    w.cfg, w.store = Config(fix_mode="open"), Broken()
    w._apply_owner_preferences()
    assert w.cfg.fix_mode == "open"


def test_the_owners_stored_alert_address_overrides_and_empty_restores_the_app_setting():
    from watcher import Watcher, Config

    class S:
        def __init__(self, notify): self.notify = notify
        def get_setting(self, name, default="", scope=None):
            return self.notify if name == "notify_to" else ""

    for stored, want in [("owner@example.com", "owner@example.com"), ("not-an-email", "app@example.com"), (None, "app@example.com"), ("", "app@example.com")]:
        w = Watcher.__new__(Watcher)
        w.cfg, w.store, w._env_override = Config(notify_override_to="app@example.com"), S(stored), "app@example.com"
        w._apply_owner_preferences()
        assert w.cfg.notify_override_to == want, stored
    w = Watcher.__new__(Watcher)
    w.cfg, w.store, w._env_override = Config(notify_override_to="owner@example.com"), S(""), "app@example.com"
    w._apply_owner_preferences()
    assert w.cfg.notify_override_to == "app@example.com"


def test_only_the_actions_the_owner_allowed_run_for_real():
    from watcher import Watcher, Config

    class S:
        def __init__(self, actions): self.actions = actions
        def get_setting(self, name, default="", scope=None): return self.actions if name == "actions" else None

    cases = [("create-work-item", {"create-work-item"}), ("retry-run, nonsense", {"retry-run"}), ("", set()), (None, set())]
    for stored, want in cases:
        w = Watcher.__new__(Watcher)
        w.cfg, w.store = Config(act=False), S(stored)
        w._apply_owner_preferences()
        assert w.allowed_actions == want, stored
    w = Watcher.__new__(Watcher)
    w.cfg, w.store = Config(act=True), S(None)
    w._apply_owner_preferences()
    assert w.allowed_actions == {"clean-workdir", "retry-run", "create-work-item"}


class _FakeTable:
    def __init__(self): self.rows = {}
    def get_entity(self, pk, rk): return self.rows[(pk, rk)]
    def upsert_entity(self, e): self.rows[(e["PartitionKey"], e["RowKey"])] = e
    def delete_entity(self, pk, rk): del self.rows[(pk, rk)]


def _store():
    from store import TableStore
    s = TableStore.__new__(TableStore)
    t = _FakeTable()
    s._t = lambda name: t
    return s


def test_a_projects_own_setting_wins_over_the_all_projects_default():
    s = _store()
    assert s.get_setting("fix_mode", "none", scope="org/a") == "none" and s.setting_source("fix_mode", "org/a") == "none"
    s.set_setting("fix_mode", "propose")                       # all projects
    assert s.get_setting("fix_mode", "", scope="org/a") == "propose" and s.setting_source("fix_mode", "org/a") == "all"
    s.set_setting("fix_mode", "open", scope="Org/A")           # one project; the scope is case-insensitive
    assert s.get_setting("fix_mode", "", scope="org/a") == "open" and s.setting_source("fix_mode", "org/a") == "project"
    assert s.get_setting("fix_mode", "", scope="org/b") == "propose"   # another project still gets the default
    s.clear_setting("fix_mode", scope="org/a")
    assert s.get_setting("fix_mode", "", scope="org/a") == "propose"


def test_scoped_setting_keys_are_valid_table_storage_row_keys():
    from store import TableStore
    for scope in ("Hackathon925/Pipemedic", "a/b c"):
        key = TableStore._key("fix_mode", scope)
        assert not any(ch in key for ch in r"/\#?"), key
