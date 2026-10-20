"""Rule-based failure classification and runner-health correlation.

Pure functions with no network access, so they can be tested with fixtures.
"""
import re
from datetime import datetime, timezone

from ai_diagnose import excerpt
from flaky import failed_tests

CPU_HIGH = 90.0
MEM_HIGH = 90.0
DISK_HIGH = 90.0
DISK_FREE_LOW_GB = 2.0


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def summarize_health(records: list[dict], start: str | None, finish: str | None) -> dict:
    """Reduce health records inside the run window (plus 1 minute of margin) to a summary."""
    window = records
    if start and finish:
        lo = _parse_ts(start).timestamp() - 60
        hi = _parse_ts(finish).timestamp() + 60
        window = [r for r in records if lo <= _parse_ts(r["timestamp_utc"]).timestamp() <= hi]

    if not window:
        return {"samples": 0, "flags": ["no-health-data"]}

    def col(name):
        return [r[name] for r in window if isinstance(r.get(name), (int, float))]

    cpu, mem, disk, free = col("cpu_percent"), col("memory_percent"), col("disk_percent"), col("disk_free_gb")
    summary = {
        "samples": len(window),
        "cpu_max": max(cpu) if cpu else None,
        "cpu_avg": round(sum(cpu) / len(cpu), 1) if cpu else None,
        "memory_max": max(mem) if mem else None,
        "disk_max": max(disk) if disk else None,
        "disk_free_gb_min": min(free) if free else None,
        "agent_service_running": all(r.get("agent_service_running", True) for r in window),
        "flags": [],
    }
    if summary["cpu_max"] is not None and summary["cpu_max"] >= CPU_HIGH:
        summary["flags"].append("high-cpu")
    if summary["memory_max"] is not None and summary["memory_max"] >= MEM_HIGH:
        summary["flags"].append("high-memory")
    if (summary["disk_max"] is not None and summary["disk_max"] >= DISK_HIGH) or (
        summary["disk_free_gb_min"] is not None and summary["disk_free_gb_min"] < DISK_FREE_LOW_GB
    ):
        summary["flags"].append("low-disk")
    if not summary["agent_service_running"]:
        summary["flags"].append("agent-service-down")
    return summary


