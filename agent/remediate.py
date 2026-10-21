"""Guardrailed remediation for PipeMedic.

plan_actions() is a pure function: it turns a diagnosis into a list of actions and says which
may run automatically. Only actions on the allowlist run on their own, and only when:
  - runner health data exists for the run (no acting on a guess),
  - the same action has not already succeeded for this run,
  - a run is retried at most once, and a retry is never retried,
  - no more than MAX_AUTO_PER_HOUR automatic actions have run in the last hour.
Everything else is reported as "needs approval". Execution is dry-run unless --act is given.
"""
import json
import os
import time

import config
from datetime import datetime, timedelta, timezone

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "remediation_log.jsonl")

AUTO_ACTIONS = {"clean-workdir", "retry-run", "create-work-item"}  # the allowlist
MAX_AUTO_RETRIES = 1
MAX_AUTO_PER_HOUR = 5
# Recognised causes a person fixes in the code or the pipeline's configuration: each gets a work item for whoever triggered the run.
WORKITEM_CATEGORIES = {"test-failure", "script-failure", "dependency", "timeout", "code-error",
                       "network", "permissions", "static-analysis", "configuration"}


_STORE = None  # set to a TableStore when running in the cloud; None means the local file


def configure_store(store) -> None:
    global _STORE
    _STORE = store


def load_history(path: str = LOG_PATH) -> list[dict]:
    if _STORE is not None:
        return _STORE.history()
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def log_entry(entry: dict, path: str = LOG_PATH) -> None:
    if _STORE is not None:
        _STORE.append_history(entry)
        return
    entry = {"time": datetime.now(timezone.utc).isoformat(), **entry}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _act(action, reason, auto=True, **params):
    return {"action": action, "auto": auto, "reason": reason, "params": params}


def plan_actions(d: dict, current_health: dict, history: list[dict], now: datetime | None = None) -> list[dict]:
    """Return the actions for one diagnosis. current_health is a summarize_health() result for 'now'."""
    now = now or datetime.now(timezone.utc)
    build_id = d["build_id"]
    flags = d["runner_health"].get("flags", [])
    verdict, category = d["verdict"], d["category"]
    host = d.get("hostname") or "the runner"

    done = [h for h in history if h.get("status") == "done"]
    already = {h["action"] for h in done if h.get("build_id") == build_id}
    is_retry = any(h["action"] == "retry-run" and h.get("new_build_id") == build_id for h in done)
    retries = sum(1 for h in done if h["action"] == "retry-run" and h.get("build_id") == build_id)
    recent = [h for h in done if h["action"] in AUTO_ACTIONS
              and datetime.fromisoformat(h["time"]) > now - timedelta(hours=1)]
    budget_left = len(recent) < MAX_AUTO_PER_HOUR
    have_health = "no-health-data" not in flags

    plan = []

    def retry_allowed():
        return retries < MAX_AUTO_RETRIES and not is_retry

    kind = d.get("kind")
    if verdict == "runner" and category == "runner-environment" and kind == "memory":
        plan.append(_act("raise-memory-limit", f"a step was killed for lack of memory on {host}; a person should lower its memory use, "
                         "raise its limit, or add memory (changing limits is outside the allowlist).", auto=False))
    elif verdict == "runner" and category == "runner-environment" and kind != "disk":
        plan.append(_act("install-prerequisite", f"{host} is missing a tool; a human must install it "
                         "(changing the runner image is outside the allowlist).", auto=False))
    elif verdict == "runner" and ("low-disk" in flags or kind == "disk"):
        if "clean-workdir" not in already:
            plan.append(_act("clean-workdir", f"{host} is low on disk; clear old job folders and the pip cache."))
        if retry_allowed():
            plan.append(_act("retry-run", "re-run once after the disk is cleaned."))
    elif verdict == "runner":
        recovered = current_health.get("samples", 0) > 0 and not current_health.get("flags")
        if recovered and retry_allowed():
            plan.append(_act("retry-run", f"{host} was stressed ({', '.join(flags)}) but has recovered; re-run once."))
        elif is_retry or not retry_allowed():
            plan.append(_act("escalate", "the retry already happened; a human should look at the runner.", auto=False))
        else:
            plan.append(_act("defer-retry", f"{host} is still unhealthy ({', '.join(current_health.get('flags', [])) or 'no data'}); "
                             "retry once it recovers.", auto=False))
    elif verdict == "flaky":
        if retry_allowed():
            plan.append(_act("retry-run", "the failing test is intermittent (it passed on the same commit); re-run once.",
                             health_independent=True))
        elif "create-work-item" not in already:
            plan.append(_act("create-work-item", "the test failed again after a retry; ask its owner to fix or quarantine it."))
        plan.append(_act("quarantine-test", "excluding a test from the suite changes the test policy; a person decides.",
                         auto=False))
    elif verdict in ("code-or-config", "mixed") and category in WORKITEM_CATEGORIES:
        if "create-work-item" not in already:
            plan.append(_act("create-work-item", "a code or config change is needed; assign it to the person who triggered the run."))
        if verdict == "mixed":
            plan.append(_act("check-runner", f"{host} also showed {', '.join(flags)}; inspect it.", auto=False))
    elif category == "unknown":
        plan.append(_act("escalate", "the cause is not recognised; a human should investigate.", auto=False))
    else:
        plan.append(_act("escalate", f"no automatic action fits a {category.replace('-', ' ')} judged '{verdict}'; a person decides.", auto=False))

    for a in plan:
        if not a["auto"]:
            continue
        if a["action"] not in AUTO_ACTIONS:
            a["auto"], a["reason"] = False, a["reason"] + " (not on the allowlist)"
        elif a["action"] in ("clean-workdir", "retry-run") and not have_health and not a["params"].get("health_independent"):
            a["auto"], a["reason"] = False, a["reason"] + " (needs approval: no runner health data to confirm)"
        elif not budget_left:
            a["auto"], a["reason"] = False, a["reason"] + f" (needs approval: {MAX_AUTO_PER_HOUR} automatic actions already run this hour)"
    return plan


