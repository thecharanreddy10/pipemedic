"""Generic behaviour: varied failures from projects other than ours, a config test, and a guard against demo names."""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config  # noqa: E402
from diagnose import apply_ai, classify  # noqa: E402

CASES = [
    ("java build", "[ERROR] Failed to execute goal: BUILD FAILURE\n##[error]Process completed with exit code 1.", "script-failure"),
    ("jest tests", "FAIL src/cart.test.js\nTests: 2 failed, 40 passed\n##[error]Bash exited with code '1'.", "test-failure"),
    ("pip missing module", "Traceback\nModuleNotFoundError: No module named 'requests'", "dependency"),
    ("permission denied", "cp: cannot create file '/opt/app/x': Permission denied\n##[error]exit code 1", "permissions"),
    ("cloud auth", "ERROR: (AuthorizationFailed) The client does not have authorization to perform action", "permissions"),
    ("network refused", "curl: (7) Failed to connect: Connection refused\n##[error]exit code 7", "network"),
    ("dns failure", "Could not resolve host: registry.example.com", "network"),
    ("disk full", "write error: No space left on device\n##[error]exit code 1", "runner-environment"),
    ("missing tool", "bash: terraform: command not found\n##[error]exit code 127", "runner-environment"),
    ("timeout", "The job ran longer than the maximum time of 60 minutes.", "timeout"),
]


def f(log):
    return {"pipeline": "p", "build_id": 1, "failed_step": "step", "log_text": log, "issues": [], "agent_name": "a",
            "hostname": "h", "start": "2026-10-16T20:00:00Z", "finish": "2026-10-16T20:02:00Z"}


def test_varied_failures_are_classified_without_knowing_our_pipelines():
    wrong = [(name, classify(f(log))["category"]) for name, log, want in CASES if classify(f(log))["category"] != want]
    assert wrong == [], f"misclassified: {wrong}"


def test_http_403_and_401_are_permission_failures_but_longer_numbers_are_not():
    # Found by the real-log benchmark: the word boundaries around 403/401 had been saved as backspace characters,
    # so an HTTP 403 from git never matched.
    git = "fatal: unable to access 'https://host/repo.git/': The requested URL returned error: 403\n##[error]exit code 128"
    assert classify(f(git))["category"] == "permissions"
    assert classify(f("request failed: HTTP 401 from the registry"))["category"] == "permissions"
    assert classify(f("wrote 14031 bytes\nerror: could not parse the manifest"))["category"] != "permissions"
    assert "\x08" not in open(os.path.join(os.path.dirname(__file__), "..", "diagnose.py"), encoding="utf-8").read()


def test_a_confident_ai_reading_is_added_for_an_unknown_cause_without_changing_the_category():
    d = {"pipeline": "p", "build_id": 1, "failed_step": "deploy", "category": "unknown", "verdict": "unknown", "hostname": "h",
         "root_cause": "Could not be identified.", "suggestions": ["Read the full log."], "evidence": [],
         "runner_health": {"samples": 3, "flags": [], "cpu_max": 3.0, "memory_max": 20.0, "disk_max": 12.0}}
    ai = {"category": "permissions", "confidence": "high", "likely_cause": "the service principal lacks a role",
          "suggested_fixes": ["Grant the role", "Re-run"], "evidence_verified": ["x"]}
    out = apply_ai(d, ai)
    assert out["category"] == "unknown" and out["verdict"] == "unknown"
    assert "AI reading (high confidence, not confirmed by the rules): the service principal lacks a role" in out["root_cause"]
    assert out["suggestions"][0] == "AI suggestion (unverified): Grant the role" and "Read the full log." in out["suggestions"]


def _generic():
    return {"pipeline": "p", "build_id": 1, "failed_step": "Build image", "category": "script-failure", "verdict": "code-or-config",
            "hostname": None, "root_cause": "r", "suggestions": ["s1"], "evidence": ["E: Unable to locate package x"],
            "runner_health": {"samples": 0, "flags": ["no-health-data"]}}


def test_the_ai_can_refine_the_generic_script_failure_when_its_quotes_are_in_the_log():
    ai = {"category": "dependency", "confidence": "high", "likely_cause": "a package does not exist",
          "evidence_verified": ["E: Unable to locate package x"], "suggested_fixes": ["Fix the name"]}
    out = apply_ai(_generic(), ai)
    assert out["category"] == "dependency" and out["rules_category"] == "script-failure"
    assert out["verdict"] == "code-or-config" and out["evidence"] == ["E: Unable to locate package x"]
    assert out["suggestions"] != ["s1"]  # the advice now matches the refined category
    assert "refined" in out["classified_by"]


def test_the_ai_cannot_refine_without_verified_quotes_or_with_low_confidence_or_into_a_runner_blame():
    base = {"category": "dependency", "confidence": "high", "likely_cause": "x", "evidence_verified": ["q"]}
    for ai in [dict(base, evidence_verified=[]), dict(base, confidence="low"), dict(base, category="timeout"),
               dict(base, category="runner-environment"), dict(base, category="unknown"), dict(base, category="configuration")]:
        out = apply_ai(_generic(), ai)
        assert out["category"] == "script-failure", ai


def test_the_ai_never_refines_a_failure_the_rules_recognised():
    d = _generic()
    d["category"] = "test-failure"
    out = apply_ai(d, {"category": "network", "confidence": "high", "likely_cause": "dns", "evidence_verified": ["q"]})
    assert out["category"] == "test-failure"


