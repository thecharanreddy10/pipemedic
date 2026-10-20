import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai_diagnose import ai_diagnose, excerpt, redact, verify_quotes  # noqa: E402
from chat_agent import TOOLS, ask, run_tool  # noqa: E402
from diagnose import diagnose  # noqa: E402

LOG = """2026-10-16T20:11:00Z Validating config/orders.prod.json...
2026-10-16T20:11:00Z ERROR: required config file config/orders.prod.json not found
2026-10-16T20:11:00Z ##[error]Bash exited with code '1'."""


class FakeLLM:
    label = "fake-model"

    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def chat(self, messages, tools=None, json_schema=None, max_tokens=0):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools, "schema": json_schema})
        return self.replies.pop(0)


def diag(**kw):
    base = {"pipeline": "azure-pipelines-script-failure", "build_id": 6, "failed_step": "Orders - validate config",
            "category": "script-failure", "verdict": "code-or-config", "root_cause": "rc", "evidence": ["e1"],
            "suggestions": ["s1"], "hostname": "vm1", "agent": "a1", "log_excerpt": excerpt(LOG), "flaky": None,
            "runner_health": {"samples": 3, "cpu_max": 4.0, "memory_max": 20.0, "disk_max": 12.0, "disk_free_gb_min": 25, "flags": []}}
    base.update(kw)
    return base


def reply(**kw):
    out = {"category": "script-failure", "likely_cause": "The required config file is missing.", "confidence": "high",
           "evidence": ["ERROR: required config file config/orders.prod.json not found"],
           "suggested_fixes": ["Add config/orders.prod.json"], "runner_related": False}
    out.update(kw)
    return {"role": "assistant", "content": json.dumps(out)}


# ---------------- redaction and verification ----------------
def test_redacts_bearer_tokens_and_passwords():
    t = redact("Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789\npassword=hunter2 token: abc123")
    assert "abcdefghijklmnopqrstuvwxyz" not in t and "hunter2" not in t and "abc123" not in t
    assert t.count("[REDACTED]") >= 3


def test_redacts_long_opaque_strings_but_not_normal_paths():
    secret = "Zx9kQ2mPaL7vNb4TcR8yUe3WdHs6JfGt1KoXqVn5BiCz0MA2lEyD4rSuPw8h"
    path = "/home/azureuser/azagent/_work/1/s/services/orders/validate_config.sh"
    out = redact(f"pat={secret} and {path}")
    assert secret not in out and path in out


def test_excerpt_strips_timestamps_and_keeps_the_tail():
    log = "\n".join(f"2026-10-16T20:11:00Z line {i}" for i in range(300))
    out = excerpt(log, max_lines=5)
    assert out.splitlines()[-1] == "line 299" and len(out.splitlines()) == 5 and "2026-10-16" not in out


def test_verify_quotes_splits_real_from_invented():
    ok, bad = verify_quotes(["ERROR: required config file config/orders.prod.json not found", "Disk is full"], LOG)
    assert ok == ["ERROR: required config file config/orders.prod.json not found"] and bad == ["Disk is full"]


def test_verify_ignores_case_and_spacing():
    ok, _ = verify_quotes(["error:  required config  file config/orders.prod.json NOT FOUND"], LOG)
    assert len(ok) == 1


# ---------------- AI second opinion ----------------
def test_agreeing_opinion_with_verified_evidence():
    r = ai_diagnose(FakeLLM([reply()]), diag())
    assert r["agrees_with_rules"] and r["confidence"] == "high" and len(r["evidence_verified"]) == 1


def test_invented_quotes_lower_confidence_to_low():
    r = ai_diagnose(FakeLLM([reply(evidence=["Segmentation fault in libfoo"])]), diag())
    assert r["confidence"] == "low" and r["evidence_unverified"] and not r["evidence_verified"]


def test_disagreement_is_reported_not_hidden():
    r = ai_diagnose(FakeLLM([reply(category="permissions")]), diag())
    assert r["agrees_with_rules"] is False


def test_configuration_and_script_failure_are_the_same_family():
    r = ai_diagnose(FakeLLM([reply(category="configuration")]), diag())
    assert r["agrees_with_rules"]
    assert not ai_diagnose(FakeLLM([reply(category="timeout")]), diag())["agrees_with_rules"]


def test_flaky_verdict_counts_as_test_failure_for_agreement():
    r = ai_diagnose(FakeLLM([reply(category="test-failure")]), diag(category="test-failure", verdict="flaky"))
    assert r["agrees_with_rules"]


def test_secrets_never_reach_the_model():
    secret = "Zx9kQ2mPaL7vNb4TcR8yUe3WdHs6JfGt1KoXqVn5BiCz0MA2lEyD4rSuPw8h"
    llm = FakeLLM([reply()])
    ai_diagnose(llm, diag(log_excerpt=f"using token {secret}\nERROR: boom"))
    sent = json.dumps(llm.calls[0]["messages"])
    assert secret not in sent and llm.calls[0]["schema"] is not None


