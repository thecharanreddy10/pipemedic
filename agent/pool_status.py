"""Agent pool health: capacity, queue wait, versions, and plain-language findings.

analyze_pool() is a pure function over the Azure DevOps agent and job-request lists.
"""
from datetime import datetime, timezone

WAIT_WARN_S = 30
WAIT_CRIT_S = 300


def _ts(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc) if value else None


def _ver(v: str):
    try:
        return tuple(int(x) for x in v.split("."))
    except (ValueError, AttributeError):
        return ()


def analyze_pool(agents: list[dict], requests: list[dict], now: datetime | None = None,
                 latest_version: str | None = None) -> dict:
    now = now or datetime.now(timezone.utc)

    online = [a for a in agents if a.get("status") == "online" and a.get("enabled", True)]
    offline = [a for a in agents if a not in online]
    busy = [a for a in online if a.get("assignedRequest")]
    idle = [a for a in online if not a.get("assignedRequest")]

    queued, waits = [], []
    for r in requests:
        q, assigned, finished = _ts(r.get("queueTime")), _ts(r.get("assignTime")), _ts(r.get("finishTime"))
        if q is None:
            continue
        if assigned is None and finished is None:
            queued.append({"pipeline": (r.get("definition") or {}).get("name", "?"), "waiting_s": int((now - q).total_seconds())})
        elif assigned is not None:
            waits.append((assigned - q).total_seconds())

    oldest = max((j["waiting_s"] for j in queued), default=0)
    stats = {
        "agents_total": len(agents), "agents_online": len(online), "agents_offline": len(offline),
        "agents_busy": len(busy), "agents_idle": len(idle),
        "utilization_pct": round(100 * len(busy) / len(online)) if online else 0,
        "jobs_queued": len(queued), "oldest_wait_s": oldest,
        "avg_wait_s": round(sum(waits) / len(waits), 1) if waits else None,
        "max_wait_s": round(max(waits), 1) if waits else None,
        "versions": sorted({a.get("version") for a in agents if a.get("version")}),
        "queued_jobs": queued,
    }

    findings = []

    def add(level, code, message, suggestion, agent=None):
        findings.append({"level": level, "code": code, "message": message, "suggestion": suggestion, "agent": agent})

    if agents and not online:
        add("critical", "no-agents-online", "No agent in the pool is online, so no job can run.",
            "Start the agent service on the runner VMs and check the VMs are running.")
    for a in offline:
        why = "disabled" if a.get("status") == "online" else "offline"
        add("warning", "agent-offline", f"{a['name']} is {why}.",
            "Check the VM is running and the agent service is active (sudo ./svc.sh status).", agent=a["name"])
    if queued and idle and oldest >= WAIT_WARN_S:
        add("warning", "queue-with-idle-agents",
            f"{len(queued)} job(s) have waited up to {oldest}s while {len(idle)} agent(s) sit idle ({', '.join(a['name'] for a in idle)}).",
            "Usually the organization's parallel-job limit (a new org gets 1 free self-hosted job) or demands the idle agent doesn't meet. "
            "Check Organization settings > Parallel jobs and the pipeline's demands.")
    elif queued and not idle and online:
        add("warning", "capacity-saturated", f"{len(queued)} job(s) are queued and every online agent is busy.",
            "Add agents to the pool or reduce concurrent pipelines.")
    if oldest >= WAIT_CRIT_S:
        add("critical", "long-queue-wait", f"The oldest queued job has waited {oldest // 60} minutes.",
            "Investigate now: a stuck job or an unavailable agent is blocking the queue.")
    versions = {a["version"] for a in agents if a.get("version")}
    if len(versions) > 1:
        add("info", "version-skew", f"Agents run different versions: {', '.join(sorted(versions))}.",
            "Update all agents to the same version.")
    if latest_version and versions and any(_ver(v) < _ver(latest_version) for v in versions):
        add("info", "update-available", f"A newer agent version ({latest_version}) is available.",
            "Agents update themselves between jobs by default; check the pool's auto-update setting if they stay behind.")

    stats["findings"] = findings
    stats["healthy"] = not any(f["level"] in ("warning", "critical") for f in findings)
    return stats


def render_pool(s: dict) -> str:
    lines = [
        "=== Agent pool ===",
        f"Agents : {s['agents_online']} online / {s['agents_total']} total "
        f"({s['agents_busy']} busy, {s['agents_idle']} idle, utilization {s['utilization_pct']}%)",
        f"Queue  : {s['jobs_queued']} waiting, oldest {s['oldest_wait_s']}s; "
        f"wait over recent jobs: avg {s['avg_wait_s']}s, max {s['max_wait_s']}s",
        f"Version: {', '.join(s['versions']) or 'unknown'}",
        "Status : " + ("healthy" if s["healthy"] else "needs attention"),
    ]
    for f in s["findings"]:
        lines.append(f"  [{f['level']}] {f['message']}")
        lines.append(f"      -> {f['suggestion']}")
    return "\n".join(lines)
