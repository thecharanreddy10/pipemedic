"""Step 2: sets up Azure DevOps for PipeMedic. Safe to run again: anything that exists is left as it is.

1. the agent pool the runners join (organization level)
2. the project and its repository, filled with the demo services and pipelines from pipemedic-pipes/
3. the pool shared with the project, the four pipelines registered and allowed to use it
4. the Function App's identity added to the organization and to the project's Contributors
5. the two service hooks (a failed run, a canceled run) that tell the Function App a run failed

    python infra/2_devops.py            # signs in with your az login; reads infra/setup.env
    python infra/2_devops.py --dry-run  # says what it would do, changes nothing

You must be a Project Collection Administrator of the organization.
"""
import argparse
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "agent"))
from azure_auth import DEVOPS_SCOPE  # noqa: E402

SERVICES = ("frontend", "orders", "payments", "inventory")
AGILE = "adcc42ab-9882-485e-a3ed-7678f01f66bc"


def load_env(path):
    out = {}
    for line in open(path, encoding="utf-8"):
        line = line.split("#", 1)[0].strip()
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = shlex.split(v)[0] if v.strip() else ""
    return out


class DevOps:
    def __init__(self, org, dry):
        from azure.identity import AzureCliCredential
        self.cred, self.org, self.dry = AzureCliCredential(), org, dry
        self.base, self.vssps = f"https://dev.azure.com/{org}", f"https://vssps.dev.azure.com/{org}"

    def h(self):
        return {"Authorization": "Bearer " + self.cred.get_token(DEVOPS_SCOPE).token, "Content-Type": "application/json"}

    def get(self, url, **params):
        params.setdefault("api-version", "7.1")
        r = requests.get(url, headers=self.h(), params=params, timeout=60)
        return r.json() if r.ok else None

    def send(self, method, url, body, what, **params):
        params.setdefault("api-version", "7.1")
        if self.dry:
            print(f"  would {what}")
            return {}
        r = requests.request(method, url, headers=self.h(), params=params, json=body, timeout=60)
        if not r.ok:
            sys.exit(f"Could not {what}: HTTP {r.status_code} {r.text[:300]}")
        print(f"  {what}: done")
        return r.json() if r.text else {}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    env = load_env(os.path.join(HERE, "setup.env"))
    org, project, pool, app, rg = env["ADO_ORG"], env["ADO_PROJECT"], env["ADO_POOL"], env["FUNCTION_APP"], env["RG"]
    d = DevOps(org, a.dry_run)

    print("== 1. agent pool")
    pools = (d.get(f"{d.base}/_apis/distributedtask/pools", poolName=pool) or {}).get("value", [])
    if pools:
        pool_id = pools[0]["id"]
        print("  exists")
    else:
        pool_id = d.send("POST", f"{d.base}/_apis/distributedtask/pools", {"name": pool, "autoProvision": True}, f"create pool {pool}").get("id")

    print("== 2. project and repository")
    proj = d.get(f"{d.base}/_apis/projects/{project}")
    if not proj:
        d.send("POST", f"{d.base}/_apis/projects", {"name": project, "visibility": 0, "description": "PipeMedic demo services and pipelines",
               "capabilities": {"versioncontrol": {"sourceControlType": "Git"}, "processTemplate": {"templateTypeId": AGILE}}}, f"create project {project}")
        for _ in range(40):
            proj = d.get(f"{d.base}/_apis/projects/{project}")
            if a.dry_run or (proj and proj.get("state") == "wellFormed"):
                break
            time.sleep(4)
    else:
        print("  project exists")
    repos = (d.get(f"{d.base}/{project}/_apis/git/repositories") or {}).get("value", [])
    repo = next((r for r in repos if r["name"] == project), repos[0] if repos else None)
    if repo and not repo.get("defaultBranch") and not a.dry_run:
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(ROOT, "pipemedic-pipes")
            subprocess.run(["git", "init", "-q", "-b", "main", tmp], check=True)
            subprocess.run(["git", "-C", tmp, "config", "core.autocrlf", "false"], check=True)
            shutil.copytree(src, tmp, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"))
            subprocess.run(["git", "-C", tmp, "add", "-A"], check=True)
            subprocess.run(["git", "-C", tmp, "-c", "user.name=PipeMedic setup", "-c", "user.email=setup@pipemedic.local",
                            "commit", "-q", "-m", "Demo services and pipelines"], check=True)
            token = d.cred.get_token(DEVOPS_SCOPE).token
            subprocess.run(["git", "-C", tmp, "-c", f"http.extraheader=AUTHORIZATION: bearer {token}", "push", "-q",
                            f"https://dev.azure.com/{org}/{project}/_git/{repo['name']}", "main"], check=True)
            print("  pushed pipemedic-pipes/ to the repository")
    else:
        print("  repository already has content" if repo else "  would push pipemedic-pipes/ to the new repository")

    print("== 3. pool shared with the project, pipelines registered")
    queues = (d.get(f"{d.base}/{project}/_apis/distributedtask/queues", queueName=pool) or {}).get("value", [])
    queue_id = queues[0]["id"] if queues else d.send("POST", f"{d.base}/{project}/_apis/distributedtask/queues",
                                                      {"name": pool, "pool": {"id": pool_id}}, "share the pool with the project",
                                                      **{"api-version": "7.1-preview.1"}).get("id")
    existing = {x["name"]: x["id"] for x in (d.get(f"{d.base}/{project}/_apis/build/definitions") or {}).get("value", [])}
    ids = []
    for svc in SERVICES:
        name = f"{svc}-ci"
        if name in existing:
            ids.append(existing[name])
            continue
        body = {"name": name, "folder": "\\", "configuration": {"type": "yaml", "path": f"/pipelines/{svc}.yml",
                                                               "repository": {"id": (repo or {}).get("id"), "type": "azureReposGit"}}}
        ids.append(d.send("POST", f"{d.base}/{project}/_apis/pipelines", body, f"register {name}").get("id"))
    if queue_id and all(ids):
        d.send("PATCH", f"{d.base}/{project}/_apis/pipelines/pipelinepermissions/queue/{queue_id}",
               {"pipelines": [{"id": i, "authorized": True} for i in ids]}, "allow the pipelines to use the pool", **{"api-version": "7.1-preview.1"})

    print("== 4. the Function App's identity in Azure DevOps")
    principal = subprocess.run(["az", "functionapp", "identity", "show", "-n", app, "-g", rg, "--query", "principalId", "-o", "tsv"],
                               capture_output=True, text=True, shell=(os.name == "nt")).stdout.strip()
    sps = (d.get(f"{d.vssps}/_apis/graph/serviceprincipals", **{"api-version": "7.1-preview.1"}) or {}).get("value", [])
    sp = next((s for s in sps if s.get("originId") == principal), None)
    if not sp:
        d.send("POST", f"https://vsaex.dev.azure.com/{org}/_apis/serviceprincipalentitlements",
               {"servicePrincipal": {"origin": "aad", "originId": principal, "subjectKind": "servicePrincipal"},
                "accessLevel": {"accountLicenseType": "express"}}, f"add the identity of {app} to the organization", **{"api-version": "7.1-preview.1"})
        sps = (d.get(f"{d.vssps}/_apis/graph/serviceprincipals", **{"api-version": "7.1-preview.1"}) or {}).get("value", [])
        sp = next((s for s in sps if s.get("originId") == principal), None)
    if sp and proj and not a.dry_run:
        scope = d.get(f"{d.vssps}/_apis/graph/descriptors/{proj['id']}", **{"api-version": "7.1-preview.1"})["value"]
        groups = d.get(f"{d.vssps}/_apis/graph/groups", scopeDescriptor=scope, **{"api-version": "7.1-preview.1"})["value"]
        contrib = next(g for g in groups if g["displayName"] == "Contributors")
        d.send("PUT", f"{d.vssps}/_apis/graph/memberships/{sp['descriptor']}/{contrib['descriptor']}", None,
               "make the identity a Contributor of the project", **{"api-version": "7.1-preview.1"})
    else:
        print("  would make the identity a Contributor of the project")

    print("== 5. service hooks")
    cmd = [sys.executable, os.path.join(ROOT, "scripts", "create_service_hook.py"), "--org", org, "--project", project,
           "--function-url", f"https://{app}.azurewebsites.net", "--app", app, "--key-from-azure"] + ([] if a.dry_run else ["--apply"])
    subprocess.run(cmd, check=True, env={**os.environ, "AZURE_RESOURCE_GROUP": rg})
    print("Azure DevOps is ready.")


if __name__ == "__main__":
    main()