def _lines_matching(text: str, pattern: str, limit: int = 4) -> list[str]:
    out = []
    for line in text.splitlines():
        # strip the leading ISO timestamp Azure DevOps adds to each log line
        clean = re.sub(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z\s*", "", line).strip()
        if re.search(pattern, clean, re.IGNORECASE):
            out.append(clean)
            if len(out) >= limit:
                break
    return out

_TS_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z\s*")
_GENERIC_EXIT = re.compile(r"exited with code|exit code \d+|Process completed with exit code", re.IGNORECASE)
_NOISE = re.compile(r"^(Starting|Finishing|Generating script|=+|\[command\]|Task\s+:|Description\s+:|Version\s+:|Author\s+:|Help\s+:)", re.IGNORECASE)


def _clean(line: str) -> str:
    return _TS_PREFIX.sub("", line).strip()


def _informative_lines(text: str) -> list[str]:
    """Error lines that say what went wrong. The generic "exited with code N" line does not count."""
    out = []
    for raw in text.splitlines():
        line = _clean(raw)
        if not line:
            continue
        if "##[error]" in line:
            detail = line.split("##[error]", 1)[1].strip()
            if detail and not _GENERIC_EXIT.search(detail):
                out.append(line)
            continue
        if re.match(r"(\[?ERROR\]?|error)\b\s*[:\]]", line, re.IGNORECASE) or re.search(
                r"\b(failed|not found|missing|No such file|cannot|unable to)\b", line, re.IGNORECASE):
            out.append(line)
    return out


def _tail_lines(text: str, n: int) -> list[str]:
    """The last few meaningful lines before the failure: where a program usually says why it stopped."""
    lines = [_clean(raw) for raw in text.splitlines()]
    keep = [ln for ln in lines if ln and not _GENERIC_EXIT.search(ln) and not ln.startswith("##[") and not _NOISE.match(ln)]
    return keep[-n:]


def classify(failure: dict) -> dict:
    """Return {category, summary, evidence} from the failed step, issues and log text."""
    log = failure.get("log_text", "")
    issues = " ".join(failure.get("issues", []))
    blob = f"{issues}\n{log}"
    step = failure.get("failed_step", "unknown step")

    if re.search(r"ran longer than the maximum time|exceeded the maximum|timed out", blob, re.IGNORECASE):
        waiting = _lines_matching(log, r"waiting for|retry|connecting", 2)
        return {
            "category": "timeout",
            "summary": f"'{step}' did not finish before the job timeout and was cancelled.",
            "evidence": ([issues.strip()] if issues.strip() else []) + waiting,
        }

    if re.search(
        r"ensurepip is not available|python3[\.\d]*-venv|command not found|exited with code '127'"
        r"|No space left on device|externally-managed-environment",
        blob,
        re.IGNORECASE,
    ):
        return {
            "category": "runner-environment",
            "summary": f"'{step}' failed because the runner is missing a tool or resource the job needs.",
            "evidence": _lines_matching(
                log,
                r"ensurepip|venv|command not found|No space left|externally-managed",
                4,
            ) or [issues.strip()],
        }

    if re.search(r"Connection refused|Could not resolve host|Name or service not known|\bECONNREFUSED\b|\bENOTFOUND\b|Network is unreachable|SSL: CERTIFICATE",
                 blob, re.IGNORECASE):
        return {
            "category": "network",
            "summary": f"'{step}' could not reach a network service it depends on.",
            "evidence": _lines_matching(log, r"Connection refused|resolve host|Name or service|\bECONNREFUSED\b|\bENOTFOUND\b|unreachable|CERTIFICATE", 4),
        }

    if re.search(r"Permission denied|AuthorizationFailed|not authorized|access denied|Forbidden|\b403\b|\b401\b|Unauthorized",
                 blob, re.IGNORECASE):
        return {
            "category": "permissions",
            "summary": f"'{step}' was refused access: a credential, role or file permission is missing.",
            "evidence": _lines_matching(log, r"Permission denied|AuthorizationFailed|not authorized|access denied|Forbidden|403|401|Unauthorized", 4),
        }

    if re.search(r"No matching distribution|Could not find a version|ModuleNotFoundError|ImportError|ResolutionImpossible", blob, re.IGNORECASE):
        return {
            "category": "dependency",
            "summary": f"'{step}' failed while resolving or importing a dependency.",
            "evidence": _lines_matching(log, r"No matching distribution|Could not find|ModuleNotFound|ImportError|ResolutionImpossible", 4),
        }

    if re.search(r"^FAILED |AssertionError|\bassert\b.*==|\d+ failed", blob, re.IGNORECASE | re.MULTILINE):
        return {
            "category": "test-failure",
            "summary": f"'{step}' ran and at least one test failed.",
            "evidence": _lines_matching(log, r"^FAILED |AssertionError|assert .* == |\d+ failed", 4),
        }

    informative = _informative_lines(log + "\n" + issues)
    if informative:
        return {
            "category": "script-failure",
            "summary": f"'{step}' exited with an error from the script itself.",
            "evidence": informative[:4],
        }

    # Only the generic "exited with code N" line (or nothing) is left: that says a step failed, not why.
    return {
        "category": "unknown",
        "step": step,
        "summary": f"'{step}' failed for a reason the rules do not recognise.",
        "evidence": _lines_matching(log, r"fatal|reject|denied|invalid|refused|abort|cannot|unable|unexpected", 4) or _tail_lines(log, 3),
    }


def correlate(diagnosis: dict, health: dict, hostname: str | None) -> dict:
    """Combine the classification with runner health to decide where the fault lies."""
    flags = health.get("flags", [])
    category = diagnosis["category"]
    host = hostname or "the runner"
    unhealthy = [f for f in flags if f not in ("no-health-data",)]
    no_data = "no-health-data" in flags
    runner_state = "had no health data for this run, so its state is unknown" if no_data else "was healthy"

    if category == "runner-environment":
        verdict, root = "runner", f"{host} is missing something the job needs. This is an environment problem, not a code change."
        fixes = [
            "Install the missing package on the runner (for example: sudo apt-get install -y python3-venv python3-pip).",
            "Add a runner prerequisite check step, or bake the tools into the VM image so new runners are ready.",
        ]
    elif category == "timeout":
        if unhealthy:
            verdict = "runner"
            root = f"The step hung while {host} was under stress ({', '.join(unhealthy)}), so resource starvation likely caused or worsened the timeout."
            fixes = [
                "Free resources on the runner (clean the work folder, stop competing jobs) or scale the pool.",
                "Re-run once health is normal. Add a step-level timeout so the job fails with a clear message sooner.",
            ]
        else:
            verdict = "code-or-config"
            root = (f"{host} {runner_state}, so the most likely cause is the step itself waiting on something "
                    "that never responds (an external service or an endless loop).")
            fixes = [
                "Check the external service the step waits on, and add a connect timeout and a retry limit to the script.",
                "Lower timeoutInMinutes for this step so a hang fails fast.",
            ]
    elif category in ("test-failure", "script-failure", "dependency", "permissions", "network"):
        if unhealthy:
            verdict = "mixed"
            root = f"The {category.replace('-', ' ')} is real, but {host} also showed {', '.join(unhealthy)}. Fix the failure, and check the runner too."
        else:
            verdict = "code-or-config"
            if no_data:
                root = (f"The error is in the {category.replace('-', ' ')} itself. {host} {runner_state}, "
                        "but nothing in the log points at the runner.")
            else:
                root = (f"{host} was healthy during the run ({_health_phrase(health)}), so the failure comes "
                        "from the code or configuration, not the runner.")
        fixes = {
            "permissions": [
                "Check which identity the job runs as and which role it holds on the target resource (the log names it).",
                "Grant the least role that allows the action, then re-run. Do not widen access beyond what the step needs.",
            ],
            "network": [
                "Check that the target address resolves and is reachable from the runner (DNS, firewall, proxy, certificates).",
                "If the service is flaky, add retries with a short backoff to the step.",
            ],
            "test-failure": [
                "Open the failing test, compare the expected and actual values, and fix the code under test (or the test if the expectation is wrong).",
                "Assign the fix to the author of the most recent change to that module.",
            ],
            "script-failure": [
                "Read the error line from the script and supply what it reports missing (config file, variable or permission).",
                "Add a clear early check to the script so the error message names the exact fix.",
            ],
            "dependency": [
                "Pin the dependency to a known good version and verify the package index is reachable from the runner.",
            ],
        }[category]
    else:
        verdict = "unknown"
        root, fixes = unknown_guidance(diagnosis.get("evidence", []), flags, step_name=diagnosis.get("step"))

    return {"verdict": verdict, "root_cause": root, "suggestions": fixes}


def _health_phrase(health: dict) -> str:
    if not health.get("samples"):
        return "no health samples"
    return f"CPU max {health.get('cpu_max')}%, memory max {health.get('memory_max')}%, disk {health.get('disk_max')}% used"


def diagnose(failure: dict, health_records: list[dict]) -> dict:
    """Full diagnosis for one failed run."""
    hostname = failure.get("hostname")
    health = summarize_health(health_records, failure.get("start"), failure.get("finish"))
    base = classify(failure)
    result = correlate(base, health, hostname)

    flaky = failure.get("flaky") or {}
    flaky_tests = []
    if base["category"] == "test-failure" and flaky:
        flaky_tests = failed_tests(failure.get("log_text", ""))
        names = ", ".join(flaky_tests) or "A test"
        if flaky.get("flaky"):
            result = {
                "verdict": "flaky",
                "root_cause": (f"{names} failed here, but this pipeline passed on the same commit {flaky.get('commit', '')} "
                               f"({flaky['passes']} pass, {flaky['fails']} fail). The code did not change between those runs, "
                               "so the test is intermittent, not a regression."),
                "suggestions": ["Re-run once; if it passes, the test is flaky.",
                                "Quarantine the test or fix its timing, state or ordering dependency.",
                                "Track it: a test that flips on one commit will keep failing builds at random."],
            }
        elif flaky.get("fails", 0) >= 2:
            result["root_cause"] += (f" It failed in all {flaky['fails']} runs on this commit, so it is consistent "
                                     "and not flaky.")
    return {
        "log_excerpt": excerpt(failure.get("log_text", "")),
        "finish": failure.get("finish"),
        "flaky": flaky or None,
        "flaky_tests": flaky_tests,
        "pipeline": failure.get("pipeline"),
        "build_id": failure.get("build_id"),
        "agent": failure.get("agent_name"),
        "hostname": hostname,
        "pipeline_id": failure.get("pipeline_id"),
        "source_branch": failure.get("source_branch"),
        "requested_for": failure.get("requested_for"),
        "failed_step": failure.get("failed_step"),
        "category": base["category"],
        "summary": base["summary"],
        "evidence": base["evidence"],
        "runner_health": health,
        **result,
    }


def unknown_guidance(evidence: list[str], flags: list[str], step_name: str | None = None) -> tuple[str, list[str]]:
    """What to tell a person when the cause is not recognised: the best clue, and concrete next steps. No fix is claimed."""
    clue = next((e.strip() for e in evidence if e.strip()), "")
    where = f" in '{step_name}'" if step_name else ""
    if clue:
        root = (f"The cause{where} could not be identified automatically. The first error line in the log is: \"{clue[:240]}\". "
                "Treat this as a lead to investigate, not a confirmed diagnosis.")
    else:
        root = (f"The cause{where} could not be identified automatically, and the log has no obvious error line. "
                "A person needs to read the full log.")
    steps = [
        "Read the full log of the failing step, especially the lines just above the first error.",
    ]
    if clue:
        steps.append("Search the repository and the runner setup for the names in that error line (a file, command, host or role).")
    unhealthy = [f for f in flags if f != "no-health-data"]
    if unhealthy:
        steps.append(f"The runner showed {', '.join(unhealthy)} during the run; check the runner first.")
    steps += [
        "Compare with the last passing run of this pipeline: what changed in the code, the pipeline definition or the runner?",
        "If it passes on a re-run with no change, record it as intermittent and look for timing or shared state.",
        "Make the failing step print a clear message that names the missing thing, so the next failure can be explained automatically.",
    ]
    return root, steps


KNOWN_CATEGORIES = {"timeout", "runner-environment", "dependency", "test-failure", "script-failure", "network",
                    "permissions", "configuration", "unknown"}
AI_CATEGORY_ALIASES = {"configuration": "script-failure"}  # a missing or wrong config is handled like a script problem


def apply_ai(d: dict, ai: dict | None) -> dict:
    """Combine the rules' result with the AI's reading. The rules keep the category and the verdict.

    - Rules recognised the failure: they stand. The AI either confirms it or its disagreement is recorded.
    - Rules could not identify the cause: the category stays "unknown" (a person is needed), and the AI's own
      words and suggestions are added, labelled unverified. Forcing its answer into a fixed category list would
      attach canned advice that does not fit, so its reading is shown as it wrote it.
    The verdict about the runner always comes from runner health, never from the AI.
    """
    if not ai or ai.get("error"):
        d["classified_by"] = "rules"
        return d
    cat = AI_CATEGORY_ALIASES.get(ai.get("category", ""), ai.get("category", ""))
    if d.get("category") != "unknown":
        d["classified_by"] = "rules, confirmed by AI" if cat == d.get("category") else f"rules (the AI suggested {cat or 'something else'})"
        return d

    cause = (ai.get("likely_cause") or "").strip()
    if not cause:
        d["classified_by"] = "rules"
        return d
    if ai.get("confidence") == "low":
        d["suggestions"] = [f"Unverified lead from the AI (low confidence): {cause}"] + d.get("suggestions", [])
        d["classified_by"] = "rules (unknown); a low-confidence AI lead was added"
        return d
    d["root_cause"] = (f"{d.get('root_cause', '')} AI reading ({ai.get('confidence')} confidence, not confirmed by the rules): {cause}").strip()
    fixes = [f"AI suggestion (unverified): {f}" for f in (ai.get("suggested_fixes") or [])[:3]]
    d["suggestions"] = fixes + d.get("suggestions", [])
    d["classified_by"] = "rules (unknown); the AI's reading was added"
    return d
