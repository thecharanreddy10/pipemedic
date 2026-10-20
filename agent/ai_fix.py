"""AI-proposed fixes, delivered as pull requests that a person reviews.

The model proposes full replacement contents for a few files. Hard checks run on the proposal before any branch or
pull request exists: only files under the configured code roots, never a test file, never a pipeline file, existing files only edited,
new files only created, Python and JSON must parse, and size limits. Nothing is ever merged automatically, and the
pipeline is re-run on the new branch so the pull request carries its own proof.
"""
import ast
import json
import os
import re

from ai_diagnose import redact

import config
FIXABLE_CATEGORIES = {"test-failure", "script-failure"}
FIXABLE_VERDICTS = {"code-or-config", "mixed"}
MAX_FILES = 3
MAX_OPEN_PER_PIPELINE = int(os.getenv("MAX_OPEN_FIXES_PER_PIPELINE", "3"))  # open fixes waiting for review, per pipeline
MAX_FILE_CHARS = 15000
MAX_CONTEXT_FILES = 6

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["summary", "explanation", "confidence", "risk", "changes"],
    "properties": {
        "summary": {"type": "string"},
        "explanation": {"type": "string"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "risk": {"type": "string"},
        "changes": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["path", "action", "new_content"],
            "properties": {"path": {"type": "string"}, "action": {"type": "string", "enum": ["edit", "create"]},
                           "new_content": {"type": "string"}}}},
    },
}

SYSTEM = (
    "You are a careful senior engineer fixing one failing CI run. Make the smallest change that fixes the ROOT CAUSE "
    "shown in the log. Rules: change only files under the code roots given to you. NEVER modify a test file or its expectations to make a "
    "test pass; fix the code under test. NEVER touch pipeline definitions. Return the FULL new content of every file you "
    "change (not a diff). Use action 'edit' for an existing file and 'create' for a new file. If a file the log says is "
    "missing must be created, choose its path from the repository file list. If you are not sure what the fix is, return "
    "no changes and explain why. NEVER weaken, skip or remove a check, validation, assertion, raise or non-zero exit to "
    "make a failure go away: if a required file is missing, CREATE it with minimal valid content instead of making the "
    "script tolerate its absence. In any file you create, use the obvious placeholder REPLACE_ME for anything "
    "environment-specific you cannot know (host names, addresses, ports of real systems, credentials, ids); never invent "
    "realistic-looking production values. Keep the explanation short and concrete."
)


def eligible(d: dict) -> bool:
    return d.get("category") in FIXABLE_CATEGORIES and d.get("verdict") in FIXABLE_VERDICTS


def _is_test_path(path: str) -> bool:
    return config.is_test_path(path)


def pick_context(tree_paths: list[str], log: str, limit: int = MAX_CONTEXT_FILES) -> list[str]:
    """Choose which repository files to show the model: those named in the log, plus the service's source files."""
    named = set(re.findall(r"[\w./-]+\.(?:py|sh|json|ya?ml|txt|js|ts|cfg|ini|toml)", log or ""))
    chosen: list[str] = []
    services = set()
    for path in tree_paths:
        if any(path == n or path.endswith("/" + n) for n in named):
            chosen.append(path)
            parts = path.split("/")
            if len(parts) >= 2:
                services.add("/".join(parts[:2]))
    for path in tree_paths:  # the service's own source and scripts (not tests) give the model the code under test
        if any(path.startswith(s + "/") for s in services) and path not in chosen and not _is_test_path(path) \
                and path.endswith((".py", ".sh", ".json", ".js", ".ts")) and "/__pycache__/" not in path:
            chosen.append(path)
    return chosen[:limit]


_GUARDS = re.compile(r"exit\s+[1-9]|\braise\b|\bassert\b|sys\.exit|set\s+-e")


def _guard_count(text: str) -> int:
    return len(_GUARDS.findall(text or ""))


