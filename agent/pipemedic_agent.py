"""PipeMedic AI agent: investigates failed Azure DevOps pipeline runs.

For each failed run it fetches the failing step's log, finds which runner executed it,
reads that runner's health history from the PipeMedic health API, then reports the
category, root cause and suggested fixes. Set ANTHROPIC_API_KEY to add a plain-language
explanation written by Claude; the verdict itself always comes from the rules.

Environment:
  AZDO_PAT           Azure DevOps personal access token (scope: Build Read, Agent Pools Read)
  HEALTH_API         base URL of the health API (default: the deployed Function App)
  ANTHROPIC_API_KEY  optional, enables the narrative summary
"""
import argparse
import base64
import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

import config
import lookahead

from diagnose import _parse_ts, apply_ai, diagnose, summarize_health
from ai_diagnose import ai_diagnose
from ai_fix import open_fix_pr
from flaky import assess as assess_flaky
from llm import AIUnavailable, get_llm
from pool_status import analyze_pool, render_pool
from predict import analyze_health, render_alerts
from remediate import execute, load_history, log_entry, plan_actions

DEFAULT_HEALTH_API = config.settings().health_api


class AzureDevOps:
    def __init__(self, org: str, project: str, pat: str | None = None, credential=None, repo: str | None = None):
        """Sign in with a personal access token, or with an Entra credential (a managed identity in Azure)."""
        self.base = f"https://dev.azure.com/{org}"
        self.org = org
        self.project = project
        self.repo = repo or project  # the repository fixes are read from and proposed to
        self._pat = pat
        self._credential = credential
        if not pat and credential is None:
            raise ValueError("Provide a personal access token or an Entra credential.")

    @property
    def headers(self) -> dict:
        if self._pat:
            return {"Authorization": "Basic " + base64.b64encode(f":{self._pat}".encode()).decode()}
        from azure_auth import DEVOPS_SCOPE
        return {"Authorization": "Bearer " + self._credential.get_token(DEVOPS_SCOPE).token}

    def _get(self, url: str, **params):
        params.setdefault("api-version", "7.1")
        r = requests.get(url, headers=self.headers, params=params, timeout=30)
        r.raise_for_status()
        return r

    def failed_builds(self, top: int):
        url = f"{self.base}/{self.project}/_apis/build/builds"
        return self._get(url, resultFilter="failed,canceled", statusFilter="completed", **{"$top": top}).json()["value"]

    def recent_builds(self, top: int):
        url = f"{self.base}/{self.project}/_apis/build/builds"
        return self._get(url, statusFilter="completed", **{"$top": top}).json()["value"]

    def build(self, build_id: int):
        return self._get(f"{self.base}/{self.project}/_apis/build/builds/{build_id}").json()

    def timeline(self, build_id: int):
        return self._get(f"{self.base}/{self.project}/_apis/build/builds/{build_id}/timeline").json()["records"]

    def log_text(self, build_id: int, log_id: int) -> str:
        return self._get(f"{self.base}/{self.project}/_apis/build/builds/{build_id}/logs/{log_id}").text

    # ---- repository access, used by the AI fix feature ----
    def _repo(self) -> str:
        return f"{self.base}/{self.project}/_apis/git/repositories/{self.repo}"

    def with_repo(self, repo: str) -> "AzureDevOps":
        """The same sign-in, working on another repository of the project."""
        import copy
        other = copy.copy(self)
        other.repo = repo
        return other

    def repo_paths(self, scope: str = "/services", branch: str = "main") -> list[str]:
        items = self._get(f"{self._repo()}/items", scopePath=scope, recursionLevel="full",
                          **{"versionDescriptor.version": branch}).json()["value"]
        return [i["path"].lstrip("/") for i in items if not i.get("isFolder")]

    def read_file(self, path: str, branch: str = "main", commit: str | None = None) -> str:
        version = {"versionDescriptor.version": commit, "versionDescriptor.versionType": "commit"} if commit else {"versionDescriptor.version": branch}
        r = self._get(f"{self._repo()}/items", path="/" + path.lstrip("/"), **version, **{"$format": "text"})
        return r.text

    def last_passing_commit(self, pipeline_id: int) -> str | None:
        """The commit the pipeline's most recent successful run built, if it has one."""
        url = f"{self.base}/{self.project}/_apis/build/builds"
        runs = self._get(url, definitions=pipeline_id, resultFilter="succeeded", statusFilter="completed", **{"$top": 1}).json()["value"]
        return runs[0].get("sourceVersion") if runs else None

    def branch_head(self, branch: str = "main") -> str:
        refs = self._get(f"{self._repo()}/refs", filter=f"heads/{branch}").json()["value"]
        return next(r["objectId"] for r in refs if r["name"] == f"refs/heads/{branch}")

    def create_branch_with_commit(self, branch: str, changes: list[dict], message: str, base: str = "main") -> str:
        """Create `branch` from `base` with one commit holding `changes` ({path, action, new_content}). Returns the commit id."""
        head = self.branch_head(base)
        h = {**self.headers, "Content-Type": "application/json"}
        r = requests.post(f"{self._repo()}/refs", headers=h, params={"api-version": "7.1"}, timeout=30,
                          json=[{"name": f"refs/heads/{branch}", "oldObjectId": "0" * 40, "newObjectId": head}])
        r.raise_for_status()
        body = {"refUpdates": [{"name": f"refs/heads/{branch}", "oldObjectId": head}],
                "commits": [{"comment": message, "changes": [
                    {"changeType": c["action"] if c["action"] == "edit" else "add", "item": {"path": "/" + c["path"]},
                     "newContent": {"content": c["new_content"], "contentType": "rawtext"}} for c in changes]}]}
        r = requests.post(f"{self._repo()}/pushes", headers=h, params={"api-version": "7.1"}, json=body, timeout=60)
        r.raise_for_status()
        return r.json()["commits"][0]["commitId"]

    def create_pull_request(self, source: str, title: str, description: str, target: str = "main") -> int:
        h = {**self.headers, "Content-Type": "application/json"}
        r = requests.post(f"{self._repo()}/pullrequests", headers=h, params={"api-version": "7.1"}, timeout=30, json={
            "sourceRefName": f"refs/heads/{source}", "targetRefName": f"refs/heads/{target}",
            "title": title, "description": description[:3900], "labels": [{"name": "pipemedic"}]})
        r.raise_for_status()
        return r.json()["pullRequestId"]

    def comment_on_pull_request(self, pr_id: int, text: str) -> None:
        h = {**self.headers, "Content-Type": "application/json"}
        r = requests.post(f"{self._repo()}/pullRequests/{pr_id}/threads", headers=h, params={"api-version": "7.1"}, timeout=30,
                          json={"comments": [{"parentCommentId": 0, "content": text, "commentType": 1}], "status": 1})
        r.raise_for_status()

    def pull_request(self, pr_id: int) -> dict:
        return self._get(f"{self._repo()}/pullrequests/{pr_id}").json()

    def changed_files(self, base_commit: str, target_commit: str) -> list[str]:
        """Files added or edited between two commits of this repository."""
        r = self._get(f"{self._repo()}/diffs/commits", baseVersion=base_commit, baseVersionType="commit",
                      targetVersion=target_commit, targetVersionType="commit", **{"$top": 200}).json()
        return [c["item"]["path"].lstrip("/") for c in r.get("changes", [])
                if not c["item"].get("isFolder") and "delete" not in str(c.get("changeType", "")).lower()]

    def run_for_commit(self, pipeline_id: int, branch: str, commit: str) -> dict | None:
        """The pipeline's run of this commit on this branch, if there is one."""
        runs = self._get(f"{self.base}/{self.project}/_apis/build/builds", definitions=pipeline_id, branchName=branch,
                         queryOrder="queueTimeDescending", **{"$top": 20}).json()["value"]
        return next((b for b in runs if b.get("sourceVersion") == commit), None)

    # ---- browsing: what an organization contains ----
    def projects(self) -> list[dict]:
        return self._get(f"{self.base}/_apis/projects", **{"$top": 200}).json()["value"]

    def repos(self) -> list[dict]:
        return self._get(f"{self.base}/{self.project}/_apis/git/repositories").json()["value"]

    def pools(self) -> list[dict]:
        return self._get(f"{self.base}/_apis/distributedtask/pools").json()["value"]

    def definitions(self, repo_id: str | None = None) -> list[dict]:
        """Pipeline definitions, optionally only those built from one repository."""
        params = {"$top": 200}
        if repo_id:
            params.update(repositoryId=repo_id, repositoryType="TfsGit")
        return self._get(f"{self.base}/{self.project}/_apis/build/definitions", **params).json()["value"]

    def runs_for(self, definition_ids: list[int] | None = None, top: int = 100) -> list[dict]:
        """Runs in any state (queued, running, finished), newest first."""
        params = {"$top": top, "queryOrder": "queueTimeDescending"}
        if definition_ids:
            params["definitions"] = ",".join(str(i) for i in definition_ids)
        return self._get(f"{self.base}/{self.project}/_apis/build/builds", **params).json()["value"]

    def _pool_id(self, pool_name: str) -> int:
        pools = self._get(f"{self.base}/_apis/distributedtask/pools", poolName=pool_name).json()["value"]
        if not pools:
            raise RuntimeError(f"agent pool '{pool_name}' not found")
        return pools[0]["id"]

    def pool_is_hosted(self, pool_name: str) -> bool:
        """True for a Microsoft-hosted pool: its agents are created per job, so they are offline between jobs and report no health."""
        pools = self._get(f"{self.base}/_apis/distributedtask/pools", poolName=pool_name).json()["value"]
        return bool(pools and pools[0].get("isHosted"))

    def pool_agents(self, pool_name: str) -> list[dict]:
        url = f"{self.base}/_apis/distributedtask/pools/{self._pool_id(pool_name)}/agents"
        return self._get(url, includeAssignedRequest="true").json()["value"]

    def pool_requests(self, pool_name: str, completed: int = 20) -> list[dict]:
        url = f"{self.base}/_apis/distributedtask/pools/{self._pool_id(pool_name)}/jobrequests"
        return self._get(url, completedRequestCount=completed).json()["value"]

    def latest_agent_version(self) -> str | None:
        try:
            v = self._get(f"{self.base}/_apis/distributedtask/packages/agent",
                          platform="linux-x64", top=1).json()["value"][0]["version"]
            return f"{v['major']}.{v['minor']}.{v['patch']}"
        except (requests.RequestException, KeyError, IndexError):
            return None

    def queue_run(self, pipeline_id: int, ref: str, parameters: dict | None = None) -> int:
        """Run a pipeline on a branch, optionally with values for its runtime parameters."""
        url = f"{self.base}/{self.project}/_apis/pipelines/{pipeline_id}/runs"
        body = {"resources": {"repositories": {"self": {"refName": ref}}}}
        if parameters:
            body["templateParameters"] = parameters
        r = requests.post(url, headers=self.headers, params={"api-version": "7.1"}, json=body, timeout=30)
        r.raise_for_status()
        return r.json()["id"]

    def create_work_item(self, title: str, html: str, assignee: str | None) -> int:
        """Create a work item, trying the usual types because process templates differ."""
        ops = [
            {"op": "add", "path": "/fields/System.Title", "value": title},
            {"op": "add", "path": "/fields/System.Description", "value": html},
            {"op": "add", "path": "/fields/System.Tags", "value": "pipemedic"},
        ]
        if assignee:
            ops.append({"op": "add", "path": "/fields/System.AssignedTo", "value": assignee})
        headers = {**self.headers, "Content-Type": "application/json-patch+json"}
        last = None
        for wi_type in ("Bug", "Issue", "Task"):
            r = requests.post(f"{self.base}/{self.project}/_apis/wit/workitems/${wi_type}",
                              headers=headers, params={"api-version": "7.1"}, json=ops, timeout=30)
            if r.ok:
                return r.json()["id"]
            last = f"{r.status_code} {r.text[:200]}"
        raise RuntimeError(f"could not create work item: {last}")

    def agent_busy(self, pool_name: str, agent_name: str) -> bool:
        pools = self._get(f"{self.base}/_apis/distributedtask/pools", poolName=pool_name).json()["value"]
        agents = self._get(f"{self.base}/_apis/distributedtask/pools/{pools[0]['id']}/agents",
                           includeAssignedRequest="true").json()["value"]
        return any(a["name"] == agent_name and a.get("assignedRequest") for a in agents)

    def agent_hosts(self, pool_name: str) -> dict:
        """Map agent name -> machine hostname using the agent's reported capabilities."""
        pools = self._get(f"{self.base}/_apis/distributedtask/pools", poolName=pool_name).json()["value"]
        if not pools:
            return {}
        agents = self._get(
            f"{self.base}/_apis/distributedtask/pools/{pools[0]['id']}/agents", includeCapabilities="true"
        ).json()["value"]
        hosts = {}
        for a in agents:
            caps = a.get("systemCapabilities", {})
            hosts[a["name"]] = caps.get("Agent.ComputerName") or caps.get("HOSTNAME")
        return hosts


