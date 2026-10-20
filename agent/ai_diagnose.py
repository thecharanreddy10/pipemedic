"""AI second opinion on a failed run.

The rules decide the verdict and the actions. The model reads the same log and health summary and writes its own
analysis. Its evidence quotes are checked against the real log, and it never triggers anything.
"""
import json
import re

CATEGORIES = ["timeout", "runner-environment", "dependency", "test-failure", "script-failure",
              "configuration", "permissions", "network", "code-error", "static-analysis", "unknown"]

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["category", "likely_cause", "confidence", "evidence", "suggested_fixes", "runner_related", "runner_effect"],
    "properties": {
        "category": {"type": "string", "enum": CATEGORIES},
        "likely_cause": {"type": "string"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "suggested_fixes": {"type": "array", "items": {"type": "string"}},
        "runner_related": {"type": "boolean"},
        "runner_effect": {"type": "string"},
    },
}

SYSTEM = (
    "You are a CI/CD reliability engineer investigating one failed pipeline run on a self-hosted runner. "
    "Use only the facts in the logs and the runner health summary. Do not guess at things that are not shown. "
    "In `evidence`, copy up to 4 EXACT lines from the logs that support your conclusion, character for character. "
    "Set runner_related to true only if the runner's health (CPU, memory, disk, service state) or its own logs plausibly caused "
    "or worsened the failure, and put a one or two sentence explanation in runner_effect: say whether the runner health numbers "
    "and the runner's own logs (when given) affected the failure, citing what they show, or say plainly that they show no problem. "
    "A kernel line 'Memory cgroup out of memory' means one process went over a memory limit set for it (a systemd, container or "
    "Kubernetes limit); it does not mean the machine ran out of memory. If the machine's memory numbers are low, say the cause is "
    "that limit or the step's own memory use, set runner_related to false, and do not call it a runner problem. "
    "Use confidence 'low' when the logs do not make the cause clear. Suggest concrete, short fixes."
)

_PATTERNS = [
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{20,}"), r"\1[REDACTED]"),
    (re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|authorization)\b(\s*[=:]\s*)\S+"), r"\1\2[REDACTED]"),
    (re.compile(r"\b(?=[A-Za-z0-9+=_\-]*\d)(?=[A-Za-z0-9+=_\-]*[A-Za-z])[A-Za-z0-9+=_\-]{40,}\b"), "[REDACTED]"),
]
_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z\s*")


def redact(text: str) -> str:
    """Remove anything that looks like a credential before the log leaves the machine."""
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


def excerpt(log: str, max_lines: int = 120, max_chars: int = 7000) -> str:
    lines = [_TS.sub("", ln).rstrip() for ln in (log or "").splitlines()]
    out = "\n".join(lines[-max_lines:])
    return out[-max_chars:]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", _TS.sub("", s)).strip().lower()


def verify_quotes(quotes: list[str], log: str) -> tuple[list[str], list[str]]:
    """Split the model's quotes into those that really appear in the log and those that do not."""
    hay = _norm(re.sub(r"\n", " ", log or ""))
    ok, bad = [], []
    for q in quotes:
        (ok if q.strip() and _norm(q) in hay else bad).append(q)
    return ok, bad


def _rules_category(d: dict) -> str:
    return "test-failure" if d.get("verdict") == "flaky" else d.get("category", "unknown")


# Different names for the same kind of cause should not count as a disagreement.
_SAME_FAMILY = [{"script-failure", "configuration"}, {"dependency", "network"}]


def categories_agree(rules: str, ai: str) -> bool:
    return rules == ai or any(rules in g and ai in g for g in _SAME_FAMILY)


def ai_diagnose(llm, d: dict) -> dict:
    """Second opinion for one diagnosis `d` (needs d['log_excerpt'])."""
    log = d.get("log_excerpt", "")
    h = d.get("runner_health", {})
    health = ("no runner health samples for this run" if not h.get("samples") else
              f"{h['samples']} samples; CPU peak {h.get('cpu_max')}%, memory peak {h.get('memory_max')}%, "
              f"disk used {h.get('disk_max')}%, free {h.get('disk_free_gb_min')} GB; flags: {', '.join(h.get('flags', [])) or 'none'}")
    user = (f"Pipeline: {d['pipeline']} (run {d['build_id']})\nFailed step: {d['failed_step']}\n"
            f"Runner: {d.get('hostname') or d.get('agent') or 'unknown'}\nRunner health during the run: {health}\n\n"
            f"Log excerpt (last lines of the failing step):\n---\n{redact(log)}\n---")
    other = d.get("other_logs") or ""
    rl = d.get("runner_logs") or {}
    runner_text = rl.get("text") if rl.get("available") else ""
    if other:
        user += f"\n\nOther failed steps in the same run:\n---\n{redact(other)}\n---"
    user += (f"\n\nThe runner's own logs around the failure ({rl.get('window', '')}):\n---\n{redact(runner_text)}\n---" if runner_text
             else "\n\nThe runner's own logs were not available for this run.")
    msg = llm.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                   json_schema=SCHEMA, max_tokens=4000)
    try:
        out = json.loads(msg.get("content") or "")
    except ValueError as e:
        raise RuntimeError("The model did not return valid JSON.") from e

    ok, bad = verify_quotes(out["evidence"], "\n".join(x for x in (log, other, runner_text) if x))
    confidence = out["confidence"]
    if not ok:  # nothing it quoted can be found in the log: do not present it as confident
        confidence = "low"
    return {
        "model": getattr(llm, "label", "model"),
        "category": out["category"],
        "likely_cause": out["likely_cause"],
        "confidence": confidence,
        "evidence_verified": ok,
        "evidence_unverified": bad,
        "suggested_fixes": out["suggested_fixes"],
        "runner_related": out["runner_related"],
        "runner_effect": out.get("runner_effect", ""),
        "agrees_with_rules": categories_agree(_rules_category(d), out["category"]),
    }
