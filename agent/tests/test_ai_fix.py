import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai_fix import (check_fix_verifications, eligible, open_fix_pr, pick_context, pr_text,  # noqa: E402
                    propose_fix, validate)

TREE = ["services/inventory/app/main.py", "services/inventory/app/__init__.py", "services/inventory/tests/test_inventory.py",
        "services/inventory/requirements.txt", "services/orders/validate_config.sh", "services/orders/app/main.py"]
OLD_MAIN = "def reserve():\n    if bad:\n        raise ValueError('x')\n    return STOCK[item] + 1\n"
OLD_SH = "set -e\nif [ ! -f f ]; then\n  exit 1\nfi\n"
FILES = {"services/inventory/app/main.py": OLD_MAIN, "services/orders/validate_config.sh": OLD_SH}


def change(path="services/inventory/app/main.py", action="edit", content="def reserve():\n    if bad:\n        raise ValueError('x')\n    return STOCK[item]\n"):
    return {"path": path, "action": action, "new_content": content}


def proposal(*changes, confidence="high"):
    return {"summary": "Fix the off-by-one", "explanation": "Returned value was one too high.", "confidence": confidence,
            "risk": "low", "changes": list(changes)}


def d(**kw):
    base = {"pipeline": "azure-pipelines-test-failure", "pipeline_id": 4, "build_id": 13, "failed_step": "Inventory - tests",
            "category": "test-failure", "verdict": "code-or-config", "root_cause": "rc",
            "log_excerpt": "FAILED tests/test_inventory.py::test_x - assert 8 == 7\ntests/test_inventory.py:24: AssertionError"}
    base.update(kw)
    return base


# ---------------- eligibility and context ----------------
def test_only_code_failures_are_fixable():
    assert eligible(d())
    assert not eligible(d(verdict="runner")) and not eligible(d(verdict="flaky"))
    assert not eligible(d(category="timeout"))


def test_context_has_the_code_under_test_but_not_the_tests():
    ctx = pick_context(TREE, d()["log_excerpt"])
    assert "services/inventory/tests/test_inventory.py" in ctx  # named in the log
    assert "services/inventory/app/main.py" in ctx  # the code under test
    assert all("orders" not in p for p in ctx)


# ---------------- hard checks on a proposal ----------------
def test_a_good_edit_passes():
    assert validate(proposal(change()), set(TREE), FILES) == []


def test_no_changes_is_rejected():
    assert validate(proposal(), set(TREE))


def test_test_files_may_not_be_changed():
    p = validate(proposal(change(path="services/inventory/tests/test_inventory.py")), set(TREE))
    assert any("test file" in x for x in p)


def test_paths_outside_services_are_rejected():
    for bad in ("agent/watcher.py", "azure-pipelines-success.yml", "../etc/passwd", "/services/x.py", "services/../agent/x.py"):
        assert validate(proposal(change(path=bad, action="create")), set(TREE)), bad


def test_pipeline_files_are_rejected():
    assert any("pipeline" in x for x in validate(proposal(change(path="services/orders/azure-pipelines-x.yml", action="create", content="a: 1")), set(TREE)))


def test_edit_needs_an_existing_file_and_create_needs_a_new_one():
    assert validate(proposal(change(path="services/inventory/app/new.py")), set(TREE))
    assert validate(proposal(change(action="create")), set(TREE))


def test_invalid_python_and_json_are_rejected():
    assert any("not valid" in x for x in validate(proposal(change(content="def broken(:\n")), set(TREE), FILES))
    assert any("not valid" in x for x in validate(proposal(change(path="services/orders/config/x.json", action="create", content="{nope")), set(TREE)))


def test_removing_checks_is_rejected():
    weakened = validate(proposal(change(path="services/orders/validate_config.sh", content="echo fine\n")), set(TREE), FILES)
    assert any("weaken" in x for x in weakened)
    no_raise = validate(proposal(change(content="def reserve():\n    return STOCK[item]\n")), set(TREE), FILES)
    assert any("weaken" in x for x in no_raise)


def test_too_many_files_and_duplicates_are_rejected():
    many = [change(path=f"services/inventory/app/f{i}.py", action="create", content="x = 1\n") for i in range(4)]
    assert any("too many" in x for x in validate(proposal(*many), set(TREE)))
    same = [change(path="services/inventory/app/a.py", action="create", content="x = 1\n")] * 2
    assert any("twice" in x for x in validate(proposal(*same), set(TREE)))


# ---------------- asking the model ----------------
class FakeLLM:
    def __init__(self, prop):
        self.prop, self.calls = prop, []

    def chat(self, messages, tools=None, json_schema=None, max_tokens=0):
        self.calls.append(messages)
        return {"role": "assistant", "content": json.dumps(self.prop)}


def test_propose_fix_sends_the_log_and_code_and_validates():
    llm = FakeLLM(proposal(change()))
    out = propose_fix(llm, d(), TREE, lambda p: FILES.get(p, "# file\n"))
    sent = json.dumps(llm.calls[0])
    assert "assert 8 == 7" in sent and "return STOCK[item] + 1" in sent
    assert out["problems"] == []


