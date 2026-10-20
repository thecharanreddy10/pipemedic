"""Sample data for the dashboard when no Azure DevOps token is available (matches the demo runs)."""
from datetime import datetime, timedelta, timezone


def _t(minutes_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def _health(samples=5, cpu_max=3.0, flags=()):
    return {"samples": samples, "cpu_max": cpu_max, "cpu_avg": cpu_max / 2, "memory_max": 21.5,
            "disk_max": 12, "disk_free_gb_min": 25, "agent_service_running": True, "flags": list(flags)}


POOL_AGENTS = [
    {"name": "pipemedic-agent-01", "status": "online", "enabled": True, "version": "5.280.0",
     "assignedRequest": {"definition": {"name": "azure-pipelines-timeout"}}},
    {"name": "pipemedic-agent-02", "status": "online", "enabled": True, "version": "5.280.0", "assignedRequest": None},
]
POOL_REQUESTS = [
    {"queueTime": _t(3), "definition": {"name": "azure-pipelines-test-failure"}},
    {"queueTime": _t(3), "definition": {"name": "azure-pipelines-script-failure"}},
    {"queueTime": _t(12), "assignTime": _t(11), "finishTime": _t(9), "result": "failed", "definition": {"name": "azure-pipelines-timeout"}},
    {"queueTime": _t(20), "assignTime": _t(20), "finishTime": _t(19), "result": "succeeded", "definition": {"name": "azure-pipelines-success"}},
]

BUILDS = [
    {"id": 9, "pipeline": "azure-pipelines-timeout", "result": "failed", "finish": _t(20), "start": _t(23)},
    {"id": 8, "pipeline": "azure-pipelines-test-failure", "result": "failed", "finish": _t(35), "start": _t(37)},
    {"id": 7, "pipeline": "azure-pipelines-timeout", "result": "failed", "finish": _t(37), "start": _t(40)},
    {"id": 6, "pipeline": "azure-pipelines-script-failure", "result": "failed", "finish": _t(41), "start": _t(42)},
    {"id": 5, "pipeline": "azure-pipelines-success", "result": "succeeded", "finish": _t(43), "start": _t(44)},
]

DIAGNOSES = [
    {"pipeline": "azure-pipelines-timeout", "build_id": 9, "agent": "pipemedic-agent-01", "hostname": "pipemedicvm1",
     "failed_step": "Payments - gateway integration check", "category": "timeout", "verdict": "runner",
     "root_cause": "The step hung while pipemedicvm1 was under stress (high-cpu), so resource starvation likely caused or worsened the timeout.",
     "evidence": ["The job ran longer than the maximum time of 2 minutes.", "waiting for payment gateway sandbox..."],
     "suggestions": ["Free resources on the runner or scale the pool.", "Re-run once health is normal."],
     "runner_health": _health(cpu_max=100.0, flags=["high-cpu"])},
    {"pipeline": "azure-pipelines-test-failure", "build_id": 8, "agent": "pipemedic-agent-01", "hostname": "pipemedicvm1",
     "failed_step": "Inventory - run unit tests", "category": "test-failure", "verdict": "code-or-config",
     "root_cause": "pipemedicvm1 was healthy during the run, so the failure comes from the code or configuration, not the runner.",
     "evidence": ["E       assert 8 == 7", "tests/test_inventory.py:24: AssertionError"],
     "suggestions": ["Fix the code under test (or the test if the expectation is wrong)."],
     "runner_health": _health(cpu_max=2.5)},
    {"pipeline": "azure-pipelines-timeout", "build_id": 7, "agent": "pipemedic-agent-01", "hostname": "pipemedicvm1",
     "failed_step": "Payments - gateway integration check", "category": "timeout", "verdict": "code-or-config",
     "root_cause": "pipemedicvm1 was healthy, so the most likely cause is the step itself waiting on something that never responds.",
     "evidence": ["The job ran longer than the maximum time of 2 minutes.", "waiting for payment gateway sandbox..."],
     "suggestions": ["Check the gateway endpoint and add a connect timeout and retry limit."],
     "runner_health": _health(cpu_max=25.1)},
    {"pipeline": "azure-pipelines-script-failure", "build_id": 6, "agent": "pipemedic-agent-01", "hostname": "pipemedicvm1",
     "failed_step": "Orders - validate deployment config", "category": "script-failure", "verdict": "code-or-config",
     "root_cause": "pipemedicvm1 was healthy during the run, so the failure comes from the code or configuration, not the runner.",
     "evidence": ["ERROR: required config file config/orders.prod.json not found"],
     "suggestions": ["Supply the missing config file."],
     "runner_health": _health(cpu_max=25.1)},
]


# Log excerpts so the AI features work on the sample runs too.
_LOGS = {
    9: "Starting a long-running task...\nwaiting for payment gateway sandbox...\nwaiting for payment gateway sandbox...\n"
       "##[error]The job running on agent pipemedic-agent-01 ran longer than the maximum time of 2 minutes.\nThe operation was canceled.",
    8: "FAILED tests/test_inventory.py::test_reserve_reduces_stock - assert 8 == 7\n>       assert r.json()[\"remaining\"] == 7\n"
       "E       assert 8 == 7\n1 failed, 4 passed in 1.27s\n##[error]Bash exited with code '1'.",
    7: "Starting a long-running task...\nwaiting for payment gateway sandbox...\nwaiting for payment gateway sandbox...\n"
       "##[error]The job running on agent pipemedic-agent-01 ran longer than the maximum time of 2 minutes.\nThe operation was canceled.",
    6: "Starting deployment preparation...\nValidating config/orders.prod.json...\n"
       "ERROR: required config file config/orders.prod.json not found\n##[error]Bash exited with code '1'.",
}
for _d in DIAGNOSES:
    _d["log_excerpt"] = _LOGS.get(_d["build_id"], "")


# Examples of the "seen before" note and an AI fix, so the sample view shows them.
for _d in DIAGNOSES:
    if _d["build_id"] == 7:
        _d["similar"] = {"build_id": 3, "pipeline": "azure-pipelines-timeout", "similarity": 0.98, "status": "open", "resolution": "",
                         "summary": "run 3 (azure-pipelines-timeout) looked similar (98%): waiting for payment gateway sandbox... It has not been resolved yet."}
    if _d["build_id"] == 8:
        _d["fix"] = {"status": "opened", "pr_id": 1, "summary": "Fix off-by-one bug in inventory reserve function", "confidence": "high",
                     "files": ["edit services/inventory/app/main.py"], "pr_url": "https://dev.azure.com/Hackathon925/Pipemedic/_git/Pipemedic/pullrequest/1"}
