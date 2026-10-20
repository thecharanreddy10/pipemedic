import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from diagnose import classify, diagnose  # noqa: E402
from flaky import assess  # noqa: E402

HEALTHY = [{"timestamp_utc": "2026-10-20T13:00:30+00:00", "cpu_percent": 5.0, "memory_percent": 20.0, "disk_percent": 12,
            "disk_free_gb": 25, "agent_service_running": True}]


def failure(log, step="Benchmark", issues=None):
    return {"pipeline": "inventory-ci", "pipeline_id": 8, "build_id": 45, "failed_step": step, "issues": issues or [], "log_text": log,
            "agent_name": "a1", "hostname": "vm1", "start": "2026-10-20T13:00:00Z", "finish": "2026-10-20T13:01:00Z"}


KILLED = "\n".join([
    "2026-10-20T13:00:40.1Z Demo scenario in effect: benchmark working set raised to 1500 MB, above its 600 MB budget",
    "2026-10-20T13:00:52.2Z /home/azureuser/myagent/_work/_temp/9f1c.sh: line 9: 20039 Killed                  systemd-run --scope --quiet -p MemoryMax=600M env BENCH_MEM_MB=1500 python benchmark.py",
    "2026-10-20T13:00:52.3Z ##[error]Bash exited with code '137'.",
])
TIMED_OUT = "\n".join([
    "2026-10-20T13:00:40.1Z Demo scenario in effect: another workload is using every CPU on the runner, and the full-size benchmark is run",
    "2026-10-20T13:01:44.0Z ##[error]The task has timed out.",
])


def test_a_process_killed_for_lack_of_memory_is_a_runner_problem_not_an_unknown_one():
    c = classify(failure(KILLED))
    assert c["category"] == "runner-environment"
    assert any("Killed" in e or "137" in e for e in c["evidence"])
    assert diagnose(failure(KILLED), HEALTHY)["verdict"] == "runner"


def test_exit_code_137_alone_is_enough():
    assert classify(failure("##[error]Bash exited with code '137'."))["category"] == "runner-environment"


def test_a_step_that_timed_out_is_a_timeout_and_a_busy_runner_makes_it_the_runners_fault():
    assert classify(failure(TIMED_OUT))["category"] == "timeout"
    busy = [{**HEALTHY[0], "cpu_percent": 100.0}]
    assert diagnose(failure(TIMED_OUT), busy)["verdict"] == "runner"
    assert diagnose(failure(TIMED_OUT), HEALTHY)["verdict"] == "code-or-config"  # a quiet runner: the step itself was slow


def test_the_word_killed_in_ordinary_output_is_not_taken_for_an_out_of_memory_kill():
    log = "2026-10-20T13:00:40Z FAILED tests/test_jobs.py::test_worker_is_killed_on_cancel - assert 1 == 2\n##[error]Bash exited with code '1'."
    assert classify(failure(log, "Unit tests"))["category"] == "test-failure"


# ---------------- flaky: only runs made with the same parameters are comparable ----------------
def build(i, result, params=None, commit="abc123"):
    return {"id": i, "result": result, "sourceVersion": commit, "definition": {"id": 8}, **({"templateParameters": params} if params is not None else {})}


def test_a_run_with_other_parameters_that_passed_does_not_make_this_failure_flaky():
    failed = build(10, "failed", {"scenario": "test-failure"})
    others = [build(11, "succeeded", {"scenario": "none"}), build(12, "succeeded", {"scenario": "lint-error"})]
    assert assess(failed, others + [failed])["flaky"] is False


def test_a_run_with_the_same_parameters_that_passed_still_makes_it_flaky():
    failed = build(10, "failed", {"scenario": "flaky-test"})
    passed = build(11, "succeeded", {"scenario": "flaky-test"})
    result = assess(failed, [failed, passed])
    assert result["flaky"] is True and result["pass_runs"] == [11]


def test_pipelines_without_parameters_behave_as_before():
    assert assess(build(10, "failed"), [build(10, "failed"), build(11, "succeeded")])["flaky"] is True
    assert assess(build(10, "failed"), [build(10, "failed"), build(11, "succeeded", commit="other")])["flaky"] is False


# ---------------- health window: readings inside the run win over the margin ----------------
def reading(ts, cpu):
    return {"timestamp_utc": ts, "cpu_percent": cpu, "memory_percent": 20.0, "disk_percent": 12, "disk_free_gb": 25, "agent_service_running": True}


