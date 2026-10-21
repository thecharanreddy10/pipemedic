"""Fix plans: what PipeMedic offers to do about a failure, for a person to approve.

build_plans() is a pure function of the diagnosis (plus, when there is one, the AI's proposed change and the runner's busiest
process). Rules decide which kinds of fix are offered; the model only writes the code or pipeline change inside a pull request.
Each plan says in plain words what will happen, what changes, how the result is checked and how to undo it, and expires.

Approval links in emails are signed (HMAC), expire, and only lead to a page with a button: opening the link approves nothing.
"""
import base64
import hashlib
import hmac
from datetime import datetime, timedelta, timezone

PLAN_HOURS = 24
CODE_CATEGORIES = {"test-failure", "script-failure", "static-analysis", "dependency", "code-error", "configuration"}
PR_KINDS = {"code-pr", "pipeline-pr"}
RUNNER_KINDS = {"clean-disk", "restart-docker", "restart-agent", "stop-process", "restart-vm"}


def _plan(d, n, kind, title, steps, check, undo, risk, now, **extra):
    return {"plan_id": f"{d['build_id']}-{n}", "build_id": d["build_id"], "pipeline": d.get("pipeline"), "pipeline_id": d.get("pipeline_id"),
            "source_branch": d.get("source_branch"), "hostname": d.get("hostname"), "kind": kind, "title": title, "steps": steps,
            "check": check, "undo": undo, "risk": risk, "status": "proposed", "created": now.isoformat(),
            "expires": (now + timedelta(hours=PLAN_HOURS)).isoformat(), **extra}


def _branch(d) -> str:
    return (d.get("source_branch") or "").removeprefix("refs/heads/") or "main"


