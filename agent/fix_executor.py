"""Deciding on a fix plan and carrying it out.

decide() records a person's approval or rejection, once. carry_out() runs an approved plan with the Function App's
identity: a pull request (and its proving run), or a runner action from runner_actions, then a re-run of the pipeline.
check_results() later records whether the re-run or the proving run passed.
"""
import json
import time
from datetime import datetime, timezone

import fixplan
import runner_actions as ra


def decide(store, build_id: int, plan_id: str, approve: bool, who: str, via: str, now: datetime | None = None) -> tuple[bool, str, dict | None]:
    """Approve or reject a plan. Returns (accepted, message for the person, the plan)."""
    now = now or datetime.now(timezone.utc)
    plan = store.get_plan(build_id, plan_id)
    if not plan:
        return False, "This fix plan does not exist.", None
    reason = fixplan.is_runnable(plan, now)
    if reason:
        return False, reason[0].upper() + reason[1:] + ".", plan
    if not store.claim_approval(build_id, plan_id, who, via):
        return False, "Someone already decided on this plan.", store.get_plan(build_id, plan_id)
    plan = store.update_plan(build_id, plan_id, status="approved" if approve else "rejected", decided_by=who, decided_via=via,
                             decided_at=now.isoformat())
    if approve:  # one fix per failure: the other options for this run are no longer on offer
        for other in store.plans_for(build_id):
            if other["plan_id"] != plan_id and other.get("status") == "proposed":
                store.update_plan(build_id, other["plan_id"], status="superseded", superseded_by=plan_id)
    return True, ("Approved. PipeMedic is carrying it out; you will get an email with the result." if approve
                  else "Rejected. Nothing will be changed."), plan


def _rerun(ado, plan: dict) -> int:
    return ado.queue_run(plan["pipeline_id"], plan.get("source_branch") or "refs/heads/main")


def _agent_online(ado, pool: str, host_agent: str | None, wait_s: int) -> bool:
    deadline = time.time() + wait_s
    while time.time() < deadline:
        agents = ado.pool_agents(pool)
        if any(a.get("status") == "online" and (host_agent is None or a.get("name") == host_agent) for a in agents):
            return True
        time.sleep(10)
    return False


def carry_out(plan: dict, ado, store, credential, pool: str, run_link: str, now: datetime | None = None) -> dict:
    """Run an approved plan. Never raises: a failure is recorded on the plan."""
    now = now or datetime.now(timezone.utc)
    bid, pid, kind = plan["build_id"], plan["plan_id"], plan["kind"]
    if plan.get("status") != "approved":
        return {"ok": False, "detail": f"the plan is {plan.get('status')}, not approved"}
    store.update_plan(bid, pid, status="running", started=now.isoformat())
    host = plan.get("hostname")
    result: dict = {}
    try:
        if kind in fixplan.PR_KINDS:
            from ai_fix import open_fix_pr
            diag = plan.get("diagnosis") or {}
            proposal = {"changes": plan["changes"], "summary": plan["title"].removeprefix("Open a pull request: "),
                        "explanation": plan.get("explanation", ""), "risk": plan.get("risk", ""), "confidence": plan.get("confidence", "medium"),
                        "problems": []}
            r = open_fix_pr(ado, None, {**diag, "build_id": bid, "pipeline": plan["pipeline"], "pipeline_id": plan["pipeline_id"],
                                        "source_branch": plan.get("source_branch")},
                            run_link, store=store, dry_run=False, allow_pipeline=kind == "pipeline-pr", proposal=proposal, now=now)
            if r.get("status") != "opened":
                raise RuntimeError(r.get("detail") or r.get("status"))
            result = {"pr_id": r["pr_id"], "branch": r["branch"], "base": r.get("base"), "proving_run": r["verify_build"]}
        else:
            if kind == "clean-disk":
                result["disk"] = json.loads(ra.run_script(credential, host, ra.CLEAN_DISK) or "{}")
            elif kind == "restart-agent":
                result["agent"] = json.loads(ra.run_script(credential, host, ra.RESTART_AGENT) or "{}")
                result["agent_online"] = _agent_online(ado, pool, None, 120)
            elif kind == "stop-process":
                p = plan["process"]
                result["process"] = json.loads(ra.run_script(credential, host, ra.stop_process_script(int(p["pid"]), p["comm"], p.get("unit") or "")) or "{}")
                if not result["process"].get("ok"):
                    raise RuntimeError(result["process"].get("detail", "the process could not be stopped"))
            elif kind == "restart-vm":
                result["restart"] = ra.restart_vm(credential, host, 240)
                result["agent_online"] = _agent_online(ado, pool, None, 180)
            elif kind != "rerun":
                raise RuntimeError(f"no way to carry out a plan of kind {kind}")
            result["rerun"] = _rerun(ado, plan)
        done = store.update_plan(bid, pid, status="done", finished=datetime.now(timezone.utc).isoformat(), result=result, verified="pending")
        return {"ok": True, "plan": done, **result}
    except Exception as e:  # recorded, never raised: the queue must not retry a half-done change
        store.update_plan(bid, pid, status="failed", finished=datetime.now(timezone.utc).isoformat(), result={**result, "error": str(e)[:300]})
        return {"ok": False, "detail": str(e)[:300], **result}


