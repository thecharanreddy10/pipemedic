import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from diagnose import classify, diagnose, summarize_health  # noqa: E402

START = "2026-10-16T20:10:00Z"
FINISH = "2026-10-16T20:12:00Z"


def sample(ts, cpu=5.0, mem=20.0, disk=12, free=25, svc=True):
    return {
        "timestamp_utc": ts, "cpu_percent": cpu, "memory_percent": mem,
        "disk_percent": disk, "disk_free_gb": free, "agent_service_running": svc,
    }


HEALTHY = [sample("2026-10-16T20:10:30+00:00"), sample("2026-10-16T20:11:30+00:00")]
STRESSED = [sample("2026-10-16T20:10:30+00:00", cpu=98.0), sample("2026-10-16T20:11:30+00:00", cpu=99.5, mem=93.0)]
FULL_DISK = [sample("2026-10-16T20:10:30+00:00", disk=97, free=0.6)]


def failure(step, log="", issues=None):
    return {
        "pipeline": "p", "build_id": 1, "failed_step": step, "log_text": log,
        "issues": issues or [], "agent_name": "pipemedic-agent-01", "hostname": "pipemedicvm1",
        "start": START, "finish": FINISH,
    }


TEST_LOG = """2026-10-16T20:11:00Z FAILED tests/test_inventory.py::test_reserve_reduces_stock - assert 8 == 7
2026-10-16T20:11:00Z 1 failed, 4 passed, 1 warning in 1.27s"""

SCRIPT_LOG = """2026-10-16T20:11:00Z Validating config/orders.prod.json...
2026-10-16T20:11:00Z ERROR: required config file config/orders.prod.json not found"""

VENV_LOG = """2026-10-16T20:11:00Z The virtual environment was not created successfully because ensurepip is not
2026-10-16T20:11:00Z available.  On Debian/Ubuntu systems, you need to install the python3-venv"""

HANG_LOG = "waiting for payment gateway sandbox...\nwaiting for payment gateway sandbox..."
TIMEOUT_ISSUE = "The job running on agent pipemedic-agent-01 ran longer than the maximum time of 2 minutes."


def test_test_failure_on_healthy_runner_is_code():
    d = diagnose(failure("Inventory - run unit tests", TEST_LOG, ["Bash exited with code '1'."]), HEALTHY)
    assert d["category"] == "test-failure"
    assert d["verdict"] == "code-or-config"
    assert any("assert 8 == 7" in e for e in d["evidence"])


def test_script_failure_missing_config():
    d = diagnose(failure("Orders - validate deployment config", SCRIPT_LOG, ["Bash exited with code '1'."]), HEALTHY)
    assert d["category"] == "script-failure"
    assert d["verdict"] == "code-or-config"
    assert any("orders.prod.json" in e for e in d["evidence"])


def test_timeout_on_healthy_runner_blames_the_step():
    d = diagnose(failure("Payments - gateway integration check", HANG_LOG, [TIMEOUT_ISSUE]), HEALTHY)
    assert d["category"] == "timeout"
    assert d["verdict"] == "code-or-config"


def test_timeout_on_stressed_runner_blames_the_runner():
    d = diagnose(failure("Payments - gateway integration check", HANG_LOG, [TIMEOUT_ISSUE]), STRESSED)
    assert d["category"] == "timeout"
    assert d["verdict"] == "runner"
    assert "high-cpu" in d["runner_health"]["flags"]


def test_missing_venv_is_runner_environment():
    d = diagnose(failure("Frontend - install dependencies", VENV_LOG, ["Bash exited with code '127'."]), HEALTHY)
    assert d["category"] == "runner-environment"
    assert d["verdict"] == "runner"
    assert any("python3-venv" in s for s in d["suggestions"])


def test_test_failure_on_stressed_runner_is_mixed():
    d = diagnose(failure("Inventory - run unit tests", TEST_LOG), STRESSED)
    assert d["verdict"] == "mixed"


def test_low_disk_flag():
    assert "low-disk" in summarize_health(FULL_DISK, START, FINISH)["flags"]


def test_no_health_data_is_reported_not_assumed_healthy():
    d = diagnose(failure("Inventory - run unit tests", TEST_LOG), [])
    assert "no-health-data" in d["runner_health"]["flags"]
    assert "state is unknown" in d["root_cause"]
    assert "was healthy" not in d["root_cause"]


def test_timeout_without_health_data_does_not_claim_healthy():
    d = diagnose(failure("Payments - gateway integration check", HANG_LOG, [TIMEOUT_ISSUE]), [])
    assert d["verdict"] == "code-or-config"
    assert "state is unknown" in d["root_cause"]


def test_health_outside_run_window_is_ignored():
    old = [sample("2026-10-16T19:00:00+00:00", cpu=99.0)]
    assert summarize_health(old, START, FINISH)["flags"] == ["no-health-data"]


def test_unknown_failure():
    assert classify(failure("Mystery step", "something odd"))["category"] == "unknown"
