import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import runner_logs  # noqa: E402
from diagnose import diagnose  # noqa: E402

NL = chr(10)
START = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
FINISH = datetime(2026, 10, 20, 12, 5, tzinfo=timezone.utc)

OOM = """### journal-warnings
Oct 20 12:03:10 vm1 kernel: Out of memory: Killed process 4121 (python3) total-vm:3000000kB
### kernel
Oct 20 12:03:10 vm1 kernel: Out of memory: Killed process 4121 (python3) total-vm:3000000kB
### agent-service
### agent-worker-log
### now
 12:06:00 up 3 days, load average: 0.10
Mem: 3905 3700 100
"""
QUIET = "### journal-warnings\n### kernel\n### agent-service\n### agent-worker-log\n### now\nMem: 3905 600 2000\n"


def test_script_reads_the_run_window_plus_a_short_margin_after_it_and_is_read_only():
    script = runner_logs.build_script(START, FINISH)
    assert 'START="2026-10-20 12:00:00"' in script and 'END="2026-10-20 12:05:30"' in script
    for forbidden in ("rm ", "kill ", "systemctl restart", "reboot", ">"):
        assert forbidden not in script.replace("2>&1", "").replace("2>/dev/null", "")
    assert f"head -c {runner_logs.MAX_OUTPUT}" in script


def test_output_is_cut_out_of_the_run_command_wrapper():
    raw = "Enable succeeded: \n[stdout]\n### kernel\nsomething\n[stderr]\nignored"
    assert runner_logs.parse_output(raw) == "### kernel\nsomething"
    assert runner_logs.parse_output("plain text") == "plain text"


def test_an_out_of_memory_kill_is_found_once_with_its_line():
    found = runner_logs.analyze(OOM)
    assert [f["flag"] for f in found] == ["out-of-memory (runner log)"]
    assert "Killed process 4121" in found[0]["line"]


def test_a_quiet_runner_has_no_findings_and_the_snapshot_never_counts():
    assert runner_logs.analyze(QUIET) == []
    assert runner_logs.analyze("### now\nkernel: Out of memory: Killed process 1") == []


def test_agent_loss_is_only_read_from_the_agent_sections():
    lost = "### agent-service\nOct 20 12:02 vm1 Runner.Listener: Lost communication with the server, will retry\n"
    assert [f["flag"] for f in runner_logs.analyze(lost)] == ["agent-lost (runner log)"]
    assert runner_logs.analyze("### journal-warnings\nsshd: user disconnect from 10.0.0.4\n") == []


def test_disk_full_and_io_errors_are_separate_findings():
    text = "### kernel\nEXT4-fs error (device sda1): ext4_find_entry\n### journal-warnings\nwrite failed: No space left on device\n"
    assert {f["flag"] for f in runner_logs.analyze(text)} == {"disk-io-errors (runner log)", "disk-full (runner log)"}


def test_collect_runs_the_script_on_the_named_vm_and_analyses_the_output():
    calls = []

    def fake_run(sub, rg, vm, script, credential, timeout):
        calls.append((vm, "journalctl" in script))
        return "Enable succeeded:\n[stdout]\n" + OOM + "\n[stderr]\n"

    out = runner_logs.collect(object(), "pipemedicvm1", "2026-10-20T12:00:00Z", "2026-10-20T12:05:00Z", run=fake_run)
    assert calls == [("pipemedicvm1", True)]
    assert out["available"] and out["error"] is None and out["window"] == "12:00:00 to 12:05:30 UTC"
    assert [f["flag"] for f in out["findings"]] == ["out-of-memory (runner log)"]


def test_a_runner_with_no_log_entries_for_the_window_says_so_instead_of_reporting_no_problems():
    empty = NL.join(['### journal-warnings', '### kernel', '### agent-service', '### agent-worker-log', '### entries', '0', '### now', 'Mem: 1 2 3'])
    out = runner_logs.collect(object(), 'vm1', '2026-10-20T12:00:00Z', '2026-10-20T12:05:00Z', run=lambda *a: empty)
    assert not out['available'] and 'no system log entries' in out['error'] and out['findings'] == []


def test_logs_from_before_a_restart_are_still_read_because_the_journal_survives_restarts():
    older = NL.join(['### journal-warnings', '### kernel', 'Oct 18 17:19 vm1 kernel: Out of memory: Killed process 9 (python3)', '### entries', '2632', '### now', 'Mem: 1 2 3'])
    out = runner_logs.collect(object(), 'vm1', '2026-10-18T17:17:00Z', '2026-10-18T17:21:00Z', run=lambda *a: older)
    assert out['available'] and [f['flag'] for f in out['findings']] == ['out-of-memory (runner log)']


def test_collect_never_raises_when_the_vm_cannot_be_reached():
    def broken(*a, **k):
        raise RuntimeError("run command rejected: HTTP 403")

    out = runner_logs.collect(object(), "pipemedicvm1", "2026-10-20T12:00:00Z", None, run=broken, now=FINISH)
    assert not out["available"] and "403" in out["error"] and out["findings"] == []
    assert runner_logs.collect(object(), None, "2026-10-20T12:00:00Z", None)["error"]
    assert runner_logs.collect(object(), "vm1", None, None)["error"]


def failure(log, category_log):
    return {"pipeline": "p", "pipeline_id": 1, "build_id": 7, "failed_step": "Unit tests", "issues": [], "log_text": log,
            "agent_name": "a1", "hostname": "vm1", "start": "2026-10-20T12:00:00Z", "finish": "2026-10-20T12:05:00Z"}


HEALTHY = [{"timestamp_utc": "2026-10-20T12:02:00+00:00", "cpu_percent": 5.0, "memory_percent": 30.0, "disk_percent": 10,
            "disk_free_gb": 25, "agent_service_running": True}]
TEST_LOG = "FAILED tests/test_x.py::test_a - assert 1 == 2\n##[error]Bash exited with code '1'."


def test_a_log_finding_turns_a_code_failure_on_a_healthy_looking_runner_into_a_mixed_verdict():
    plain = diagnose(failure(TEST_LOG, ""), HEALTHY)
    assert plain["verdict"] == "code-or-config"
    logs = {"available": True, "text": OOM, "findings": runner_logs.analyze(OOM)}
    d = diagnose(failure(TEST_LOG, ""), HEALTHY, logs)
    assert d["verdict"] == "mixed" and "out-of-memory (runner log)" in d["root_cause"]
    assert d["runner_logs"]["findings"][0]["flag"] == "out-of-memory (runner log)"


def test_quiet_runner_logs_do_not_change_the_verdict():
    logs = {"available": True, "text": QUIET, "findings": []}
    assert diagnose(failure(TEST_LOG, ""), HEALTHY, logs)["verdict"] == "code-or-config"
