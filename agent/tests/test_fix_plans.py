import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import fix_executor  # noqa: E402
import fixplan  # noqa: E402
import runner_actions as ra  # noqa: E402

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
SECRET = "test-secret"


def diag(**kw):
    base = {"build_id": 70, "pipeline": "inventory-ci", "pipeline_id": 9, "source_branch": "refs/heads/demo/test-failure",
            "hostname": "pipemedicvm1", "verdict": "code-or-config", "category": "test-failure", "kind": None,
            "failed_step": "Unit tests", "root_cause": "rc", "runner_health": {"flags": []}, "suggestions": ["Fix the code."]}
    base.update(kw)
    return base


def proposal(path="services/inventory/app/main.py", **kw):
    p = {"summary": "Fix the off-by-one", "explanation": "One too many.", "confidence": "high", "risk": "Low.",
         "changes": [{"path": path, "action": "edit", "new_content": "x = 1"}], "problems": []}
    p.update(kw)
    return p


def kinds(plans):
    return [p["kind"] for p in plans]


# ---------------- which plans are offered ----------------
def test_a_clean_code_proposal_becomes_a_pull_request_plan_on_the_run_branch():
    plans = fixplan.build_plans(diag(), proposal(), now=NOW)
    assert kinds(plans) == ["code-pr"]
    p = plans[0]
    assert p["plan_id"] == "70-1" and p["status"] == "proposed"
    assert "demo/test-failure" in " ".join(p["steps"]) and "never merged" in " ".join(p["steps"])
    assert p["changes"][0]["path"] == "services/inventory/app/main.py"
    assert p["diagnosis"]["category"] == "test-failure"
    assert datetime.fromisoformat(p["expires"]) == NOW + timedelta(hours=fixplan.PLAN_HOURS)


def test_a_change_to_a_pipeline_file_is_a_pipeline_plan_and_says_so():
    plans = fixplan.build_plans(diag(category="configuration", kind="memory-limit"), proposal("pipelines/templates/service-ci.yml"), now=NOW)
    assert kinds(plans) == ["pipeline-pr"]
    assert plans[0]["risk"].startswith("Changes a pipeline file.")


def test_a_rejected_or_doubtful_proposal_leaves_a_job_for_a_person():
    for p in (proposal(problems=["x is a test file"]), proposal(confidence="low"), None):
        plans = fixplan.build_plans(diag(), p, now=NOW)
        assert kinds(plans) == ["manual"]
    plans = fixplan.build_plans(diag(), {"detail": "not sure", "problems": []}, now=NOW)
    assert plans[0]["reason"] == "not sure"


def test_a_full_disk_offers_cleaning_then_a_vm_restart_then_a_plain_rerun():
    plans = fixplan.build_plans(diag(verdict="runner", category="runner-environment", kind="disk",
                                     runner_health={"flags": ["low-disk"]}), now=NOW)
    assert kinds(plans) == ["clean-disk", "restart-vm", "rerun"]
    assert "pipemedicvm1" in plans[0]["title"]
    assert [p["plan_id"] for p in plans] == ["70-1", "70-2", "70-3"]


def test_a_busy_process_and_a_dead_agent_are_offered_with_the_process_named():
    busy = {"pid": 4242, "comm": "python3", "user": "azureuser", "cpu": 97.0, "mem": 2.0, "seconds": 600, "unit": "pm-hog.scope", "args": "python3 hog.py"}
    plans = fixplan.build_plans(diag(verdict="runner", category="timeout", runner_health={"flags": ["high-cpu", "agent-service-down"]}),
                                busy_process=busy, now=NOW)
    assert kinds(plans) == ["stop-process", "restart-agent", "restart-vm", "rerun"]
    stop = plans[0]
    assert "the CPU" in stop["title"] and "4242" in stop["steps"][0] and "pm-hog.scope" in stop["steps"][0]
    assert stop["process"]["pid"] == 4242