def test_a_busy_cpu_from_the_run_before_is_not_counted_when_the_run_has_its_own_readings():
    from diagnose import summarize_health
    records = [reading("2026-10-20T12:59:20+00:00", 100.0),   # the previous run, ending just before this one
               reading("2026-10-20T13:00:20+00:00", 5.0), reading("2026-10-20T13:01:20+00:00", 6.0)]
    h = summarize_health(records, "2026-10-20T13:00:05Z", "2026-10-20T13:01:30Z")
    assert h["samples"] == 2 and h["flags"] == [] and h["cpu_max"] == 6.0


def test_a_short_run_with_no_reading_inside_it_still_uses_the_margin():
    from diagnose import summarize_health
    h = summarize_health([reading("2026-10-20T12:59:50+00:00", 100.0)], "2026-10-20T13:00:05Z", "2026-10-20T13:00:40Z")
    assert h["samples"] == 1 and "high-cpu" in h["flags"]


# ---------------- runner problems say what kind they are ----------------
DISK_FULL = "\n".join([
    "2026-10-20T13:00:40Z Demo scenario in effect: filling the runner disk, leaving 60 MB",
    "2026-10-20T13:00:52Z ERROR: Could not install packages due to an OSError: [Errno 28] No space left on device",
    "2026-10-20T13:00:52Z ##[error]Bash exited with code '1'.",
])
MISSING_TOOL = "2026-10-20T13:00:52Z The virtual environment was not created successfully because ensurepip is not available.\n##[error]Bash exited with code '1'."


def test_a_memory_kill_is_described_as_a_memory_problem_with_matching_fixes():
    d = diagnose(failure(KILLED), HEALTHY)
    assert d["kind"] == "memory" and d["verdict"] == "runner"
    assert "memory" in d["root_cause"] and "missing something" not in d["root_cause"]
    assert any("memory limit" in s for s in d["suggestions"]) and not any("apt-get" in s for s in d["suggestions"])


def test_a_full_disk_is_described_as_a_disk_problem():
    d = diagnose(failure(DISK_FULL, "Install dependencies"), HEALTHY)
    assert d["kind"] == "disk" and "disk space" in d["root_cause"]
    assert any("Clear old job folders" in s for s in d["suggestions"])


def test_a_missing_tool_keeps_the_original_wording():
    d = diagnose(failure(MISSING_TOOL, "Install dependencies"), HEALTHY)
    assert d["kind"] == "tool" and "missing something" in d["root_cause"] and any("apt-get" in s for s in d["suggestions"])


def test_the_suggested_actions_match_the_kind_of_runner_problem():
    from remediate import plan_actions
    idle = {"samples": 1, "flags": []}
    memory = plan_actions(diagnose(failure(KILLED), HEALTHY), idle, [])
    assert [a["action"] for a in memory] == ["raise-memory-limit"] and not memory[0]["auto"]
    disk = [a["action"] for a in plan_actions(diagnose(failure(DISK_FULL, "Install dependencies"), HEALTHY), idle, [])]
    assert "clean-workdir" in disk and "install-prerequisite" not in disk
    tool = [a["action"] for a in plan_actions(diagnose(failure(MISSING_TOOL, "Install dependencies"), HEALTHY), idle, [])]
    assert tool == ["install-prerequisite"]


# ---------------- a step over its own memory limit is not the runner's fault ----------------
CGROUP_LOG = "\n".join([
    "### kernel",
    "Oct 20 14:08:52 pipemedicvm1 kernel: Memory cgroup out of memory: Killed process 252817 (python) total-vm:1589800kB, anon-rss:613052kB",
    "### entries",
    "812",
])
SYSTEM_OOM_LOG = "\n".join([
    "### kernel",
    "Oct 20 14:08:52 pipemedicvm1 kernel: Out of memory: Killed process 252817 (python) total-vm:3589800kB",
    "### entries",
    "812",
])


def logs_from(text):
    import runner_logs
    return {"available": True, "window": "14:08:00 to 14:09:26 UTC", "text": text, "findings": runner_logs.analyze(text)}


