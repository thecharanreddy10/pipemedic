import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import memory  # noqa: E402
from notify import render_email  # noqa: E402
from watcher import Config, Watcher  # noqa: E402

A, B, C = [1.0, 0.0, 0.0], [0.99, 0.1, 0.0], [0.0, 1.0, 0.0]


def entry(build_id, vec, status="open", resolution="", pipeline="pipe", finish="2026-10-16T20:00:00Z", branch="refs/heads/main"):
    return {"build_id": build_id, "pipeline": pipeline, "category": "timeout", "verdict": "code-or-config", "root_cause": "The step hung.",
            "embedding": vec, "status": status, "resolution": resolution, "finish": finish, "source_branch": branch}


class Store:
    def __init__(self, entries=(), fixes=()):
        self.mem = {e["build_id"]: dict(e) for e in entries}
        self._fixes = list(fixes)
        self.init, self.seen, self.diags, self.hist = True, {}, {}, []

    def memory_entries(self): return [dict(e) for e in self.mem.values()]
    def save_memory(self, e): self.mem[e["build_id"]] = dict(e)
    def update_memory(self, bid, **ch): self.mem[bid].update(ch)
    def fixes(self): return self._fixes
    def initialised(self): return self.init
    def set_initialised(self): pass
    def seen_ids(self): return set(self.seen)
    def mark_seen(self, i, note=""): self.seen[i] = note

    def claim(self, i, note="processing"):
        if i in self.seen:
            return False
        self.seen[i] = note
        return True

    def history(self): return self.hist
    def append_history(self, e): self.hist.append(e)
    def save_diagnosis(self, d): self.diags[d["build_id"]] = d


# ---------------- similarity ----------------
def test_cosine_of_identical_and_orthogonal_vectors():
    assert memory.cosine(A, A) == 1.0 and memory.cosine(A, C) == 0.0 and memory.cosine([0, 0], A) == 0.0


def test_find_similar_applies_threshold_and_excludes_itself():
    entries = [entry(1, A), entry(2, B), entry(3, C), entry(9, A)]
    hits = memory.find_similar(A, entries, exclude_build=9)
    assert [h["build_id"] for h in hits] == [1, 2] and hits[0]["similarity"] >= hits[1]["similarity"]


def test_signature_drops_timestamps_and_keeps_the_key_lines():
    d = {"category": "timeout", "failed_step": "Payments", "root_cause": "hung", "evidence": ["2026-10-16T20:11:00Z waiting for gateway"]}
    sig = memory.signature(d)
    assert "2026-10-16" not in sig and "waiting for gateway" in sig and "Payments" in sig


def test_description_changes_with_status():
    m = {"build_id": 6, "pipeline": "p", "similarity": 0.91, "root_cause": "config missing", "resolution": "added the file", "status": "resolved"}
    assert "resolved: added the file" in memory.describe(m) and "91%" in memory.describe(m)
    assert "A fix is ready" in memory.describe({**m, "status": "fix-ready"})
    assert "not been resolved" in memory.describe({**m, "status": "open", "resolution": ""})


class FakeLLM:
    def __init__(self, vec): self.vec = vec
    def embed(self, texts): return [self.vec for _ in texts]


def test_description_prefers_the_actual_error_line():
    m = {"build_id": 6, "pipeline": "p", "similarity": 0.9, "root_cause": "runner was healthy so it is code", "evidence": "ERROR: config file missing",
         "resolution": "", "status": "open"}
    assert "ERROR: config file missing" in memory.describe(m) and "runner was healthy" not in memory.describe(m)


def test_recall_returns_the_best_match_and_the_embedding():
    store = Store([entry(1, A, "resolved", "added the file"), entry(2, C)])
    match, vec = memory.recall(FakeLLM(B), store, {"build_id": 5, "category": "timeout", "failed_step": "s", "root_cause": "r", "evidence": []})
    assert match["build_id"] == 1 and "resolved" in match["summary"] and vec == B