def validate(proposal: dict, existing: set[str], old_contents: dict[str, str] | None = None) -> list[str]:
    """Return the list of reasons the proposal must be rejected (empty means acceptable)."""
    old_contents = old_contents or {}
    problems = []
    changes = proposal.get("changes", [])
    if not changes:
        return ["the model proposed no change"]
    if len(changes) > MAX_FILES:
        problems.append(f"too many files changed ({len(changes)} > {MAX_FILES})")
    seen = set()
    for c in changes:
        path, action, content = c["path"], c["action"], c["new_content"]
        if path in seen:
            problems.append(f"{path} appears twice")
        seen.add(path)
        if ".." in path.split("/") or path.startswith("/") or not config.under_code_root(path):
            problems.append(f"{path} is outside the code roots ({', '.join(config.settings().code_roots)})")
        if _is_test_path(path):
            problems.append(f"{path} is a test file; fixes must change the code, not the tests")
        if path.endswith((".yml", ".yaml")) and "pipeline" in path.lower():
            problems.append(f"{path} is a pipeline definition")
        if action == "edit" and path not in existing:
            problems.append(f"{path} does not exist, so it cannot be edited")
        if action == "create" and path in existing:
            problems.append(f"{path} already exists, so it cannot be created")
        if action == "edit" and path in old_contents and _guard_count(content) < _guard_count(old_contents[path]):
            problems.append(f"{path} removes error handling or checks (exit, raise, assert); fixes must not weaken checks")
        if len(content) > MAX_FILE_CHARS:
            problems.append(f"{path} is too large")
        if not content.strip():
            problems.append(f"{path} would be empty")
        try:
            if path.endswith(".py"):
                ast.parse(content)
            elif path.endswith(".json"):
                json.loads(content)
        except (SyntaxError, ValueError) as e:
            problems.append(f"{path} is not valid: {e}")
    return problems


def propose_fix(llm, d: dict, tree_paths: list[str], read_file) -> dict:
    """Ask the model for a fix. `read_file(path)` returns a file's text. Returns the proposal plus `problems`."""
    log = d.get("log_excerpt", "")
    context_paths = pick_context(tree_paths, log)
    files = []
    for p in context_paths:
        try:
            files.append((p, read_file(p)[:MAX_FILE_CHARS]))
        except Exception:
            continue
    listing = "\n".join(sorted(tree_paths)[:150])
    ai = d.get("ai") if d.get("ai") and not d["ai"].get("error") else None
    user = (f"Failing pipeline: {d['pipeline']} (run {d['build_id']}), step: {d['failed_step']}\n"
            f"Rules say: {d['category']}, {d['root_cause']}\n"
            + (f"AI analysis: {ai['likely_cause']}\n" if ai else "")
            + f"\nLog excerpt:\n---\n{redact(log)}\n---\n\nRepository files under {', '.join(config.settings().code_roots)}:\n{listing}\n\n"
            + "\n\n".join(f"=== {p} ===\n{redact(c)}" for p, c in files))
    msg = llm.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                   json_schema=SCHEMA, max_tokens=8000)
    proposal = json.loads(msg.get("content") or "{}")
    shown = dict(files)
    old = {}
    for c in proposal.get("changes", []):
        if c.get("action") == "edit" and c.get("path") in tree_paths:
            try:
                old[c["path"]] = shown.get(c["path"]) or read_file(c["path"])
            except Exception:
                pass
    proposal["problems"] = validate(proposal, set(tree_paths), old)
    proposal["context_files"] = context_paths
    return proposal


def pr_text(d: dict, proposal: dict, run_link: str) -> tuple[str, str]:
    """Title and description for the pull request."""
    title = f"[PipeMedic] Fix {d['pipeline']} (run {d['build_id']}): {proposal['summary']}"[:200]
    body = (f"**AI-generated fix. A person must review it before merging.**\n\n"
            f"Failing run: {run_link}\n\n**Why it failed:** {d['root_cause']}\n\n"
            f"**What this changes:** {proposal['explanation']}\n\n**Risk:** {proposal['risk']}\n\n"
            f"Confidence: {proposal['confidence']}\n\nFiles changed:\n"
            + "\n".join(f"- `{c['path']}` ({c['action']})" for c in proposal["changes"])
            + ("\n\n**Check new files carefully:** their contents were written by an AI. Replace every REPLACE_ME "
               "and confirm any other value before merging." if any(c["action"] == "create" for c in proposal["changes"]) else "")
            + "\n\nThe pipeline is re-run on this branch automatically; its result is posted below. "
              "PipeMedic never merges pull requests.")
    return title, body


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
MAX_PRS_PER_DAY = 3