def test_bad_json_raises():
    with pytest.raises(RuntimeError):
        ai_diagnose(FakeLLM([{"role": "assistant", "content": "not json"}]), diag())


def test_diagnose_carries_a_log_excerpt():
    f = {"pipeline": "p", "build_id": 1, "failed_step": "s", "log_text": LOG, "issues": [], "agent_name": "a",
         "hostname": "h", "start": "2026-10-16T20:10:00Z", "finish": "2026-10-16T20:12:00Z"}
    assert "required config file" in diagnose(f, [])["log_excerpt"]


# ---------------- chat ----------------
def ctx():
    now = datetime.now(timezone.utc)
    rec = lambda host, cpu, mins: {"hostname": host, "timestamp_utc": (now - timedelta(minutes=mins)).isoformat(),  # noqa: E731
                                   "cpu_percent": cpu, "memory_percent": 20.0, "disk_percent": 12.0, "disk_free_gb": 25}
    return {
        "runs": [{"id": 5, "pipeline": "ok", "result": "succeeded", "finish": "t"}, {"id": 9, "pipeline": "slow", "result": "failed", "finish": "t"}],
        "diags": [diag(build_id=9, verdict="runner", category="timeout", pipeline="slow")],
        "pool": {"agents_total": 2, "agents_online": 2, "agents_busy": 1, "agents_idle": 1, "utilization_pct": 50, "jobs_queued": 2,
                 "oldest_wait_s": 90, "avg_wait_s": 4.0, "versions": ["5.280.0"], "findings": [{"message": "queue with idle agent"}]},
        "alerts": [], "health": [rec("vm1", 100.0, 3), rec("vm1", 5.0, 1), rec("vm2", 7.0, 2), rec("vm2", 6.0, 500)],
    }


def test_tools_are_read_only():
    names = {t["function"]["name"] for t in TOOLS}
    assert names == {"list_runs", "list_failures", "get_failure", "runner_health", "pool_status", "predictive_alerts"}
    assert not any(w in n for n in names for w in ("retry", "clean", "create", "delete", "run_pipeline"))


def test_run_tools_return_real_data():
    c = ctx()
    assert [r["run"] for r in run_tool("list_runs", {"result": "failed"}, c)] == [9]
    assert run_tool("list_failures", {"verdict": "runner"}, c)[0]["run"] == 9
    assert run_tool("get_failure", {"build_id": 404}, c)["error"]
    h = run_tool("runner_health", {"minutes": 60}, c)
    assert h["vm1"]["cpu_peak"] == 100.0 and h["vm1"]["latest_cpu"] == 5.0 and h["vm2"]["samples"] == 1  # old sample excluded
    assert run_tool("pool_status", {}, c)["jobs_queued"] == 2
    assert run_tool("predictive_alerts", {}, c) == "none"
    assert run_tool("nope", {}, c)["error"]


def test_chat_calls_a_tool_then_answers_from_its_result():
    call = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "list_failures", "arguments": json.dumps({"verdict": "runner"})}}]}
    llm = FakeLLM([call, {"role": "assistant", "content": "Run 9 failed because of the runner."}])
    out = ask(llm, "Which failures were the runner's fault?", ctx())
    assert out["answer"].startswith("Run 9") and out["trace"][0]["tool"] == "list_failures"
    tool_msg = llm.calls[1]["messages"][-1]
    assert tool_msg["role"] == "tool" and '"run": 9' in tool_msg["content"]


def test_chat_stops_after_too_many_steps():
    loop = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "x", "type": "function", "function": {"name": "pool_status", "arguments": "{}"}}]}
    out = ask(FakeLLM([loop] * 10), "again?", ctx(), max_steps=3)
    assert "too many steps" in out["answer"] and len(out["trace"]) == 3


def test_chat_survives_bad_tool_arguments():
    call = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "list_runs", "arguments": "{oops"}}]}
    out = ask(FakeLLM([call, {"role": "assistant", "content": "ok"}]), "runs?", ctx())
    assert out["answer"] == "ok"


# ---------------- model client ----------------
def test_tenant_mismatch_gives_an_actionable_message(monkeypatch):
    import llm as llm_mod

    class Resp:
        status_code, ok = 400, False
        text = '{"error":{"code":"Tenant provided in token does not match resource token","message":"Token tenant X does not match resource tenant."}}'
        headers = {}

    monkeypatch.setattr(llm_mod.FoundryLLM, "_token", lambda self: "t")
    monkeypatch.setattr(llm_mod.requests, "post", lambda *a, **k: Resp())
    with pytest.raises(llm_mod.AIUnavailable) as e:
        llm_mod.FoundryLLM().chat([{"role": "user", "content": "hi"}])
    assert "az login" in str(e.value) and "different Azure tenant" in str(e.value)
