import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ado_browse import (agents_involved, branch_name, duration_label, job_rows, pipeline_rows,  # noqa: E402
                        seconds_between, status_of, step_rows)

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def build(i, definition, result="succeeded", status="completed", queued="2026-10-20T10:00:00Z"):
    return {"id": i, "definition": {"id": definition, "name": f"p{definition}"}, "result": result, "status": status, "queueTime": queued}


def test_status_uses_the_result_when_finished_and_the_state_while_running():
    assert status_of({"result": "failed", "status": "completed"}) == ("critical", "Failed")
    assert status_of({"status": "inProgress"}) == ("info", "Running")
    assert status_of({"status": "notStarted"}) == ("info", "Queued")
    assert status_of({"result": "somethingNew"}) == ("muted", "somethingNew")


def test_duration_counts_up_to_now_for_a_run_that_is_still_going():
    assert seconds_between("2026-10-20T11:58:00Z", "2026-10-20T11:59:30Z") == 90
    assert seconds_between("2026-10-20T11:58:00Z", None, NOW) == 120
    assert seconds_between(None, None, NOW) is None


def test_duration_labels():
    assert [duration_label(x) for x in (None, 42, 90, 3725)] == ["-", "42s", "1m 30s", "1h 02m"]


def test_branch_names_are_shortened():
    assert branch_name("refs/heads/feature/x") == "feature/x" and branch_name(None) == "-"


def test_pipeline_rows_show_latest_run_pass_rate_and_order_by_recent_activity():
    defs = [{"id": 1, "name": "old"}, {"id": 2, "name": "new"}, {"id": 3, "name": "never-run"}]
    builds = [build(1, 1, "failed", queued="2026-10-18T10:00:00Z"),
              build(2, 2, "succeeded", queued="2026-10-20T09:00:00Z"), build(3, 2, "failed", queued="2026-10-20T10:00:00Z"),
              build(4, 2, status="inProgress", result=None, queued="2026-10-20T11:00:00Z")]
    rows = pipeline_rows(defs, builds)
    assert [r["name"] for r in rows] == ["new", "old", "never-run"]
    new = rows[0]
    assert new["last"]["id"] == 4 and new["runs"] == 3 and new["finished"] == 2 and new["pass_rate"] == 50
    assert rows[2]["last"] is None and rows[2]["pass_rate"] is None


TIMELINE = [
    {"id": "j2", "type": "Job", "name": "Test", "workerName": "agent-02", "result": "failed", "startTime": "2026-10-20T10:05:00Z", "finishTime": "2026-10-20T10:07:00Z", "order": 2},
    {"id": "j1", "type": "Job", "name": "Build", "workerName": "agent-01", "result": "succeeded", "startTime": "2026-10-20T10:00:00Z", "finishTime": "2026-10-20T10:04:00Z", "order": 1},
    {"id": "s", "type": "Stage", "name": "CI"},
    {"id": "t1", "type": "Task", "parentId": "j1", "name": "Install", "result": "succeeded", "startTime": "2026-10-20T10:00:10Z", "finishTime": "2026-10-20T10:01:10Z"},
    {"id": "t2", "type": "Task", "parentId": "j2", "name": "pytest", "result": "failed", "startTime": "2026-10-20T10:05:10Z", "finishTime": "2026-10-20T10:06:10Z"},
    {"id": "t3", "type": "Task", "parentId": "j2", "name": "never started", "result": None},
]
HOSTS = {"agent-01": "vm1", "agent-02": "vm2"}


def test_jobs_are_in_order_with_their_agent_and_machine():
    jobs = job_rows(TIMELINE, HOSTS)
    assert [(j["name"], j["agent"], j["host"], j["status"], j["seconds"]) for j in jobs] == [
        ("Build", "agent-01", "vm1", "Succeeded", 240), ("Test", "agent-02", "vm2", "Failed", 120)]


def test_steps_skip_the_ones_that_never_started_and_name_their_job():
    steps = step_rows(TIMELINE)
    assert [(s["job"], s["name"], s["status"]) for s in steps] == [("Build", "Install", "Succeeded"), ("Test", "pytest", "Failed")]


def test_agents_involved_lists_each_agent_once_with_its_jobs_and_whether_one_failed():
    jobs = job_rows(TIMELINE + [{"id": "j3", "type": "Job", "name": "Lint", "workerName": "agent-01", "result": "succeeded",
                                  "startTime": "2026-10-20T10:08:00Z", "finishTime": "2026-10-20T10:09:00Z", "order": 3}], HOSTS)
    agents = agents_involved(jobs)
    a1 = next(a for a in agents if a["agent"] == "agent-01")
    assert len(agents) == 2 and a1["jobs"] == ["Build", "Lint"] and not a1["failed"]
    assert a1["start"] == "2026-10-20T10:00:00Z" and a1["finish"] == "2026-10-20T10:09:00Z"
    assert next(a for a in agents if a["agent"] == "agent-02")["failed"]


def test_a_job_that_has_not_been_assigned_an_agent_yet_is_left_out_of_agents():
    jobs = job_rows([{"id": "j", "type": "Job", "name": "Wait", "state": "pending"}], {})
    assert jobs[0]["agent"] is None and agents_involved(jobs) == []