def test_the_runner_log_tells_a_step_limit_apart_from_the_machine_running_out_of_memory():
    import runner_logs
    step = runner_logs.analyze(CGROUP_LOG)
    machine = runner_logs.analyze(SYSTEM_OOM_LOG)
    assert [(f["flag"], f["runner_fault"]) for f in step] == [("step-memory-limit (runner log)", False)]
    assert [(f["flag"], f["runner_fault"]) for f in machine] == [("out-of-memory (runner log)", True)]


def test_a_step_killed_by_its_own_limit_on_a_healthy_runner_is_a_configuration_problem():
    d = diagnose(failure(KILLED), HEALTHY, logs_from(CGROUP_LOG))
    assert d["verdict"] == "code-or-config" and d["category"] == "configuration" and d["kind"] == "memory-limit"
    assert "Memory cgroup out of memory" in d["root_cause"] and "20%" in d["root_cause"] and "not the runner" in d["root_cause"]
    assert d["runner_health"]["flags"] == []  # the step's limit is not held against the runner
    assert any("raise the memory limit" in s for s in d["suggestions"])


def test_the_machine_running_out_of_memory_is_still_the_runners_fault():
    d = diagnose(failure(KILLED), HEALTHY, logs_from(SYSTEM_OOM_LOG))
    assert d["verdict"] == "runner" and "out-of-memory (runner log)" in d["runner_health"]["flags"]


def test_a_step_limit_hit_while_the_machine_memory_was_also_high_stays_a_runner_problem():
    busy = [{**HEALTHY[0], "memory_percent": 96.0}]
    d = diagnose(failure(KILLED), busy, logs_from(CGROUP_LOG))
    assert d["verdict"] == "runner"


def test_the_model_is_told_what_a_memory_cgroup_kill_means():
    from ai_diagnose import SYSTEM
    assert "Memory cgroup out of memory" in SYSTEM and "does not mean the machine ran out of memory" in SYSTEM


def test_systemd_oom_kill_lines_from_a_step_limit_do_not_count_as_the_machine_running_out():
    import runner_logs
    text = "\n".join([
        "### journal-warnings",
        "Oct 20 14:08:52 pipemedicvm1 systemd[1]: run-r1a2.scope: A process of this unit has been killed by the OOM killer.",
        "Oct 20 14:08:52 pipemedicvm1 systemd[1]: run-r1a2.scope: Failed with result 'oom-kill'.",
        "### kernel",
        "Oct 20 14:08:52 pipemedicvm1 kernel: python invoked oom-killer: gfp_mask=0xcc0(GFP_KERNEL), order=0, oom_score_adj=0",
        "Oct 20 14:08:52 pipemedicvm1 kernel: Memory cgroup out of memory: Killed process 252817 (python) total-vm:1589800kB",
        "Oct 20 14:08:52 pipemedicvm1 kernel: oom_reaper: reaped process 252817 (python), now anon-rss:0kB",
        "### entries", "812"])
    assert [(f["flag"], f["runner_fault"]) for f in runner_logs.analyze(text)] == [("step-memory-limit (runner log)", False)]
    d = diagnose(failure(KILLED), HEALTHY, logs_from(text))
    assert d["verdict"] == "code-or-config" and d["runner_health"]["flags"] == []


# ---------------- a recognised cause is never called "not recognised" ----------------
def test_recognised_code_or_config_causes_get_a_work_item_not_an_unrecognised_escalation():
    from remediate import plan_actions
    idle = {"samples": 1, "flags": []}
    for category, log in [("network", "curl: (28) Connection timed out after 8002 milliseconds\n##[error]Bash exited with code '28'."),
                          ("permissions", "cp: cannot create regular file '/opt/x/app.py': Permission denied\n##[error]Bash exited with code '1'.")]:
        d = diagnose(failure(log, "Deploy"), HEALTHY)
        assert d["category"] == category, (category, d["category"])
        actions = plan_actions(d, idle, [])
        assert [a["action"] for a in actions] == ["create-work-item"], (category, actions)
        assert not any("not recognised" in a["reason"] for a in actions)


def test_only_an_unknown_cause_is_called_not_recognised():
    from remediate import plan_actions
    d = diagnose(failure("something odd happened\n##[error]Bash exited with code '3'."), HEALTHY)
    assert d["category"] == "unknown"
    assert any("not recognised" in a["reason"] for a in plan_actions(d, {"samples": 1, "flags": []}, []))
