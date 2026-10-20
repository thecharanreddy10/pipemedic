"""Ask PipeMedic: a read-only chat over the data the dashboard already loaded.

The model can only call the tools below, which read runs, diagnoses, runner health, pool status and alerts.
There is no tool that changes anything, so a question can never trigger an action.
"""
import json
from datetime import datetime, timezone

SYSTEM = (
    "You are PipeMedic, an assistant for a team that runs Azure DevOps pipelines on self-hosted runners. "
    "Answer the user's question using ONLY the tools; call them before answering anything about runs, failures, "
    "runners, the pool or alerts. Never invent run numbers, names or numbers. If the tools do not contain the answer, "
    "say so plainly. Cite run numbers like 'run 9'. Keep answers short: a direct answer first, then at most 3 bullets. "
    "You are read-only: you cannot retry, change or fix anything, so when action is needed, say what a person should do."
)

TOOLS = [
    {"type": "function", "function": {
        "name": "list_runs", "description": "Recent finished pipeline runs, newest first.",
        "parameters": {"type": "object", "properties": {
            "limit": {"type": "integer", "description": "How many runs, default 10"},
            "result": {"type": "string", "enum": ["succeeded", "failed", "canceled"], "description": "Filter by result"}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "list_failures", "description": "Investigated failed runs with verdict and root cause, newest first.",
        "parameters": {"type": "object", "properties": {
            "verdict": {"type": "string", "enum": ["runner", "code-or-config", "flaky", "mixed", "unknown"]}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "get_failure", "description": "Full detail for one failed run: evidence, suggested fixes, runner health during the run.",
        "parameters": {"type": "object", "properties": {"build_id": {"type": "integer"}}, "required": ["build_id"]}}},
    {"type": "function", "function": {
        "name": "runner_health", "description": "Latest and peak CPU, memory and disk for each runner over a time window.",
        "parameters": {"type": "object", "properties": {
            "hostname": {"type": "string"}, "minutes": {"type": "integer", "description": "Window, default 60"}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "pool_status", "description": "Agent pool capacity, queue, wait times and findings.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "predictive_alerts", "description": "Runners trending towards trouble, with minutes left.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
]


def _failure_summary(d: dict) -> dict:
    return {"run": d["build_id"], "pipeline": d["pipeline"], "step": d["failed_step"], "category": d["category"],
            "verdict": d["verdict"], "root_cause": d["root_cause"], "runner": d.get("hostname") or d.get("agent")}


def run_tool(name: str, args: dict, ctx: dict):
    """Execute one tool against the loaded data. `ctx` keys: runs, diags, pool, alerts, health (list of records)."""
    if name == "list_runs":
        rows = sorted(ctx["runs"], key=lambda r: -r["id"])
        if args.get("result"):
            rows = [r for r in rows if r.get("result") == args["result"]]
        return [{"run": r["id"], "pipeline": r["pipeline"], "result": r.get("result"), "finished": r.get("finish")}
                for r in rows[: int(args.get("limit") or 10)]]
    if name == "list_failures":
        ds = sorted(ctx["diags"], key=lambda d: -d["build_id"])
        if args.get("verdict"):
            ds = [d for d in ds if d["verdict"] == args["verdict"]]
        return [_failure_summary(d) for d in ds]
    if name == "get_failure":
        for d in ctx["diags"]:
            if d["build_id"] == args.get("build_id"):
                return {**_failure_summary(d), "evidence": d["evidence"][:5], "suggestions": d["suggestions"],
                        "runner_health": d["runner_health"], "flaky": d.get("flaky"),
                        "seen_before": (d.get("similar") or {}).get("summary"),
                        "ai_fix": ({k: d["fix"].get(k) for k in ("status", "summary", "files", "pr_url")} if d.get("fix") else None)}
        return {"error": f"run {args.get('build_id')} is not among the investigated failures"}
    if name == "runner_health":
        minutes = int(args.get("minutes") or 60)
        now = datetime.now(timezone.utc).timestamp()
        out = {}
        for r in ctx["health"]:
            if args.get("hostname") and r["hostname"] != args["hostname"]:
                continue
            ts = datetime.fromisoformat(r["timestamp_utc"].replace("Z", "+00:00")).timestamp()
            if now - ts > minutes * 60:
                continue
            o = out.setdefault(r["hostname"], {"samples": 0, "cpu_peak": 0, "memory_peak": 0, "disk_peak": 0, "_t": 0})
            o["samples"] += 1
            o["cpu_peak"] = max(o["cpu_peak"], r.get("cpu_percent") or 0)
            o["memory_peak"] = max(o["memory_peak"], r.get("memory_percent") or 0)
            o["disk_peak"] = max(o["disk_peak"], r.get("disk_percent") or 0)
            if ts >= o["_t"]:
                o.update(_t=ts, latest_cpu=r.get("cpu_percent"), latest_memory=r.get("memory_percent"),
                         latest_disk=r.get("disk_percent"), latest_disk_free_gb=r.get("disk_free_gb"))
        for o in out.values():
            o.pop("_t", None)
        return out or {"error": "no health samples in that window"}
    if name == "pool_status":
        p = ctx["pool"]
        return {k: p[k] for k in ("agents_total", "agents_online", "agents_busy", "agents_idle", "utilization_pct",
                                  "jobs_queued", "oldest_wait_s", "avg_wait_s", "versions")} | {
            "findings": [f["message"] for f in p["findings"]]}
    if name == "predictive_alerts":
        return [{"level": a["level"], "message": a["message"], "suggestion": a["suggestion"]} for a in ctx["alerts"]] or "none"
    return {"error": f"unknown tool {name}"}


def ask(llm, question: str, ctx: dict, history: list[dict] | None = None, max_steps: int = 6) -> dict:
    """Answer a question. Returns {answer, trace} where trace lists each tool call made."""
    messages = [{"role": "system", "content": SYSTEM}] + list(history or []) + [{"role": "user", "content": question}]
    trace = []
    for _ in range(max_steps):
        msg = llm.chat(messages, tools=TOOLS, max_tokens=4000)
        calls = msg.get("tool_calls") or []
        if not calls:
            return {"answer": (msg.get("content") or "").strip() or "I could not produce an answer.", "trace": trace}
        messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": calls})
        for call in calls:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except ValueError:
                args = {}
            result = run_tool(name, args, ctx)
            text = json.dumps(result, default=str)[:6000]
            trace.append({"tool": name, "args": args, "result": text})
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": text})
    return {"answer": "I needed too many steps to answer that. Try a narrower question.", "trace": trace}