def test_a_flaky_failure_only_offers_a_rerun():
    assert kinds(fixplan.build_plans(diag(verdict="flaky"), now=NOW)) == ["rerun"]


# ---------------- approving ----------------
def test_only_a_fresh_proposed_automatic_plan_can_be_approved():
    plan = fixplan.build_plans(diag(verdict="flaky"), now=NOW)[0]
    assert fixplan.is_runnable(plan, NOW) is None
    assert "expired" in fixplan.is_runnable(plan, NOW + timedelta(hours=25))
    assert "already approved" in fixplan.is_runnable({**plan, "status": "approved"}, NOW)
    manual = fixplan.build_plans(diag(), None, now=NOW)[0]
    assert "person" in fixplan.is_runnable(manual, NOW)


def test_a_signed_link_names_its_plan_and_cannot_be_forged_or_reused_late():
    plan = fixplan.build_plans(diag(verdict="flaky"), now=NOW)[0]
    token = fixplan.sign_link(SECRET, plan, "dev@example.com")
    assert fixplan.read_link(SECRET, token, NOW) == {"build_id": 70, "plan_id": "70-1", "recipient": "dev@example.com"}
    assert fixplan.read_link("other-secret", token, NOW) is None
    body, sig = token.split(".")
    forged = fixplan.sign_link(SECRET, {**plan, "plan_id": "70-9"}, "dev@example.com").split(".")[0] + "." + sig
    assert fixplan.read_link(SECRET, forged, NOW) is None
    assert fixplan.read_link(SECRET, token, NOW + timedelta(hours=25)) is None
    assert fixplan.read_link(SECRET, "garbage", NOW) is None


# ---------------- runner actions ----------------
def test_only_the_agent_users_own_jobs_can_be_stopped():
    assert ra.stoppable({"user": "azureuser", "comm": "python3", "args": "python3 hog.py", "unit": "pm-hog.scope"})
    assert not ra.stoppable({"user": "root", "comm": "python3", "args": "python3 x.py"})
    assert not ra.stoppable({"user": "azureuser", "comm": "Agent.Listener", "args": "./bin/Agent.Listener run"})
    assert not ra.stoppable({"user": "azureuser", "comm": "bash", "args": "/opt/report_health.sh"})
    assert not ra.stoppable({"user": "azureuser", "comm": "uvicorn", "args": "uvicorn app.main:app", "unit": "pipemedic-payments.service"})
    assert "ps -eo pid=,user:32=" in ra.TOP_PROCESSES and "ps -o user:32=" in ra.stop_process_script(1, "x", "")  # ps cuts names at 8


def test_the_busiest_stoppable_process_is_picked_only_when_it_matters():
    procs = ra.parse_json_lines('not json\n{"pid": 1, "user": "root", "cpu": 99, "mem": 1, "comm": "dockerd", "args": ""}\n'
                                '{"pid": 2, "user": "azureuser", "cpu": 80, "mem": 3, "comm": "python3", "args": "hog"}\n'
                                '{"pid": 3, "user": "azureuser", "cpu": 5, "mem": 40, "comm": "node", "args": "big"}\n')
    assert [p["pid"] for p in procs] == [1, 2, 3]
    assert ra.busiest(procs, "cpu")["pid"] == 2
    assert ra.busiest(procs, "mem")["pid"] == 3
    assert ra.busiest([p for p in procs if p["pid"] != 2], "cpu") is None


def test_the_stop_script_checks_the_process_before_touching_it():
    script = ra.stop_process_script(4242, "python3", "pm-hog.scope")
    assert '"$now" != "python3"' in script and '"$owner" != "azureuser"' in script
    assert 'systemctl stop "pm-hog.scope"' in script
    plain = ra.stop_process_script(4242, "python3", "session-3.scope")
    assert "systemctl stop" not in plain and "kill -TERM 4242" in plain


def test_run_command_output_is_cut_to_stdout():
    assert ra.output_of("Enable succeeded: \n[stdout]\n{\"ok\": true}\n\n[stderr]\nwarn") == '{"ok": true}'