def test_recall_with_nothing_similar_returns_none():
    match, _ = memory.recall(FakeLLM(C), Store([entry(1, A)]), {"build_id": 5, "category": "x", "failed_step": "s", "root_cause": "r", "evidence": []})
    assert match is None


# ---------------- resolution ----------------
def build(i, result, finish, branch="refs/heads/main", pipeline="pipe"):
    return {"id": i, "result": result, "finishTime": finish, "sourceBranch": branch, "definition": {"name": pipeline}}


def test_a_later_pass_on_the_same_branch_resolves_the_failure():
    store = Store([entry(1, A)])
    notes = memory.resolve([build(2, "succeeded", "2026-10-16T20:30:00Z")], store)
    assert store.mem[1]["status"] == "resolved" and "run 2" in store.mem[1]["resolution"] and "30 minutes" in store.mem[1]["resolution"] and notes


def test_a_pass_on_another_branch_does_not_resolve_it():
    store = Store([entry(1, A)])
    memory.resolve([build(2, "succeeded", "2026-10-16T20:30:00Z", branch="refs/heads/pipemedic/fix-run-1")], store)
    assert store.mem[1]["status"] == "open"


def test_a_pass_before_the_failure_or_in_another_pipeline_does_not_count():
    store = Store([entry(1, A)])
    memory.resolve([build(2, "succeeded", "2026-10-16T19:00:00Z"), build(3, "succeeded", "2026-10-16T21:00:00Z", pipeline="other")], store)
    assert store.mem[1]["status"] == "open"


def test_a_verified_unmerged_fix_shows_as_fix_ready_then_resolves_when_merged():
    fix = {"build_id": 1, "pr_id": 7, "status": "open", "verified": "passed", "title": "Fix it"}
    store = Store([entry(1, A)], fixes=[fix])
    memory.resolve([], store)
    assert store.mem[1]["status"] == "fix-ready" and "7" in store.mem[1]["resolution"]
    fix["status"] = "completed"
    memory.resolve([build(2, "succeeded", "2026-10-16T21:00:00Z")], store)
    assert store.mem[1]["status"] == "resolved" and "fixed by pull request 7" in store.mem[1]["resolution"]


# ---------------- in the watcher and the email ----------------
def test_watcher_recalls_and_remembers_and_the_email_mentions_it():
    sent = []
    store = Store([entry(1, A, "resolved", "added the config file", pipeline="p")])
    cfg = Config(health_api="x", notify_on=True, notify_default_to="o@x.com", ai_on=False, memory_on=True)

    class Ado:
        def recent_builds(self, n): return [{"id": 1, "result": "failed", "sourceVersion": "a", "definition": {"id": 3, "name": "p"}},
                                            {"id": 2, "result": "failed", "sourceVersion": "a", "definition": {"id": 3, "name": "p"}}]
        def agent_hosts(self, pool): return {"a1": "vm1"}

    def collect(ado, b, hosts):
        return {"pipeline": "p", "pipeline_id": 3, "build_id": b["id"], "failed_step": "s", "issues": [], "log_text": "ERROR: x\n##[error]Bash exited with code '1'.",
                "agent_name": "a1", "hostname": "vm1", "start": "2026-10-16T20:10:00Z", "finish": "2026-10-16T20:11:00Z"}

    w = Watcher(cfg, Ado(), store, health_fetch=lambda *a: [], llm=FakeLLM(B), mailer=lambda to, s, t, h: sent.append(t) or "ok", collect=collect)
    store.seen = {1: "baseline"}
    w.run_once()
    d = store.diags[2]
    assert d["similar"]["build_id"] == 1 and 2 in store.mem and store.mem[2]["status"] == "open"
    assert "Seen before" in sent[0] and "added the config file" in sent[0]


def test_email_leaves_out_the_memory_section_when_there_is_none():
    d = {"pipeline": "p", "build_id": 2, "failed_step": "s", "verdict": "runner", "root_cause": "rc", "evidence": [], "suggestions": [],
         "runner_health": {"samples": 0}, "similar": None}
    assert "Seen before" not in render_email(d, "u", None)[1]
