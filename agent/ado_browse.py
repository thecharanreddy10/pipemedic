"""Turns Azure DevOps run and timeline data into what the Pipelines page shows.

Pure functions with no network access, so they can be tested with fixtures.
"""
from datetime import datetime, timezone

# result (or status, for a run that is not finished) -> (colour key, label)
STATUS = {
    "succeeded": ("good", "Succeeded"),
    "failed": ("critical", "Failed"),
    "canceled": ("warning", "Canceled"),
    "partiallySucceeded": ("warning", "Partly succeeded"),
    "succeededWithIssues": ("warning", "Succeeded with issues"),
    "skipped": ("muted", "Skipped"),
    "abandoned": ("muted", "Abandoned"),
    "inProgress": ("info", "Running"),
    "notStarted": ("info", "Queued"),
    "cancelling": ("warning", "Canceling"),
    "pending": ("info", "Waiting"),
}


def _ts(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc) if value else None


def seconds_between(start, finish, now=None):
    """Length of a run or step. A run that is still going counts up to now."""
    a, b = _ts(start), _ts(finish) or (now or datetime.now(timezone.utc) if start else None)
    return max(0.0, (b - a).total_seconds()) if a and b else None


def status_of(item: dict) -> tuple[str, str]:
    """(colour key, label) for a run or a timeline record: its result once finished, otherwise its state."""
    key = item.get("result") or item.get("status") or item.get("state") or "notStarted"
    return STATUS.get(key, ("muted", str(key)))


def branch_name(ref: str | None) -> str:
    return (ref or "").removeprefix("refs/heads/").removeprefix("refs/pull/") or "-"


def pipeline_rows(definitions: list[dict], builds: list[dict], recent: int = 10) -> list[dict]:
    """One row per pipeline: its latest run and its pass rate over the last `recent` finished runs. Newest activity first."""
    by_def: dict[int, list[dict]] = {}
    for b in builds:
        by_def.setdefault(b["definition"]["id"], []).append(b)
    rows = []
    for d in definitions:
        runs = sorted(by_def.get(d["id"], []), key=lambda b: b.get("queueTime") or "", reverse=True)
        finished = [b for b in runs if b.get("status") == "completed"][:recent]
        passed = sum(1 for b in finished if b.get("result") == "succeeded")
        rows.append({
            "id": d["id"], "name": d["name"], "runs": len(runs), "last": runs[0] if runs else None,
            "pass_rate": round(100 * passed / len(finished)) if finished else None, "finished": len(finished),
        })
    return sorted(rows, key=lambda r: (r["last"] is None, "" if r["last"] is None else _neg(r["last"].get("queueTime"))))


def _neg(text):
    """Sort key that puts later timestamps first when used in an ascending sort."""
    return "".join(chr(0x10FFFF - ord(c)) for c in (text or ""))


def job_rows(timeline: list[dict], hosts: dict, now=None) -> list[dict]:
    """The jobs of a run, in order, with the agent and the machine that ran each."""
    jobs = [r for r in timeline if r.get("type") == "Job"]
    jobs.sort(key=lambda r: (r.get("startTime") or "9", r.get("order") or 0))
    out = []
    for j in jobs:
        agent = j.get("workerName")
        colour, label = status_of(j)
        out.append({"name": j["name"], "agent": agent, "host": hosts.get(agent) if agent else None,
                    "colour": colour, "status": label, "start": j.get("startTime"), "finish": j.get("finishTime"),
                    "seconds": seconds_between(j.get("startTime"), j.get("finishTime"), now), "id": j.get("id")})
    return out


def step_rows(timeline: list[dict], now=None) -> list[dict]:
    """The steps (tasks) of a run in execution order, with the job each belongs to."""
    job_name = {r["id"]: r["name"] for r in timeline if r.get("type") == "Job"}
    tasks = [r for r in timeline if r.get("type") == "Task" and r.get("startTime")]
    tasks.sort(key=lambda r: r["startTime"])
    return [{"job": job_name.get(t.get("parentId"), ""), "name": t["name"], "status": status_of(t)[1], "colour": status_of(t)[0],
             "seconds": seconds_between(t.get("startTime"), t.get("finishTime"), now)} for t in tasks]


def agents_involved(jobs: list[dict]) -> list[dict]:
    """Each distinct agent that ran a job in the run, with the jobs it ran and whether any failed."""
    seen: dict[str, dict] = {}
    for j in jobs:
        if not j["agent"]:
            continue
        a = seen.setdefault(j["agent"], {"agent": j["agent"], "host": j["host"], "jobs": [], "failed": False,
                                         "start": j["start"], "finish": j["finish"]})
        a["jobs"].append(j["name"])
        a["failed"] = a["failed"] or j["colour"] == "critical"
        a["start"] = min(filter(None, [a["start"], j["start"]]), default=None)
        a["finish"] = max(filter(None, [a["finish"], j["finish"]]), default=None)
    return list(seen.values())


def duration_label(seconds) -> str:
    if seconds is None:
        return "-"
    s = int(round(seconds))
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"