def check_results(ado, store) -> list[dict]:
    """For finished plans: did the re-run (or the pull request's proving run) pass? Returns the plans whose result is now known."""
    known = []
    for plan in store.plans_to_verify():
        run_id = (plan.get("result") or {}).get("rerun") or (plan.get("result") or {}).get("proving_run")
        if not run_id:
            continue
        b = ado.build(run_id)
        if b.get("status") != "completed":
            continue
        verified = "passed" if b.get("result") == "succeeded" else "failed"
        known.append(store.update_plan(plan["build_id"], plan["plan_id"], verified=verified))
    return known


# ---------------- in the Function App ----------------
def _tell(w, plan: dict) -> str:
    """Email the person who decided (or the owner) how the fix went."""
    from fix_review import result_email
    from notify import _EMAIL, run_url

    cfg = w.cfg
    if not (cfg.notify_on and w.mailer is not None and cfg.notify_default_to):
        return "not sent"
    who = plan.get("decided_by") or ""
    to = cfg.notify_override_to or (who if _EMAIL.match(who) else cfg.notify_default_to)
    subject, text, body = result_email(plan, run_url(cfg.org, cfg.project, plan["build_id"]),
                                       f"https://dev.azure.com/{cfg.org}/{cfg.project}/_git/{cfg.project}")
    try:
        return str(w.mailer(to, subject, text, body))
    except Exception as e:
        return f"email failed: {str(e)[:150]}"


def execute(build_id: int, plan_id: str) -> dict:
    """Queue trigger: carry out one approved plan and email the result."""
    from azure_auth import get_credential
    from notify import run_url
    from watcher import build_watcher

    w = build_watcher()
    plan = w.store.get_plan(build_id, plan_id)
    if not plan:
        return {"ok": False, "detail": "no such plan"}
    r = carry_out(plan, w.ado, w.store, get_credential(), w.cfg.pool, run_url(w.cfg.org, w.cfg.project, build_id))
    plan = w.store.get_plan(build_id, plan_id)
    if plan.get("status") == "failed":
        _tell(w, plan)
    w.store.trace(build_id, f"fix {plan_id} ({plan.get('kind')}): {plan.get('status')}")
    return {k: v for k, v in r.items() if k != "plan"}


def verify_pending(w=None) -> list[str]:
    """Timer: report every finished fix whose re-run (or proving run) has now completed."""
    if w is None:
        from watcher import build_watcher
        w = build_watcher()
    told = []
    for plan in check_results(w.ado, w.store):
        told.append(f"{plan['plan_id']} {plan['verified']}: email {_tell(w, plan)}")
    return told
