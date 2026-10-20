import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai_fix import eligible  # noqa: E402
from diagnose import apply_ai, diagnose  # noqa: E402
from notify import render_email  # noqa: E402

# A log our rules do not recognise: no exit-code line, no test or dependency words.
MYSTERY = "\n".join([
    "2026-10-18T10:00:00Z Running tool frobnicate --mode fast",
    "2026-10-18T10:00:02Z fatal: segment 7 rejected by quorum",
    "2026-10-18T10:00:02Z frobnicate: status 3",
])


def f(log, step="Deploy edge cache"):
    return {"pipeline": "p", "build_id": 9, "failed_step": step, "log_text": log, "issues": [], "agent_name": "a",
            "hostname": "vm1", "start": "2026-10-18T10:00:00Z", "finish": "2026-10-18T10:01:00Z"}


def test_unknown_failure_gets_a_clue_and_next_steps_not_a_fix():
    d = diagnose(f(MYSTERY), [])
    assert d["verdict"] == "unknown" and d["category"] == "unknown"
    assert "could not be identified" in d["root_cause"] and "segment 7 rejected" in d["root_cause"]
    assert "Treat this as a lead" in d["root_cause"]
    assert any("Search the repository" in s for s in d["suggestions"])
    assert any("Compare with the last passing run" in s for s in d["suggestions"])
    assert not any("fixed" in s.lower() for s in d["suggestions"])


def test_unknown_failure_with_no_error_line_asks_for_the_full_log():
    d = diagnose(f("all quiet\nstill quiet"), [])
    assert d["verdict"] == "unknown" and "Read the full log" in d["suggestions"][0]


def test_unhealthy_runner_is_named_in_the_next_steps():
    health = [{"timestamp_utc": "2026-10-18T10:00:30+00:00", "cpu_percent": 99.0, "memory_percent": 20.0,
               "disk_percent": 12, "disk_free_gb": 25, "agent_service_running": True}]
    d = diagnose(f(MYSTERY), health)
    assert d["verdict"] == "unknown"
    assert any("check the runner first" in s for s in d["suggestions"])


def test_low_confidence_ai_hypothesis_is_offered_as_an_unverified_lead():
    d = diagnose(f(MYSTERY), [])
    out = apply_ai(d, {"confidence": "low", "category": "network", "likely_cause": "the quorum service is unreachable"})
    assert out["category"] == "unknown" and out["classified_by"].startswith("rules (unknown)")
    assert out["suggestions"][0].startswith("Unverified lead from the AI (low confidence): the quorum service")


def test_unknown_failures_are_not_eligible_for_ai_fixes():
    assert eligible(diagnose(f(MYSTERY), [])) is False


def test_the_email_says_a_person_is_needed():
    d = diagnose(f(MYSTERY), [])
    _, text, _ = render_email(d, "https://x", None)
    assert "Needs a person" in text and "Search the repository" in text


ADO_STYLE = "\n".join([
    "2026-10-18T11:00:00Z ##[section]Starting: Reconcile edge cache",
    "2026-10-18T11:00:00Z [command]/usr/bin/bash /home/azureuser/azagent/_work/_temp/abc.sh",
    "2026-10-18T11:00:01Z Reconciling 12 cache shards",
    "2026-10-18T11:00:02Z shard 7 rejected by quorum (2 of 5 replicas agreed)",
    "2026-10-18T11:00:02Z ##[error]Bash exited with code '3'.",
    "2026-10-18T11:00:02Z ##[section]Finishing: Reconcile edge cache",
])


def test_the_generic_exit_line_alone_is_not_a_diagnosis():
    d = diagnose(f(ADO_STYLE, "Reconcile edge cache"), [])
    assert d["category"] == "unknown" and d["verdict"] == "unknown"
    assert any("rejected by quorum" in e for e in d["evidence"])
    assert not any("exited with code" in e for e in d["evidence"])


def test_a_specific_error_line_still_gives_a_script_failure():
    log = "2026-10-18T11:00:01Z ERROR: required config file config/x.json not found\n2026-10-18T11:00:01Z ##[error]Bash exited with code '1'."
    d = diagnose(f(log), [])
    assert d["category"] == "script-failure" and d["evidence"] == ["ERROR: required config file config/x.json not found"]


def test_a_failing_log_with_no_message_at_all_still_points_a_person_somewhere():
    d = diagnose(f("2026-10-18T11:00:02Z ##[error]Bash exited with code '2'."), [])
    assert d["verdict"] == "unknown" and "Read the full log" in d["suggestions"][0]