def build_plans(d: dict, proposal: dict | None = None, busy_process: dict | None = None, now: datetime | None = None) -> list[dict]:
    """The fixes to offer for diagnosis d, most useful first."""
    now = now or datetime.now(timezone.utc)
    verdict, category, kind = d.get("verdict"), d.get("category"), d.get("kind")
    flags = (d.get("runner_health") or {}).get("flags", [])
    host = d.get("hostname") or "the runner"
    plans: list[dict] = []
    rerun_steps = [f"Re-run {d.get('pipeline')} on {_branch(d)}."]

    def add(*a, **k):
        plans.append(_plan(d, len(plans) + 1, *a, now=now, **k))

    if verdict in ("code-or-config", "mixed") and category in CODE_CATEGORIES:
        good = proposal and proposal.get("changes") and not proposal.get("problems") and proposal.get("confidence") != "low"
        if good:
            from ai_fix import is_pipeline_file
            files = [c["path"] for c in proposal["changes"]]
            pipeline = any(is_pipeline_file(f) for f in files)
            add("pipeline-pr" if pipeline else "code-pr",
                f"Open a pull request: {proposal.get('summary', 'fix the failure')}",
                [f"Make a branch from {_branch(d)} and commit the change to {', '.join(files)}.",
                 f"Open a pull request into {_branch(d)} for a person to review. It is never merged automatically.",
                 f"Run {d.get('pipeline')} on the new branch to prove the change works, and post the result on the pull request."],
                "The pipeline passes on the fix branch.", "Abandon the pull request; nothing changes until someone merges it.",
                ("Changes a pipeline file. " if pipeline else "") + (proposal.get("risk") or ""),
                changes=[{**c, "old_content": (proposal.get("old_contents") or {}).get(c["path"], "")} for c in proposal["changes"]],
                explanation=proposal.get("explanation", ""), confidence=proposal.get("confidence", ""),
                diagnosis={k: d.get(k) for k in ("category", "verdict", "failed_step", "root_cause", "repository")})
        elif category in CODE_CATEGORIES:
            add("manual", "A person changes the code", [s for s in d.get("suggestions", [])[:3]],
                "The next run passes.", "Nothing to undo.", "No automatic change.",
                reason=(proposal or {}).get("detail") or "; ".join((proposal or {}).get("problems", [])) or "the AI did not propose a safe change")

    if verdict in ("runner", "mixed"):
        if kind == "disk" or "low-disk" in flags or "disk-full (runner log)" in flags:
            add("clean-disk", f"Free disk space on {host}, then re-run",
                [f"Delete old job folders, the pip cache and old temporary files on {host}. Tools the agent installed are kept."] + rerun_steps,
                "Free space goes up and the re-run passes.", "Nothing to undo: only caches and old job output are deleted.",
                "Low: jobs re-download what they need.")
        if kind == "docker" or "docker-stopped (runner log)" in flags:
            add("restart-docker", f"Start Docker on {host}, then re-run",
                [f"Restart the Docker service on {host} and check it answers."] + rerun_steps,
                "Docker answers and the re-run passes.", "Nothing to undo.", "Low: no job can build images while Docker is down anyway.")
        if busy_process:
            p = busy_process
            add("stop-process", f"Stop the process using {'the CPU' if float(p.get('cpu', 0)) >= float(p.get('mem', 0)) else 'the memory'} on {host}, then re-run",
                [f"Stop '{p.get('comm')}' (process {p.get('pid')}, CPU {p.get('cpu')}%, memory {p.get('mem')}%, running {int(p.get('seconds', 0)) // 60} min)"
                 + (f" and the group it runs in ({p.get('unit')})" if str(p.get("unit", "")).endswith(".scope") else "") + "."] + rerun_steps,
                f"CPU and memory on {host} go back to normal and the re-run passes.",
                "Start the process again by hand if it was needed.", "Medium: whatever that process was doing stops.",
                process={k: p.get(k) for k in ("pid", "comm", "user", "cpu", "mem", "unit", "args")})
        if "agent-service-down" in flags or "agent-lost (runner log)" in flags or "agent-service-failed (runner log)" in flags:
            add("restart-agent", f"Restart the Azure DevOps agent on {host}, then re-run",
                [f"Restart the agent service on {host}.", "Wait until the agent shows online in the pool."] + rerun_steps,
                "The agent is online and the re-run passes.", "Nothing to undo.", "Low: a job running on that agent would be stopped.")
        add("restart-vm", f"Restart {host}, then re-run (last resort)",
            [f"Restart the VM {host} through Azure.", "Wait until it reports health and its agent is online again."] + rerun_steps,
            f"{host} reports normal health, its agent is online, and the re-run passes.", "Nothing to undo.",
            "High: the runner is offline for about two minutes and any job on it stops.")

    if category == "permissions":  # a re-run fails the same way; access is for a person to grant, never PipeMedic
        add("manual", "A person grants the access the step needs",
            ["Find the identity and the role named in the error.", "Grant the least role that allows the step, then re-run."],
            "The re-run passes.", "Nothing to undo.", "No automatic change.",
            reason="PipeMedic does not change who has access to what.")
    if verdict == "flaky" or verdict in ("runner", "mixed") or category in ("network", "timeout", "unknown"):
        add("rerun", "Re-run without changing anything", rerun_steps, "The re-run passes.", "Nothing to undo.",
            "Low." if verdict == "flaky" else "Low, but the same failure may happen again.")
    return plans


def is_runnable(plan: dict, now: datetime | None = None) -> str | None:
    """Why a plan cannot be approved now, or None if it can."""
    now = now or datetime.now(timezone.utc)
    if plan.get("status") != "proposed":
        return f"this plan is already {plan.get('status')}"
    if plan.get("kind") == "manual":
        return "this plan is for a person to carry out"
    if datetime.fromisoformat(plan["expires"]) < now:
        return "this plan has expired; make a new one from the latest run"
    return None


# ---------------- signed approval links ----------------
def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def sign_link(secret: str, plan: dict, recipient: str) -> str:
    """A token for the email's approval page: which plan, for whom, until when."""
    payload = f"{plan['build_id']}|{plan['plan_id']}|{plan['expires']}|{recipient}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    return _b64(payload.encode()) + "." + _b64(sig)


def read_link(secret: str, token: str, now: datetime | None = None) -> dict | None:
    """The plan a token points at, or None when it is forged, damaged or expired."""
    now = now or datetime.now(timezone.utc)
    try:
        body, sig = token.split(".", 1)
        payload = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode()
        want = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64(want), sig):
            return None
        build_id, plan_id, expires, recipient = payload.split("|", 3)
        if datetime.fromisoformat(expires) < now:
            return None
        return {"build_id": int(build_id), "plan_id": plan_id, "recipient": recipient}
    except (ValueError, UnicodeDecodeError):
        return None