def test_secrets_in_files_are_redacted_before_sending():
    import random
    import string
    secret = "".join(random.choices(string.ascii_letters + string.digits, k=60))  # made up on the spot, never a real credential
    llm = FakeLLM(proposal(change()))
    propose_fix(llm, d(), TREE, lambda p: f"TOKEN = '{secret}'\n" + FILES.get(p, ""))
    assert secret not in json.dumps(llm.calls[0])


# ---------------- pull request flow ----------------
class FakeStore:
    def __init__(self, fixes=()):
        self._fixes = [dict(f) for f in fixes]

    def fixes(self): return self._fixes
    def record_fix(self, rec): self._fixes.append(rec)
    def update_fix(self, build_id, **ch):
        for f in self._fixes:
            if f["build_id"] == build_id:
                f.update(ch)


class FakeAdo:
    def __init__(self):
        self.calls, self.reads, self.pr_status, self.build_state = [], [], "active", {"status": "inProgress"}

    def repo_paths(self, scope="/services", branch="main"):  # a read, not an action: kept apart from calls
        self.reads.append((scope, branch))
        return [p for p in TREE if p.startswith(scope.lstrip("/") + "/")]
    def read_file(self, p, branch="main"): return FILES.get(p, "# file\n")
    def create_branch_with_commit(self, branch, changes, message, base="main"): self.calls.append(("branch", branch, len(changes), base)); return "c1"
    def create_pull_request(self, source, title, desc, target="main"): self.calls.append(("pr", source, target)); return 77
    def queue_run(self, pid, ref): self.calls.append(("queue", pid, ref)); return 500
    def comment_on_pull_request(self, pr, text): self.calls.append(("comment", pr, text))
    def pull_request(self, pr): return {"status": self.pr_status}
    def build(self, i): return self.build_state


def test_dry_run_changes_nothing():
    ado = FakeAdo()
    r = open_fix_pr(ado, FakeLLM(proposal(change())), d(), "link", store=FakeStore(), dry_run=True)
    assert r["status"] == "proposed" and ado.calls == []


def test_opening_creates_branch_pr_and_reruns_the_pipeline_on_it():
    ado, store = FakeAdo(), FakeStore()
    r = open_fix_pr(ado, FakeLLM(proposal(change())), d(), "link", store=store, dry_run=False)
    assert r["status"] == "opened" and r["pr_id"] == 77
    kinds = [c[0] for c in ado.calls]
    assert kinds == ["branch", "pr", "queue", "comment"]
    assert ado.calls[2][2] == "refs/heads/pipemedic/fix-run-13"
    assert store.fixes()[0]["status"] == "open" and store.fixes()[0]["verified"] == "pending"


def test_only_one_open_fix_per_pipeline():
    store = FakeStore([{"pipeline": "azure-pipelines-test-failure", "status": "open", "build_id": 1, "time": datetime.now(timezone.utc).isoformat()}])
    r = open_fix_pr(FakeAdo(), FakeLLM(proposal(change())), d(), "link", store=store, dry_run=False)
    assert r["status"] == "skipped" and "waiting for review" in r["detail"]


def test_daily_cap():
    now = datetime.now(timezone.utc).isoformat()
    store = FakeStore([{"pipeline": f"p{i}", "status": "completed", "build_id": i, "time": now} for i in range(3)])
    assert open_fix_pr(FakeAdo(), FakeLLM(proposal(change())), d(), "link", store=store, dry_run=False)["status"] == "skipped"
    # a draft for a person to approve, and a plan a person approved, are not held back by it
    assert open_fix_pr(FakeAdo(), FakeLLM(proposal(change())), d(), "link", store=store, dry_run=True)["status"] == "proposed"
    approved = {**proposal(change()), "problems": []}
    assert open_fix_pr(FakeAdo(), None, d(), "link", store=store, dry_run=False, proposal=approved)["status"] == "opened"


def test_low_confidence_and_bad_proposals_open_nothing():
    ado = FakeAdo()
    assert open_fix_pr(ado, FakeLLM(proposal(change(), confidence="low")), d(), "l", store=FakeStore(), dry_run=False)["status"] == "rejected"
    bad = proposal(change(path="services/inventory/tests/test_inventory.py"))
    assert open_fix_pr(ado, FakeLLM(bad), d(), "l", store=FakeStore(), dry_run=False)["status"] == "rejected"
    assert ado.calls == []


def test_unfixable_failures_are_skipped_without_calling_the_model():
    llm = FakeLLM(proposal(change()))
    assert open_fix_pr(FakeAdo(), llm, d(verdict="runner"), "l", dry_run=False)["status"] == "skipped" and llm.calls == []


def test_verification_result_is_posted_on_the_pr_once():
    ado = FakeAdo()
    store = FakeStore([{"build_id": 13, "pipeline": "p", "pr_id": 77, "verify_build": 500, "status": "open", "verified": "pending",
                        "time": datetime.now(timezone.utc).isoformat()}])
    assert check_fix_verifications(ado, store) == []  # still running
    ado.build_state = {"status": "completed", "result": "succeeded"}
    assert "verification passed" in check_fix_verifications(ado, store)[0]
    assert store.fixes()[0]["verified"] == "passed"
    posted = [c for c in ado.calls if c[0] == "comment"]
    assert len(posted) == 1 and "succeeded" in posted[0][2]
    check_fix_verifications(ado, store)
    assert len([c for c in ado.calls if c[0] == "comment"]) == 1