# ---------------- deciding and carrying out ----------------
class FakeStore:
    def __init__(self, plans):
        self.plans = {p["plan_id"]: dict(p) for p in plans}
        self.decided = set()

    def get_plan(self, build_id, plan_id):
        return dict(self.plans[plan_id]) if plan_id in self.plans else None

    def update_plan(self, build_id, plan_id, **changes):
        self.plans[plan_id].update(changes)
        return dict(self.plans[plan_id])

    def claim_approval(self, build_id, plan_id, who, via):
        if plan_id in self.decided:
            return False
        self.decided.add(plan_id)
        return True

    def plans_for(self, build_id):
        return [dict(p) for p in self.plans.values()]

    def plans_to_verify(self):
        return [dict(p) for p in self.plans.values() if p.get("status") == "done" and p.get("verified") == "pending"]

    def fixes(self):
        return []

    def record_fix(self, f):
        self.fix = f


class FakeAdo:
    def __init__(self):
        self.queued, self.branches, self.prs = [], [], []
        self.builds = {}

    def queue_run(self, pipeline_id, ref, parameters=None):
        self.queued.append((pipeline_id, ref)); self.params = getattr(self, "params", []) + [parameters]
        return 500 + len(self.queued)

    def create_branch_with_commit(self, branch, changes, message, base="main"):
        self.branches.append((branch, base, [c["path"] for c in changes]))

    def create_pull_request(self, source, title, description, target="main"):
        self.prs.append((source, target, title))
        return 31

    def comment_on_pull_request(self, pr_id, text):
        pass

    def pool_agents(self, pool):
        return [{"name": "pipemedicvm1", "status": "online"}]

    def build(self, build_id):
        return self.builds.get(build_id, {"status": "inProgress"})


def test_a_plan_is_decided_once_even_if_two_people_press_at_the_same_time():
    plan = fixplan.build_plans(diag(verdict="flaky"), now=NOW)[0]
    store = FakeStore([plan])
    ok, msg, p = fix_executor.decide(store, 70, "70-1", True, "a@example.com", "dashboard", now=NOW)
    assert ok and p["status"] == "approved" and p["decided_by"] == "a@example.com"
    ok, msg, _ = fix_executor.decide(store, 70, "70-1", False, "b@example.com", "email", now=NOW)
    assert not ok and "already" in msg
    assert fix_executor.decide(store, 70, "70-9", True, "a", "dashboard", now=NOW)[0] is False


def test_an_expired_plan_cannot_be_approved():
    plan = fixplan.build_plans(diag(verdict="flaky"), now=NOW)[0]
    ok, msg, _ = fix_executor.decide(FakeStore([plan]), 70, "70-1", True, "a", "email", now=NOW + timedelta(days=2))
    assert not ok and "expired" in msg


def test_an_approved_rerun_queues_the_run_branch_and_is_checked_later():
    plan = {**fixplan.build_plans(diag(verdict="flaky"), now=NOW)[0], "status": "approved"}
    store, ado = FakeStore([plan]), FakeAdo()
    r = fix_executor.carry_out(plan, ado, store, None, "pipemedic-pool", "link", now=NOW)
    assert r["ok"] and ado.queued == [(9, "refs/heads/demo/test-failure")]
    assert store.plans["70-1"]["status"] == "done" and store.plans["70-1"]["verified"] == "pending"
    assert fix_executor.check_results(ado, store) == []
    ado.builds[501] = {"status": "completed", "result": "succeeded"}
    assert fix_executor.check_results(ado, store)[0]["verified"] == "passed"


def test_an_unapproved_plan_is_never_carried_out():
    plan = fixplan.build_plans(diag(verdict="flaky"), now=NOW)[0]
    ado = FakeAdo()
    assert not fix_executor.carry_out(plan, ado, FakeStore([plan]), None, "pool", "link")["ok"]
    assert ado.queued == []