def open_fix_pr(ado, llm, d: dict, run_link: str, store=None, dry_run: bool = True, now=None) -> dict:
    """Propose a fix for diagnosis `d`. With dry_run False, also open the pull request and re-run the pipeline on it."""
    from datetime import datetime, timedelta, timezone

    if not eligible(d):
        return {"status": "skipped", "detail": "not a failure a code change can fix"}
    now = now or datetime.now(timezone.utc)
    if store is not None:
        fixes = store.fixes()
        waiting = [f for f in fixes if f["pipeline"] == d["pipeline"] and f["status"] == "open"]
        signature = f"{d.get('category')}|{d.get('failed_step')}"
        if any(f.get("signature") == signature for f in waiting):
            return {"status": "skipped", "detail": "a fix for this same failure is already waiting for review"}
        if len(waiting) >= MAX_OPEN_PER_PIPELINE:
            return {"status": "skipped", "detail": f"{len(waiting)} fixes for this pipeline are already waiting for review"}
        # Older records did not note which files they touch: treat those as touching everything, as before.
        busy_files = set()
        for f in waiting:
            if f.get("files") is None:
                return {"status": "skipped", "detail": "an earlier fix for this pipeline is waiting for review and its files are unknown"}
            busy_files |= set(f["files"])
        recent = [f for f in fixes if datetime.fromisoformat(f["time"]) > now - timedelta(days=1)]
        if len(recent) >= MAX_PRS_PER_DAY:
            return {"status": "skipped", "detail": f"already opened {MAX_PRS_PER_DAY} fixes in the last day"}

    proposal = propose_fix(llm, d, ado.repo_paths(), ado.read_file)
    if proposal["problems"]:
        return {"status": "rejected", "detail": "; ".join(proposal["problems"]), "proposal": proposal}
    if proposal["confidence"] == "low":
        return {"status": "rejected", "detail": "the model is not confident enough to open a pull request", "proposal": proposal}

    if store is not None:
        clash = sorted(busy_files & {c["path"] for c in proposal["changes"]})
        if clash:
            return {"status": "skipped", "detail": f"another open fix already changes {', '.join(clash)}", "proposal": proposal}

    title, body = pr_text(d, proposal, run_link)
    if dry_run:
        return {"status": "proposed", "proposal": proposal, "title": title}

    branch = f"pipemedic/fix-run-{d['build_id']}"
    ado.create_branch_with_commit(branch, proposal["changes"], title)
    pr_id = ado.create_pull_request(branch, title, body)
    verify = ado.queue_run(d["pipeline_id"], f"refs/heads/{branch}")
    ado.comment_on_pull_request(pr_id, f"PipeMedic re-ran the pipeline on this branch: run {verify}. The result will be posted here.")
    if store is not None:
        store.record_fix({"build_id": d["build_id"], "pipeline": d["pipeline"], "pr_id": pr_id, "verify_build": verify,
                          "branch": branch, "title": title[:120], "status": "open", "verified": "pending", "time": now.isoformat(),
                          "files": [c["path"] for c in proposal["changes"]], "signature": signature})
    return {"status": "opened", "pr_id": pr_id, "verify_build": verify, "branch": branch, "proposal": proposal, "title": title}


def check_fix_verifications(ado, store) -> list[str]:
    """Post the verification run's result on each open fix, and close the record once the PR is finished."""
    notes = []
    for f in store.fixes():
        if f["status"] != "open":
            continue
        try:
            pr = ado.pull_request(f["pr_id"])
            if pr.get("status") in ("completed", "abandoned"):
                store.update_fix(f["build_id"], status=pr["status"])
                notes.append(f"PR {f['pr_id']} {pr['status']}")
                continue
            if f.get("verified") == "pending":
                b = ado.build(f["verify_build"])
                if b.get("status") == "completed":
                    ok = b.get("result") == "succeeded"
                    ado.comment_on_pull_request(
                        f["pr_id"], f"Verification run {f['verify_build']} **{'succeeded' if ok else 'failed'}**. "
                        + ("The pipeline passes with this change." if ok else "This change did not make the pipeline pass; a person should look."))
                    store.update_fix(f["build_id"], verified="passed" if ok else "failed")
                    notes.append(f"PR {f['pr_id']} verification {'passed' if ok else 'failed'}")
        except Exception as e:
            notes.append(f"PR {f['pr_id']} check failed: {str(e)[:80]}")
    return notes
