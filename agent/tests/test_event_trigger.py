import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import remediate  # noqa: E402
from ai_diagnose import ai_diagnose  # noqa: E402
from watcher import Config, Watcher  # noqa: E402

TEST_LOG = "FAILED tests/test_x.py::test_a - assert 1 == 2\n##[error]Bash exited with code '1'."
OOM_LOGS = {"available": True, "window": "11:58 to 12:07 UTC", "hostname": "vm1", "error": None,
            "text": "### kernel\nOct 20 12:03:10 vm1 kernel: Out of memory: Killed process 4121 (python3)",
            "findings": [{"flag": "out-of-memory (runner log)", "message": "m", "line": "l", "section": "kernel"}]}


class Store:
    def __init__(self):
        self.seen, self.diags, self.hist = {}, {}, []

    def initialised(self): return True
    def set_initialised(self): pass
    def seen_ids(self): return set(self.seen)
    def mark_seen(self, i, note=""): self.seen[i] = note

    def claim(self, i, note="processing"):
        if i in self.seen:
            return False
        self.seen[i] = note
        return True

    def history(self): return list(self.hist)
    def append_history(self, e): self.hist.append(e)
    def save_diagnosis(self, d): self.diags[d["build_id"]] = d
    def get_setting(self, *a, **k): return None


class Ado:
    def __init__(self, builds):
        self.builds = {b["id"]: b for b in builds}

    def build(self, i): return self.builds[i]
    def recent_builds(self, n): return list(self.builds.values())
    def agent_hosts(self, pool): return {"a1": "vm1"}
    def agent_busy(self, pool, name): return False
    def create_work_item(self, *a, **k): return 1
    def queue_run(self, *a, **k): return 99


def b(i, result="failed", status="completed"):
    return {"id": i, "result": result, "status": status, "sourceVersion": "abc", "definition": {"id": 3, "name": "p"}}


def collect(ado, build, hosts):
    return {"pipeline": "p", "pipeline_id": 3, "build_id": build["id"], "failed_step": "Unit tests", "issues": [], "log_text": TEST_LOG,
            "other_logs": "=== Lint ===\nE501 line too long", "agent_name": "a1", "hostname": "vm1",
            "start": "2026-10-20T12:00:00Z", "finish": "2026-10-20T12:05:00Z", "requested_for": "dev@example.com", "source_branch": "refs/heads/main"}


def healthy(api, host, start):
    return [{"timestamp_utc": "2026-10-20T12:02:00+00:00", "cpu_percent": 4.0, "memory_percent": 20.0, "disk_percent": 12,
             "disk_free_gb": 25, "agent_service_running": True}]


def make(builds, runner_logs=OOM_LOGS, **cfg):
    sent, fetched = [], []
    store = Store()
    remediate.configure_store(store)

    def fetch(host, start, finish):
        fetched.append((host, start, finish))
        return runner_logs

    cfg.setdefault("runner_logs", True)
    w = Watcher(Config(health_api="x", notify_on=True, notify_default_to="boss@example.com", **cfg), Ado(builds), store,
                health_fetch=healthy, collect=collect, runner_log_fetch=fetch,
                mailer=lambda to, s, t, h: sent.append((to, s)) or "Succeeded")
    return w, store, sent, fetched


# ---------------- the hook ----------------
def test_a_build_completed_event_is_investigated_at_once_and_emailed():
    w, store, sent, _ = make([b(5)])
    out = w.handle_event(5)
    assert out["run"] == 5 and len(sent) == 1 and 5 in store.diags and store.seen[5].startswith("done")


def test_the_timer_does_not_repeat_a_run_the_hook_already_handled():
    w, store, sent, _ = make([b(5)])
    w.handle_event(5)
    assert w.run_once()["handled"] == 0 and len(sent) == 1


def test_the_hook_does_not_repeat_a_run_the_timer_already_handled():
    w, store, sent, _ = make([b(5)])
    w.run_once()
    assert w.handle_event(5) == {"run": 5, "skipped": "already handled"} and len(sent) == 1


def test_the_same_event_delivered_twice_is_handled_once():
    w, store, sent, _ = make([b(5)])
    w.handle_event(5)
    assert w.handle_event(5)["skipped"] == "already handled" and len(sent) == 1


def test_events_for_runs_that_did_not_fail_are_ignored():
    w, store, sent, _ = make([b(5, "succeeded"), b(6, None, "inProgress")])
    assert w.handle_event(5)["skipped"] == "the run did not fail" and w.handle_event(6)["skipped"] == "the run did not fail"
    assert sent == [] and store.seen == {}