# ---------- executors ----------

CLEAN_SCRIPT = (
    "find /home/azureuser/azagent/_work -mindepth 1 -maxdepth 1 -type d ! -name _tool ! -name _temp "
    "! -name _tasks -exec rm -rf {} + ; rm -rf /home/azureuser/.cache/pip ; df -h / | tail -1"
)


def arm_resource_exists(subscription: str, name: str, rtype: str, credential, timeout_s: int = 30) -> bool:
    """Whether a resource of this name and type exists among those PipeMedic's identity can read (it has Reader on the
    resource group)."""
    import requests

    from azure_auth import ARM_SCOPE
    url = f"https://management.azure.com/subscriptions/{subscription}/resources"
    flt = f"name eq '{name.replace(chr(39), '')}' and resourceType eq '{rtype.replace(chr(39), '')}'"
    r = requests.get(url, params={"$filter": flt, "api-version": "2021-04-01"}, timeout=timeout_s,
                     headers={"Authorization": "Bearer " + credential.get_token(ARM_SCOPE).token})
    r.raise_for_status()
    return bool(r.json().get("value"))


def arm_run_command(subscription: str, rg: str, vm: str, script: str, credential, timeout_s: int = 240) -> str:
    """Run a shell script on a VM through the Azure Resource Manager run-command API and return its output."""
    import requests
    from azure_auth import ARM_SCOPE

    def hdr():
        return {"Authorization": "Bearer " + credential.get_token(ARM_SCOPE).token, "Content-Type": "application/json"}

    url = (f"https://management.azure.com/subscriptions/{subscription}/resourceGroups/{rg}/providers/"
           f"Microsoft.Compute/virtualMachines/{vm}/runCommand?api-version=2024-07-01")
    r = requests.post(url, headers=hdr(), json={"commandId": "RunShellScript", "script": [script]}, timeout=60)
    if r.status_code == 200:
        result = r.json()
    elif r.status_code == 202:
        poll = r.headers.get("Location") or r.headers.get("Azure-AsyncOperation")
        deadline = time.time() + timeout_s
        while True:
            time.sleep(int(r.headers.get("Retry-After", "5")))
            r = requests.get(poll, headers=hdr(), timeout=60)
            if r.status_code == 200:
                result = r.json()
                break
            if r.status_code != 202 or time.time() > deadline:
                raise RuntimeError(f"run command did not finish: HTTP {r.status_code}")
    else:
        raise RuntimeError(f"run command rejected: HTTP {r.status_code} {r.text[:200]}")
    values = (result.get("value") or result.get("properties", {}).get("output", {}).get("value") or [])
    return " | ".join(v.get("message", "") for v in values)


def execute(action: dict, d: dict, ado, pool: str, dry_run: bool) -> dict:
    """Run one allowlisted action. Returns a log entry."""
    name = action["action"]
    entry = {"build_id": d["build_id"], "pipeline": d["pipeline"], "action": name}
    if name not in AUTO_ACTIONS:
        return {**entry, "status": "skipped", "detail": "not on the allowlist"}
    if dry_run:
        return {**entry, "status": "dry-run", "detail": action["reason"]}

    try:
        if name == "retry-run":
            new_id = ado.queue_run(d["pipeline_id"], d.get("source_branch") or "refs/heads/main")
            return {**entry, "status": "done", "new_build_id": new_id, "detail": f"queued run {new_id}"}

        if name == "clean-workdir":
            if ado.agent_busy(pool, d["agent"]):
                return {**entry, "status": "skipped", "detail": f"{d['agent']} is running a job; will not clean now"}
            from azure_auth import DEFAULT_SUBSCRIPTION, get_credential
            sub = os.getenv("AZURE_SUBSCRIPTION_ID") or os.getenv("AZ_SUBSCRIPTION") or DEFAULT_SUBSCRIPTION
            rg = os.getenv("AZURE_RESOURCE_GROUP") or os.getenv("AZ_RESOURCE_GROUP") or config.settings().resource_group
            msg = arm_run_command(sub, rg, d["hostname"], CLEAN_SCRIPT, get_credential())
            return {**entry, "status": "done", "detail": msg.strip()[-300:]}

        if name == "create-work-item":
            title = f"[PipeMedic] {d['pipeline']} run {d['build_id']}: {d['category']} in '{d['failed_step']}'"
            body = (f"<b>Root cause:</b> {d['root_cause']}<br><b>Evidence:</b><br>"
                    + "<br>".join(e.replace("<", "&lt;") for e in d["evidence"])
                    + "<br><b>Suggestions:</b><br>" + "<br>".join(d["suggestions"]))
            wid = ado.create_work_item(title, body, d.get("requested_for"))
            return {**entry, "status": "done", "work_item": wid, "detail": f"work item {wid}"}
    except Exception as e:  # never let one failed action stop the agent
        return {**entry, "status": "failed", "detail": str(e)[:300]}
    return {**entry, "status": "skipped", "detail": "unhandled action"}