def test_an_approved_pull_request_plan_opens_exactly_the_approved_change_into_the_run_branch():
    plan = {**fixplan.build_plans(diag(), proposal(), now=NOW)[0], "status": "approved"}
    store, ado = FakeStore([plan]), FakeAdo()
    r = fix_executor.carry_out(plan, ado, store, None, "pool", "link", now=NOW)
    assert r["ok"], r
    assert ado.branches == [("pipemedic/fix-run-70", "demo/test-failure", ["services/inventory/app/main.py"])]
    assert ado.prs[0][1] == "demo/test-failure"
    assert store.plans["70-1"]["result"]["pr_id"] == 31 and store.plans["70-1"]["result"]["proving_run"] == 501


def test_a_runner_action_that_fails_is_recorded_and_nothing_is_rerun(monkeypatch):
    busy = {"pid": 4242, "comm": "python3", "user": "azureuser", "cpu": 97.0, "mem": 2.0, "seconds": 600, "unit": "", "args": "x"}
    plan = {**fixplan.build_plans(diag(verdict="runner", runner_health={"flags": ["high-cpu"]}), busy_process=busy, now=NOW)[0],
            "status": "approved"}
    monkeypatch.setattr(ra, "run_script", lambda cred, vm, script, timeout_s=120: '{"ok": false, "detail": "the process is no longer running"}')
    store, ado = FakeStore([plan]), FakeAdo()
    r = fix_executor.carry_out(plan, ado, store, None, "pool", "link", now=NOW)
    assert not r["ok"] and "no longer running" in r["detail"]
    assert store.plans["70-1"]["status"] == "failed" and ado.queued == []


def test_cleaning_the_disk_reports_the_space_freed_then_reruns(monkeypatch):
    plan = {**fixplan.build_plans(diag(verdict="runner", kind="disk", runner_health={"flags": ["low-disk"]}), now=NOW)[0], "status": "approved"}
    seen = []
    monkeypatch.setattr(ra, "run_script", lambda cred, vm, script, timeout_s=120: seen.append((vm, script)) or '{"free_mb_before": 90, "free_mb_after": 5100}')
    store, ado = FakeStore([plan]), FakeAdo()
    r = fix_executor.carry_out(plan, ado, store, None, "pool", "link", now=NOW)
    assert r["ok"] and r["disk"]["free_mb_after"] == 5100
    assert seen == [("pipemedicvm1", ra.CLEAN_DISK)] and len(ado.queued) == 1


# ---------------- storing plans ----------------
class _Table:
    def __init__(self):
        self.rows = {}

    def upsert_entity(self, e):
        self.rows[(e["PartitionKey"], e["RowKey"])] = dict(e)

    def get_entity(self, pk, rk):
        from azure.core.exceptions import ResourceNotFoundError
        if (pk, rk) not in self.rows:
            raise ResourceNotFoundError("missing")
        return self.rows[(pk, rk)]

    def create_entity(self, e):
        from azure.core.exceptions import ResourceExistsError
        if (e["PartitionKey"], e["RowKey"]) in self.rows:
            raise ResourceExistsError("exists")
        self.upsert_entity(e)

    def query_entities(self, flt):
        if flt.startswith("PartitionKey"):
            pk = flt.split("'")[1]
            return [e for (p, _), e in self.rows.items() if p == pk]
        return [e for e in self.rows.values() if e.get("status") == "done" and e.get("verified") == "pending"]


def _table_store():
    from store import TableStore
    s = TableStore.__new__(TableStore)
    tables = {}
    s._t = lambda name: tables.setdefault(name, _Table())
    return s, tables


def test_a_large_plan_is_stored_compressed_in_parts_and_read_back_whole():
    import random
    s, tables = _table_store()
    rnd = random.Random(1)
    big = "".join(rnd.choice("abcdefghijklmnopqrstuvwxyz0123456789 ") for _ in range(90000))
    plan = fixplan.build_plans(diag(), proposal(), now=NOW)[0]
    plan["changes"][0]["new_content"] = big
    s.save_plan(plan)
    row = tables["FixPlans"].rows[("70", "70-1")]
    assert row["parts"] > 1 and all(len(row[f"p{i}"]) <= 30000 for i in range(row["parts"]))
    assert s.get_plan(70, "70-1")["changes"][0]["new_content"] == big
    assert [p["plan_id"] for p in s.plans_for(70)] == ["70-1"]
    assert s.get_plan(70, "70-2") is None
    s.update_plan(70, "70-1", status="done", verified="pending")
    assert [p["plan_id"] for p in s.plans_to_verify()] == ["70-1"]