# ---------------- the runner's logs ----------------
def test_runner_logs_are_read_for_the_machine_and_window_of_the_failed_run():
    w, store, sent, fetched = make([b(5)])
    w.handle_event(5)
    assert fetched == [("vm1", "2026-10-20T12:00:00Z", "2026-10-20T12:05:00Z")]


def test_a_problem_in_the_runner_logs_changes_the_verdict_and_is_kept_with_the_diagnosis():
    w, store, sent, _ = make([b(5)])
    w.handle_event(5)
    d = store.diags[5]
    assert d["verdict"] == "mixed" and d["runner_logs"]["findings"][0]["flag"] == "out-of-memory (runner log)"
    assert "other_logs" in d and "Lint" in d["other_logs"]


def test_without_the_setting_no_runner_logs_are_read():
    w, store, sent, fetched = make([b(5)], runner_logs=OOM_LOGS)
    w.cfg.runner_logs = False
    w.handle_event(5)
    assert fetched == [] and store.diags[5]["verdict"] == "code-or-config" and store.diags[5]["runner_logs"] is None


def test_unreachable_runner_logs_never_stop_the_investigation():
    w, store, sent, _ = make([b(5)])
    w.runner_log_fetch = lambda *a: (_ for _ in ()).throw(RuntimeError("vm unreachable"))
    assert w.handle_event(5)["run"] == 5 and store.diags[5]["verdict"] == "code-or-config"


def test_with_runner_logs_on_the_timer_takes_fewer_runs_per_check():
    w, store, sent, _ = make([b(i) for i in range(1, 10)], runner_logs=OOM_LOGS)
    w.cfg.runner_logs = True
    store.seen = {}
    assert w.run_once()["handled"] == 2


# ---------------- what the model is given ----------------
class FakeLlm:
    label = "fake"

    def __init__(self, evidence):
        self.evidence, self.prompt = evidence, ""

    def chat(self, messages, json_schema=None, max_tokens=0):
        self.prompt = messages[1]["content"]
        return {"content": json.dumps({"category": "test-failure", "likely_cause": "x", "confidence": "high", "evidence": self.evidence,
                                       "suggested_fixes": ["y"], "runner_related": True,
                                       "runner_effect": "The kernel killed python3 for lack of memory."})}


def diagnosis_with_logs():
    return {"pipeline": "p", "build_id": 5, "failed_step": "Unit tests", "hostname": "vm1", "category": "test-failure",
            "verdict": "mixed", "log_excerpt": TEST_LOG, "other_logs": "=== Lint ===\nE501 line too long", "runner_logs": OOM_LOGS,
            "runner_health": {"samples": 1, "cpu_max": 4.0, "memory_max": 20.0, "disk_max": 12.0, "flags": []}}


def test_the_model_is_given_every_failed_step_and_the_runner_logs_and_explains_the_runner_effect():
    llm = FakeLlm(["E501 line too long", "Out of memory: Killed process 4121 (python3)"])
    out = ai_diagnose(llm, diagnosis_with_logs())
    assert "Other failed steps" in llm.prompt and "E501 line too long" in llm.prompt
    assert "runner's own logs around the failure (11:58 to 12:07 UTC)" in llm.prompt and "Out of memory" in llm.prompt
    assert out["runner_effect"] == "The kernel killed python3 for lack of memory."
    assert len(out["evidence_verified"]) == 2 and out["confidence"] == "high"  # quotes from the other logs count as real


def test_a_quote_that_is_in_no_log_is_still_rejected():
    out = ai_diagnose(FakeLlm(["a line the model made up"]), diagnosis_with_logs())
    assert out["evidence_verified"] == [] and out["confidence"] == "low"


def test_the_model_is_told_when_there_are_no_runner_logs_and_secrets_in_them_are_removed():
    d = diagnosis_with_logs()
    d["runner_logs"] = {"available": False, "error": "vm unreachable"}
    llm = FakeLlm([])
    ai_diagnose(llm, d)
    assert "runner's own logs were not available" in llm.prompt
    d["runner_logs"] = {**OOM_LOGS, "text": "### agent-service\nusing token=abcd1234secretvalue to connect"}
    ai_diagnose(llm, d)
    assert "secretvalue" not in llm.prompt and "[REDACTED]" in llm.prompt
