import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from diagnose import diagnose  # noqa: E402
from flaky import assess, failed_tests  # noqa: E402
from remediate import plan_actions  # noqa: E402

LOG = """2026-10-16T22:00:00Z FAILED flaky/test_cache_flaky.py::test_cache_warmup_is_ready - AssertionError: cache not warm
2026-10-16T22:00:00Z 1 failed in 0.05s"""


def build(i, result, commit="abc12345", pipeline=7):
    return {"id": i, "result": result, "sourceVersion": commit, "definition": {"id": pipeline, "name": "flaky"}}


def failure(flaky=None):
    return {"pipeline": "flaky", "build_id": 20, "failed_step": "Frontend - flaky test", "log_text": LOG,
            "issues": ["Bash exited with code '1'."], "agent_name": "a1", "hostname": "vm1",
            "start": "2026-10-16T22:00:00Z", "finish": "2026-10-16T22:01:00Z", "flaky": flaky,
            "pipeline_id": 7, "source_branch": "refs/heads/main", "requested_for": "a@b.c"}


def healthy():
    return [{"timestamp_utc": "2026-10-16T22:00:30+00:00", "cpu_percent": 4.0, "memory_percent": 20.0,
             "disk_percent": 12, "disk_free_gb": 25, "agent_service_running": True}]


def test_failed_tests_parsed_from_log():
    assert failed_tests(LOG) == ["flaky/test_cache_flaky.py::test_cache_warmup_is_ready"]


def test_pass_on_same_commit_means_flaky():
    a = assess(build(20, "failed"), [build(20, "failed"), build(21, "succeeded")])
    assert a["flaky"] and a["passes"] == 1 and a["fails"] == 1


def test_pass_on_a_different_commit_is_not_flaky():
    a = assess(build(20, "failed"), [build(20, "failed"), build(21, "succeeded", commit="other")])
    assert not a["flaky"]


def test_other_pipelines_are_ignored():
    a = assess(build(20, "failed"), [build(20, "failed"), build(21, "succeeded", pipeline=99)])
    assert not a["flaky"]


def test_diagnosis_says_flaky_with_evidence():
    d = diagnose(failure(assess(build(20, "failed"), [build(21, "succeeded")])), healthy())
    assert d["verdict"] == "flaky"
    assert "same commit" in d["root_cause"] and "intermittent" in d["root_cause"]
    assert d["flaky_tests"] == ["flaky/test_cache_flaky.py::test_cache_warmup_is_ready"]


def test_consistent_failure_is_not_called_flaky():
    f = assess(build(20, "failed"), [build(19, "failed")])
    d = diagnose(failure(f), healthy())
    assert d["verdict"] == "code-or-config"
    assert "consistent" in d["root_cause"]


def test_flaky_failure_gets_one_automatic_retry_even_without_health_data():
    d = diagnose(failure(assess(build(20, "failed"), [build(21, "succeeded")])), [])
    plan = plan_actions(d, {"samples": 0, "flags": []}, [])
    assert [a["action"] for a in plan if a["auto"]] == ["retry-run"]
    assert any(a["action"] == "quarantine-test" and not a["auto"] for a in plan)


def test_flaky_failure_after_a_retry_goes_to_a_person():
    d = diagnose(failure(assess(build(20, "failed"), [build(21, "succeeded")])), healthy())
    hist = [{"action": "retry-run", "status": "done", "build_id": 20, "new_build_id": 22,
             "time": "2026-10-16T22:02:00+00:00"}]
    plan = plan_actions(d, {"samples": 1, "flags": []}, hist)
    assert "retry-run" not in [a["action"] for a in plan]
    assert "create-work-item" in [a["action"] for a in plan if a["auto"]]
