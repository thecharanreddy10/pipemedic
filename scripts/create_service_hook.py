"""Creates the Azure DevOps service hooks that tell PipeMedic a pipeline run failed.

For one project it creates two "build completed" hooks (a failed run and a canceled one) that POST to the Function App's
/api/ado-event endpoint. It only prints what it would do unless you pass --apply, and it skips a hook that already exists.

  python scripts/create_service_hook.py --org Hackathon925 --project Pipemedic \
      --function-url https://pipemedic-api-16135.azurewebsites.net --key-from-azure
  (add --apply to create them)

Signs in with your az login. The account needs the "Service Hooks: read, write and manage" permission in the project.
The Function key is sent as the x-functions-key header and is never printed.
"""
import argparse
import os
import subprocess
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent"))

import config  # noqa: E402
from azure_auth import DEVOPS_SCOPE, get_credential  # noqa: E402

STATUSES = {"Failed": "a failed run", "Stopped": "a canceled run"}


def azure_function_key(group: str, app: str) -> str:
    out = subprocess.run(["az", "functionapp", "keys", "list", "-g", group, "-n", app, "--query", "functionKeys.default", "-o", "tsv"],
                         capture_output=True, text=True, shell=(os.name == "nt"))
    key = out.stdout.strip()
    if out.returncode or not key:
        sys.exit(f"Could not read the Function key with az: {out.stderr.strip()[:200]}")
    return key


def main() -> None:
    c = config.settings()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--org", default=c.org)
    p.add_argument("--project", default=c.project)
    p.add_argument("--function-url", default=c.health_api, help="base address of the Function App")
    p.add_argument("--key", default=os.getenv("FUNCTION_KEY"), help="the Function key (or set FUNCTION_KEY)")
    p.add_argument("--key-from-azure", action="store_true", help="read the Function key with az functionapp keys list")
    p.add_argument("--app", default=c.health_api.split("//")[-1].split(".")[0], help="Function App name, for --key-from-azure")
    p.add_argument("--apply", action="store_true", help="really create the hooks (without it nothing is changed)")
    a = p.parse_args()

    key = a.key or (azure_function_key(c.resource_group, a.app) if a.key_from_azure else None)
    if not key:
        sys.exit("Give the Function key with --key, FUNCTION_KEY or --key-from-azure.")
    url = a.function_url.rstrip("/") + "/api/ado-event"
    headers = {"Authorization": "Bearer " + get_credential().get_token(DEVOPS_SCOPE).token, "Content-Type": "application/json"}
    base = f"https://dev.azure.com/{a.org}/_apis"

    project = requests.get(f"{base}/projects/{a.project}", params={"api-version": "7.1"}, headers=headers, timeout=30)
    project.raise_for_status()
    project_id = project.json()["id"]
    existing = requests.get(f"{base}/hooks/subscriptions", params={"api-version": "7.1"}, headers=headers, timeout=30)
    existing.raise_for_status()
    have = {(s["eventType"], (s.get("publisherInputs") or {}).get("projectId"), (s.get("publisherInputs") or {}).get("buildStatus"),
             (s.get("consumerInputs") or {}).get("url")) for s in existing.json()["value"]}

    for status, label in STATUSES.items():
        if ("build.complete", project_id, status, url) in have:
            print(f"already there: {label} in {a.org}/{a.project} -> {url}")
            continue
        body = {"publisherId": "tfs", "eventType": "build.complete", "resourceVersion": "1.0",
                "consumerId": "webHooks", "consumerActionId": "httpRequest",
                "publisherInputs": {"projectId": project_id, "buildStatus": status},
                "consumerInputs": {"url": url, "httpHeaders": f"x-functions-key:{key}", "resourceDetailsToSend": "minimal",
                                   "messagesToSend": "none", "detailedMessagesToSend": "none"}}
        if not a.apply:
            print(f"would create: {label} in {a.org}/{a.project} -> {url}   (add --apply)")
            continue
        r = requests.post(f"{base}/hooks/subscriptions", params={"api-version": "7.1"}, headers=headers, json=body, timeout=30)
        if r.ok:
            print(f"created: {label} in {a.org}/{a.project} (hook {r.json()['id']})")
        else:
            sys.exit(f"Azure DevOps refused the hook for {label}: HTTP {r.status_code} {r.text[:300]}")


if __name__ == "__main__":
    main()