def test_the_table_store_lets_only_the_first_decision_through():
    s, _ = _table_store()
    assert s.claim_approval(70, "70-1", "a@example.com", "email")
    assert not s.claim_approval(70, "70-1", "b@example.com", "dashboard")


# ---------------- the review page and the result email ----------------
def test_the_review_page_shows_the_change_and_only_a_post_form_decides():
    import fix_review
    plan = fixplan.build_plans(diag(), proposal(), now=NOW)[0]
    plan["changes"][0]["old_content"] = "x = 2"
    page = fix_review.review_page(plan, "tok<en>", "dev@example.com")
    assert '<form method="post">' in page and 'value="approve"' in page and 'value="reject"' in page
    assert "-x = 2" in page and "+x = 1" in page
    assert "tok&lt;en&gt;" in page and "tok<en>" not in page
    blocked = fix_review.review_page(plan, "t", "dev@example.com", blocked="this plan is already approved")
    assert "<form" not in blocked and "already approved" in blocked


def test_the_result_email_says_plainly_whether_the_fix_worked():
    import fix_review
    plan = {**fixplan.build_plans(diag(verdict="runner", kind="disk", runner_health={"flags": ["low-disk"]}), now=NOW)[0],
            "status": "done", "verified": "passed", "decided_by": "dev@example.com", "decided_via": "email",
            "result": {"disk": {"free_mb_before": 90, "free_mb_after": 5100}, "rerun": 501}}
    subject, text, body = fix_review.result_email(plan, "https://run", "https://repo")
    assert subject.endswith("worked") and "90 MB to 5100 MB" in text and "run 501" in text
    failed = {**plan, "status": "failed", "verified": "", "result": {"error": "VM not found"}}
    subject, text, _ = fix_review.result_email(failed, "https://run", "https://repo")
    assert subject.endswith("failed") and "VM not found" in text


def test_approving_one_plan_withdraws_the_other_options_for_that_run():
    plans = fixplan.build_plans(diag(verdict="runner", kind="disk", runner_health={"flags": ["low-disk"]}), now=NOW)
    store = FakeStore(plans)
    assert fix_executor.decide(store, 70, "70-1", True, "a@example.com", "dashboard", now=NOW)[0]
    assert {p["plan_id"]: p["status"] for p in store.plans.values()} == {"70-1": "approved", "70-2": "superseded", "70-3": "superseded"}
    assert "superseded" in fixplan.is_runnable(store.plans["70-2"], NOW)


def test_the_result_email_status_is_kept_on_the_plan():
    from types import SimpleNamespace
    plan = {**fixplan.build_plans(diag(verdict="flaky"), now=NOW)[0], "status": "done", "verified": "pending",
            "result": {"rerun": 501}, "decided_by": "dev@example.com"}
    store, ado = FakeStore([plan]), FakeAdo()
    ado.builds[501] = {"status": "completed", "result": "succeeded"}
    sent = []
    cfg = SimpleNamespace(notify_on=True, notify_default_to="owner@example.com", notify_override_to="", org="o", project="p")
    w = SimpleNamespace(cfg=cfg, ado=ado, store=store, mailer=lambda to, s, t, h: sent.append((to, s)) or "Succeeded")
    told = fix_executor.verify_pending(w)
    assert sent[0][0] == "dev@example.com" and sent[0][1].endswith("worked")
    assert store.plans["70-1"]["result_email"] == "Succeeded" and "passed" in told[0]
    assert fix_executor.verify_pending(w) == []  # reported once