def list_orgs(credential) -> list[str]:
    """The organizations a signed-in person belongs to. Needs a Microsoft Entra sign-in: a personal access token cannot list them."""
    from azure_auth import DEVOPS_SCOPE
    headers = {"Authorization": "Bearer " + credential.get_token(DEVOPS_SCOPE).token}
    me = requests.get("https://app.vssps.visualstudio.com/_apis/profile/profiles/me", params={"api-version": "7.1"}, headers=headers, timeout=30)
    me.raise_for_status()
    accounts = requests.get("https://app.vssps.visualstudio.com/_apis/accounts",
                            params={"memberId": me.json()["id"], "api-version": "7.1"}, headers=headers, timeout=30)
    accounts.raise_for_status()
    return sorted(a["accountName"] for a in accounts.json()["value"])


def collect_failure(ado: AzureDevOps, build: dict, hosts: dict) -> dict:
    """Gather the failing step, its issues and log, and the runner that executed it."""
    build_id = build["id"]
    records = ado.timeline(build_id)
    bad = [r for r in records if r.get("result") in ("failed", "canceled") and r["type"] in ("Task", "Job")]
    tasks = [r for r in bad if r["type"] == "Task"]
    jobs = [r for r in bad if r["type"] == "Job"]

    step = tasks[0] if tasks else (jobs[0] if jobs else None)
    issues = []
    for r in bad:
        issues += [i["message"] for i in (r.get("issues") or []) if i.get("type") == "error"]
        issues += [i["message"] for i in (r.get("issues") or []) if i.get("type") == "warning" and "maximum time" in i["message"]]
    log = ado.log_text(build_id, step["log"]["id"]) if step and step.get("log") else ""
    # The other failed steps (up to three more): the first failure is not always the whole story.
    others = []
    for t in [t for t in tasks[1:] if t.get("log")][:3]:
        tail = "\n".join(ado.log_text(build_id, t["log"]["id"]).splitlines()[-40:])
        others.append(f"=== {t['name']} ===\n{tail}")

    job = jobs[0] if jobs else next((r for r in records if r["type"] == "Job"), {})
    agent_name = job.get("workerName")
    return {
        "pipeline": build["definition"]["name"],
        "pipeline_id": build["definition"]["id"],
        "source_branch": build.get("sourceBranch"),
        "source_version": build.get("sourceVersion"),
        "repository": (build.get("repository") or {}).get("name"),
        "requested_for": (build.get("requestedFor") or {}).get("uniqueName"),
        "build_id": build_id,
        "failed_step": step["name"] if step else "unknown step",
        "issues": issues,
        "log_text": log,
        "other_logs": "\n".join(others)[-4000:],
        "not_reached": lookahead.not_reached(records),
        "agent_name": agent_name,
        "hostname": hosts.get(agent_name) if agent_name else None,
        "start": build.get("startTime"),
        "finish": build.get("finishTime"),
    }