def test_a_finished_pr_closes_the_record():
    ado = FakeAdo()
    ado.pr_status = "completed"
    store = FakeStore([{"build_id": 13, "pipeline": "p", "pr_id": 77, "verify_build": 500, "status": "open", "verified": "pending", "time": "t"}])
    check_fix_verifications(ado, store)
    assert store.fixes()[0]["status"] == "completed"


def test_pr_text_warns_about_invented_files_and_says_it_is_ai_written():
    title, body = pr_text(d(), proposal(change(path="services/orders/config/x.json", action="create", content="{}")), "link")
    assert "AI-generated" in body and "REPLACE_ME" in body and "never merges" in body and title.startswith("[PipeMedic]")


def _open(build, files, step="Inventory - tests", category="test-failure"):
    return {"pipeline": "azure-pipelines-test-failure", "status": "open", "build_id": build, "files": files,
            "signature": f"{category}|{step}", "time": datetime.now(timezone.utc).isoformat()}


def test_a_second_fix_on_the_same_pipeline_is_allowed_when_it_touches_other_files_for_another_failure():
    store = FakeStore([_open(1, ["services/inventory/app/other.py"], step="Inventory - lint")])
    r = open_fix_pr(FakeAdo(), FakeLLM(proposal(change())), d(), "link", store=store, dry_run=False)
    assert r["status"] == "opened"


def test_a_second_fix_is_refused_when_it_changes_a_file_another_open_fix_changes():
    store = FakeStore([_open(1, ["services/inventory/app/main.py"], step="Inventory - lint")])
    r = open_fix_pr(FakeAdo(), FakeLLM(proposal(change())), d(), "link", store=store, dry_run=False)
    assert r["status"] == "skipped" and "services/inventory/app/main.py" in r["detail"]


def test_the_same_failure_is_not_fixed_twice_and_the_open_fix_cap_holds():
    same = FakeStore([_open(1, ["a.py"])])
    r = open_fix_pr(FakeAdo(), FakeLLM(proposal(change())), d(), "link", store=same, dry_run=False)
    assert r["status"] == "skipped" and "same failure" in r["detail"]
    full = FakeStore([_open(i, [f"f{i}.py"], step=f"step {i}") for i in range(1, 4)])
    r = open_fix_pr(FakeAdo(), FakeLLM(proposal(change())), d(), "link", store=full, dry_run=False)
    assert r["status"] == "skipped" and "3 fixes" in r["detail"]


# ---------------- fixes are made on the failed run's own branch ----------------
def test_a_fix_is_made_on_the_runs_branch_and_proposed_back_into_it():
    ado, store = FakeAdo(), FakeStore()
    r = open_fix_pr(ado, FakeLLM(proposal(change())), d(source_branch="refs/heads/demo/test-failure"), "link", store=store, dry_run=False)
    assert r["status"] == "opened" and r["base"] == "demo/test-failure"
    assert ("/services", "demo/test-failure") in ado.reads
    branch_call = next(c for c in ado.calls if c[0] == "branch")
    pr_call = next(c for c in ado.calls if c[0] == "pr")
    assert branch_call[3] == "demo/test-failure" and pr_call[2] == "demo/test-failure"
    assert store.fixes()[0]["base"] == "demo/test-failure"


def test_without_a_branch_the_default_branch_is_used():
    from ai_fix import run_branch
    assert run_branch({}) == "main" and run_branch({"source_branch": "refs/heads/feature/x"}) == "feature/x"


def test_lint_dependency_code_and_config_failures_can_be_fixed_too():
    for category in ("static-analysis", "dependency", "code-error", "configuration"):
        assert eligible(d(category=category)), category
    assert not eligible(d(category="network")) and not eligible(d(category="permissions"))


def test_a_pipeline_file_is_refused_unless_a_person_allowed_it():
    from ai_fix import system_prompt
    pipe = {"changes": [{"path": "pipelines/templates/service-ci.yml", "action": "edit", "new_content": "stages: []\n"}]}
    existing = {"pipelines/templates/service-ci.yml"}
    assert any("pipeline" in p for p in validate(pipe, existing))
    assert validate(pipe, existing, allow_pipeline=True) == []
    assert "NEVER touch pipeline" in system_prompt(False) and "may change a pipeline definition" in system_prompt(True)


def test_an_approved_proposal_is_opened_as_it_is_without_asking_the_model_again():
    ado, llm = FakeAdo(), FakeLLM(proposal(change()))
    approved = {**proposal(change()), "problems": []}
    r = open_fix_pr(ado, llm, d(), "link", store=FakeStore(), dry_run=False, proposal=approved)
    assert r["status"] == "opened" and llm.calls == [] and ado.reads == []