def test_the_ai_cannot_overrule_a_failure_the_rules_recognise():
    d = {"pipeline": "p", "build_id": 1, "failed_step": "s", "category": "script-failure", "verdict": "code-or-config", "hostname": "h",
         "root_cause": "r", "suggestions": ["s1"], "evidence": [], "runner_health": {"samples": 3, "flags": []}}
    out = apply_ai(d, {"category": "network", "confidence": "high", "likely_cause": "dns", "suggested_fixes": ["x"]})
    assert out["category"] == "script-failure" and out["suggestions"] == ["s1"]
    assert out["classified_by"] == "rules (the AI suggested network)"


def test_ai_cannot_blame_the_runner_without_health_evidence():
    d = {"pipeline": "p", "build_id": 1, "failed_step": "s", "category": "script-failure", "verdict": "code-or-config", "hostname": "h",
         "root_cause": "r", "suggestions": [], "evidence": [], "runner_health": {"samples": 3, "flags": [], "cpu_max": 3.0, "memory_max": 20.0, "disk_max": 12.0}}
    out = apply_ai(d, {"category": "timeout", "confidence": "high", "likely_cause": "runner slow", "evidence_verified": []})
    assert out["verdict"] == "code-or-config"  # healthy runner: the verdict stays with the code


def test_low_confidence_or_failed_ai_keeps_the_rules():
    d = {"pipeline": "p", "build_id": 1, "failed_step": "s", "category": "script-failure", "verdict": "code-or-config", "hostname": "h",
         "root_cause": "r", "suggestions": [], "evidence": [], "runner_health": {}}
    assert apply_ai(dict(d), {"category": "network", "confidence": "low"})["category"] == "script-failure"
    assert apply_ai(dict(d), {"error": "down"})["classified_by"] == "rules"


def test_settings_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("ADO_ORG", "other-org")
    monkeypatch.setenv("ADO_PROJECT", "billing")
    monkeypatch.setenv("ADO_POOL", "build-pool")
    monkeypatch.setenv("CODE_ROOTS", "src,lib")
    s = config.settings()
    assert (s.org, s.project, s.pool) == ("other-org", "billing", "build-pool")
    assert config.under_code_root("src/app/main.py") and not config.under_code_root("services/x.py")
    assert s.web_url == "https://dev.azure.com/other-org/billing"


def test_test_files_are_recognised_by_location_or_name():
    assert config.is_test_path("services/a/tests/test_x.py") and config.is_test_path("src/foo_test.py")
    assert not config.is_test_path("services/a/app/main.py")


def test_no_project_names_are_hard_coded_outside_config_and_samples():
    """Guard: project-specific names belong in config.py (or environment), not scattered through the code."""
    root = os.path.join(os.path.dirname(__file__), "..")
    allowed = {"config.py", "sample_data.py", "gen_arch.py"}
    banned = re.compile(r"Hackathon925|pipemedic-pool|pipemedic-api-16135|pipemedicst25011|pipemedic-ai-14246|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?!/\.default)")  # names, and any subscription id (not a sign-in scope like <id>/.default)
    offenders = []
    for name in os.listdir(root):
        if not name.endswith(".py") or name in allowed:
            continue
        with open(os.path.join(root, name), encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                if banned.search(line):
                    offenders.append(f"{name}:{i}")
    assert offenders == [], offenders


def test_the_watcher_builds_its_config_from_the_environment(monkeypatch):
    """Regression: the cloud timer crashed here because a default was read from a missing class attribute."""
    from watcher import Config
    monkeypatch.setenv("ADO_ORG", "other-org")
    monkeypatch.setenv("NOTIFY_ENABLED", "1")
    cfg = Config.from_env()
    assert cfg.org == "other-org" and cfg.notify_on is True and cfg.project and cfg.pool


def test_dashboard_only_merges_watcher_records_for_the_watched_project():
    import ast
    src = open(os.path.join(os.path.dirname(__file__), "..", "ui_data.py"), encoding="utf-8").read()
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "watched_project")
    ns = {"cfg": config.settings()}
    exec(compile(ast.Module([fn], []), "dash", "exec"), ns)
    c = config.settings()
    assert ns["watched_project"](c.org.upper(), c.project) is True
    assert ns["watched_project"]("someone-else", c.project) is False
    assert ns["watched_project"](c.org, "another-project") is False


def test_health_is_not_fetched_for_a_run_whose_runner_is_unknown(monkeypatch):
    import pipemedic_agent
    def boom(*a, **k): raise AssertionError("must not call the health API without a hostname")
    monkeypatch.setattr(pipemedic_agent.requests, "get", boom)
    assert pipemedic_agent.fetch_health("http://x", None, "2026-10-16T20:00:00Z") == []


def test_a_microsoft_hosted_pool_is_recognised_from_the_pool_record():
    import pipemedic_agent
    class R:
        def __init__(self, v): self.v = v
        def json(self): return {"value": self.v}
    for pools, want in [([{"id": 9, "isHosted": True}], True), ([{"id": 1, "isHosted": False}], False), ([{"id": 1}], False), ([], False)]:
        a = pipemedic_agent.AzureDevOps.__new__(pipemedic_agent.AzureDevOps)
        a.base = "x"
        a._get = lambda url, **kw: R(pools)
        assert a.pool_is_hosted("p") is want
