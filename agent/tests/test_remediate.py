import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from remediate import execute, plan_actions  # noqa: E402

NOW = datetime(2026, 10, 16, 21, 30, tzinfo=timezone.utc)
RECOVERED = {"samples": 3, "flags": []}
STILL_BAD = {"samples": 3, "flags": ["high-cpu"]}


def diag(verdict, category, flags=(), build_id=10):
    return {
        "build_id": build_id, "pipeline": "p", "pipeline_id": 3, "agent": "pipemedic-agent-01",
        "hostname": "pipemedicvm1", "failed_step": "step", "category": category, "verdict": verdict,
        "root_cause": "rc", "evidence": ["e"], "suggestions": ["s"], "requested_for": "a@b.c",
        "source_branch": "refs/heads/main", "runner_health": {"samples": 3, "flags": list(flags)},
    }


def names(plan, auto=None):
    return [a["action"] for a in plan if auto is None or a["auto"] == auto]


def test_code_failure_creates_work_item_only():
    plan = plan_actions(diag("code-or-config", "test-failure"), RECOVERED, [], NOW)
    assert names(plan, auto=True) == ["create-work-item"]
    assert "retry-run" not in names(plan)


def test_stressed_runner_retries_once_after_recovery():
    plan = plan_actions(diag("runner", "timeout", ["high-cpu"]), RECOVERED, [], NOW)
    assert names(plan, auto=True) == ["retry-run"]


def test_stressed_runner_defers_while_still_unhealthy():
    plan = plan_actions(diag("runner", "timeout", ["high-cpu"]), STILL_BAD, [], NOW)
    assert names(plan, auto=True) == []
    assert "defer-retry" in names(plan)


def test_low_disk_cleans_then_retries():
    plan = plan_actions(diag("runner", "timeout", ["low-disk"]), RECOVERED, [], NOW)
    assert names(plan, auto=True) == ["clean-workdir", "retry-run"]


def test_missing_tool_is_never_automatic():
    plan = plan_actions(diag("runner", "runner-environment"), RECOVERED, [], NOW)
    assert names(plan, auto=True) == []
    assert "install-prerequisite" in names(plan, auto=False)


def test_no_health_data_blocks_runner_actions():
    d = diag("runner", "timeout", ["low-disk", "no-health-data"])
    plan = plan_actions(d, RECOVERED, [], NOW)
    assert names(plan, auto=True) == []


def test_retry_happens_at_most_once_per_run():
    hist = [{"action": "retry-run", "status": "done", "build_id": 10, "new_build_id": 11, "time": NOW.isoformat()}]
    plan = plan_actions(diag("runner", "timeout", ["high-cpu"]), RECOVERED, hist, NOW)
    assert "retry-run" not in names(plan)


def test_a_retried_run_is_never_retried_again():
    hist = [{"action": "retry-run", "status": "done", "build_id": 10, "new_build_id": 11, "time": NOW.isoformat()}]
    plan = plan_actions(diag("runner", "timeout", ["high-cpu"], build_id=11), RECOVERED, hist, NOW)
    assert names(plan, auto=True) == []


def test_work_item_not_duplicated():
    hist = [{"action": "create-work-item", "status": "done", "build_id": 10, "time": NOW.isoformat()}]
    plan = plan_actions(diag("code-or-config", "test-failure"), RECOVERED, hist, NOW)
    assert names(plan, auto=True) == []


def test_hourly_budget_forces_approval():
    hist = [{"action": "create-work-item", "status": "done", "build_id": i, "time": NOW.isoformat()} for i in range(1, 6)]
    plan = plan_actions(diag("code-or-config", "test-failure"), RECOVERED, hist, NOW)
    assert names(plan, auto=True) == []
    assert "needs approval" in plan[0]["reason"]


def test_unknown_cause_escalates():
    plan = plan_actions(diag("unknown", "unknown"), RECOVERED, [], NOW)
    assert names(plan, auto=True) == [] and "escalate" in names(plan)


def test_dry_run_changes_nothing():
    class Boom:
        def __getattr__(self, name):
            raise AssertionError("dry run must not call Azure DevOps")

    plan = plan_actions(diag("code-or-config", "test-failure"), RECOVERED, [], NOW)
    entry = execute(plan[0], diag("code-or-config", "test-failure"), Boom(), "pool", dry_run=True)
    assert entry["status"] == "dry-run"


def test_actions_off_the_allowlist_are_refused():
    entry = execute({"action": "delete-vm", "auto": True, "reason": "x", "params": {}},
                    diag("runner", "timeout"), None, "pool", dry_run=False)
    assert entry["status"] == "skipped"