def fetch_health(api: str, hostname: str | None, start: str | None) -> list[dict]:
    """Read health history that covers the run, using the public API (minutes counts back from now)."""
    if not hostname:
        return []  # without the runner's name the history of every other runner would be returned and blamed or cleared wrongly
    minutes = 60
    if start:
        age = (datetime.now(timezone.utc) - _parse_ts(start)).total_seconds() / 60
        minutes = max(5, math.ceil(age) + 5)
    params = {"minutes": minutes}
    params["hostname"] = hostname
    r = requests.get(f"{api}/api/health-history", params=params, timeout=30)
    r.raise_for_status()
    return r.json()["records"]


def narrate(diagnosis: dict) -> str | None:
    """Optional plain-language explanation from Claude; returns None if unavailable."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic

        client = anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-sonnet-5-5",
            max_tokens=400,
            messages=[{
                "role": "user",
                "content": "You are a CI/CD reliability engineer. In 3 short sentences, explain this pipeline "
                           "failure to a developer, using only the facts given. Do not change the verdict.\n\n"
                           + json.dumps(diagnosis, indent=2),
            }],
        )
        return msg.content[0].text.strip()
    except Exception as e:  # narrative is optional, never fail the report
        return f"(narrative unavailable: {e})"


def render(d: dict) -> str:
    h = d["runner_health"]
    lines = [
        f"=== {d['pipeline']} (run {d['build_id']}) ===",
        f"Failed step : {d['failed_step']}",
        f"Runner      : {d.get('agent') or '?'} ({d.get('hostname') or 'host unknown'})",
        f"Category    : {d['category']}",
        f"Verdict     : {d['verdict']}",
        f"Root cause  : {d['root_cause']}",
    ]
    if h.get("samples"):
        lines.append(f"Runner health during run: {h['samples']} samples, CPU max {h['cpu_max']}%, "
                     f"memory max {h['memory_max']}%, disk {h['disk_max']}% used"
                     + (f", flags: {', '.join(h['flags'])}" if h["flags"] else ", no flags"))
    else:
        lines.append("Runner health during run: no samples found")
    if d["evidence"]:
        lines.append("Evidence:")
        lines += [f"  - {e}" for e in d["evidence"]]
    lines.append("Suggestions:")
    lines += [f"  {i}. {s}" for i, s in enumerate(d["suggestions"], 1)]
    if d.get("narrative"):
        lines += ["Explanation:", f"  {d['narrative']}"]
    ai = d.get("ai")
    if ai and ai.get("error"):
        lines += ["AI second opinion:", f"  unavailable: {ai['error']}"]
    elif ai:
        agree = "agrees with the rules" if ai["agrees_with_rules"] else f"differs from the rules ({d['category']})"
        lines += [f"AI second opinion ({ai['model']}): {ai['category']}, confidence {ai['confidence']}, {agree}",
                  f"  Cause: {ai['likely_cause']}"]
        lines += [f"  Verified quote: {q}" for q in ai["evidence_verified"][:3]]
        if ai["evidence_unverified"]:
            lines.append(f"  Not found in the log (ignored): {len(ai['evidence_unverified'])} quote(s)")
        lines += [f"  Fix: {s}" for s in ai["suggested_fixes"][:3]]
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description="PipeMedic: investigate failed pipeline runs")
    cfg = config.settings()
    p.add_argument("--org", default=cfg.org)
    p.add_argument("--project", default=cfg.project)
    p.add_argument("--pool", default=cfg.pool)
    p.add_argument("--build-id", type=int, help="investigate one run")
    p.add_argument("--last", type=int, default=4, help="investigate the N most recent failed runs")
    p.add_argument("--json", action="store_true", help="print JSON instead of text")
    p.add_argument("--pool-status", action="store_true", help="show agent pool capacity, queue wait and findings")
    p.add_argument("--remediate", action="store_true", help="show the guardrailed remediation plan (dry-run)")
    p.add_argument("--act", action="store_true", help="execute allowlisted remediation actions (implies --remediate)")
    p.add_argument("--watch", action="store_true", help="keep running and investigate each new failed run")
    p.add_argument("--interval", type=int, default=30, help="seconds between checks in --watch mode")
    p.add_argument("--ai", action="store_true", help="add an AI second opinion from Azure AI Foundry (needs az login)")
    p.add_argument("--propose-fix", action="store_true", help="ask the AI for a fix and show it (changes nothing)")
    p.add_argument("--open-pr", action="store_true", help="open a pull request with the AI fix and re-run the pipeline on it")
    p.add_argument("--build-memory", action="store_true", help="remember every failed run so later failures can be matched to them")
    p.add_argument("--alerts", action="store_true", help="show predictive alerts from runner health (no token needed)")
    p.add_argument("--alert-window", type=int, default=60, help="minutes of health history for --alerts")
    args = p.parse_args()

    api = os.getenv("HEALTH_API", DEFAULT_HEALTH_API)
    if args.alerts:
        r = requests.get(f"{api}/api/health-history", params={"minutes": args.alert_window}, timeout=30)
        r.raise_for_status()
        alerts = analyze_health(r.json()["records"])
        print(json.dumps(alerts, indent=2) if args.json else render_alerts(alerts))
        return

    pat = os.getenv("AZDO_PAT")
    if not pat:
        sys.exit("Set AZDO_PAT (an Azure DevOps personal access token) in your environment.")

    ado = AzureDevOps(args.org, args.project, pat)
    if args.pool_status:
        status = analyze_pool(ado.pool_agents(args.pool), ado.pool_requests(args.pool),
                              latest_version=ado.latest_agent_version())
        print(json.dumps(status, indent=2) if args.json else render_pool(status))
        return
    hosts = ado.agent_hosts(args.pool)
    if args.build_memory:
        import memory
        from azure_auth import get_credential
        from store import DEFAULT_STATE_URL, TableStore
        added = memory.seed(ado, TableStore(os.getenv("STATE_TABLE_URL", DEFAULT_STATE_URL), get_credential()), get_llm(), hosts,
                            ado.recent_builds(50), api, collect_failure, fetch_health, diagnose, assess_flaky)
        print(f"Added {added} failed run(s) to memory.")
        return

    def investigate(builds):
        results = []
        recent = ado.recent_builds(50)  # for flaky-test comparison on the same commit
        for b in builds:
            failure = collect_failure(ado, b, hosts)
            failure["flaky"] = assess_flaky(b, recent)
            health = fetch_health(api, failure["hostname"], failure["start"])
            d = diagnose(failure, health)
            d["narrative"] = narrate(d)
            if args.ai:
                try:
                    d["ai"] = ai_diagnose(get_llm(), d)
                except (AIUnavailable, RuntimeError) as e:  # the rules' verdict still stands
                    d["ai"] = {"error": str(e)}
                d = apply_ai(d, d.get("ai"))
            results.append(d)
        return results

    def propose(results):
        for d in results:
            print(f"\n--- AI fix for run {d['build_id']} ({'opening a pull request' if args.open_pr else 'proposal only'}) ---", flush=True)
            try:
                r = open_fix_pr(ado, get_llm(), d, f"https://dev.azure.com/{args.org}/{args.project}/_build/results?buildId={d['build_id']}",
                                dry_run=not args.open_pr)
            except (AIUnavailable, RuntimeError, requests.RequestException) as e:
                print(f"  not available: {e}", flush=True)
                continue
            p_ = r.get("proposal") or {}
            print(f"  {r['status']}: {r.get('detail') or p_.get('summary', '')}", flush=True)
            for c in p_.get("changes", []):
                print(f"    {c['action']} {c['path']}", flush=True)
            if r.get("pr_id"):
                print(f"    pull request {r['pr_id']}, verification run {r['verify_build']}", flush=True)

    def show(results):
        if args.json:
            print(json.dumps(results, indent=2), flush=True)
        else:
            print("\n\n".join(render(d) for d in results) or "No failed runs found.", flush=True)
        if args.remediate or args.act:
            remediate_all(results)
        if args.propose_fix or args.open_pr:
            propose(results)

    def remediate_all(results):
        for d in results:
            history = load_history()
            now_start = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
            try:
                current = summarize_health(
                    fetch_health(api, d["hostname"], now_start), now_start, datetime.now(timezone.utc).isoformat()
                )
            except requests.RequestException:
                current = {"samples": 0, "flags": ["no-health-data"]}
            plan = plan_actions(d, current, history)
            mode = "ACTING" if args.act else "dry-run"
            print(f"\n--- Remediation for run {d['build_id']} ({mode}) ---", flush=True)
            for a in plan:
                tag = "auto" if a["auto"] else "needs approval"
                print(f"  [{tag}] {a['action']}: {a['reason']}", flush=True)
                if a["auto"]:
                    entry = execute(a, d, ado, args.pool, dry_run=not args.act)
                    print(f"      -> {entry['status']}: {entry['detail']}", flush=True)
                    if args.act:
                        log_entry(entry)

    if args.watch:
        # Remember what already failed so only new failures are investigated.
        seen = {b["id"] for b in ado.failed_builds(50)}
        printed_alerts: set[str] = set()
        print(f"Watching {args.org}/{args.project} every {args.interval}s. Ctrl+C to stop.", flush=True)
        try:
            while True:
                time.sleep(args.interval)
                try:  # predictive alerts: print each one once, when it first appears
                    hr = requests.get(f"{api}/api/health-history", params={"minutes": 60}, timeout=30)
                    for a in analyze_health(hr.json()["records"]):
                        key = f"{a['host']}|{a['metric']}|{a['level']}"
                        if key not in printed_alerts:
                            printed_alerts.add(key)
                            print(f"\n[ALERT {a['level']}] {a['message']}\n    -> {a['suggestion']}", flush=True)
                except (requests.RequestException, ValueError, KeyError):
                    pass
                new = [b for b in ado.failed_builds(50) if b["id"] not in seen]
                if new:
                    seen.update(b["id"] for b in new)
                    show(investigate(sorted(new, key=lambda b: b["id"])))
        except KeyboardInterrupt:
            return
    else:
        builds = [ado.build(args.build_id)] if args.build_id else ado.failed_builds(args.last)
        show(investigate(builds))


if __name__ == "__main__":
    main()
