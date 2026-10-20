"""Reads the payload of an Azure DevOps "build completed" service hook.

Pure functions with no network access. The hook only says which run finished; the watcher reads everything else from
Azure DevOps itself, so nothing in the payload is trusted beyond the run's id.
"""
import re

FAILED = ("failed", "canceled", "stopped")


def parse_event(payload: dict) -> dict | None:
    """The run to investigate, or None when the event is not a finished failed run.

    A hook set to send minimal details (what scripts/create_service_hook.py creates) carries only the run's id and url, no
    outcome. That is fine: the hooks are filtered to failed and canceled runs, and the watcher checks the run's real result
    with Azure DevOps before it does anything. When an outcome is present it is read from resource.result (newer payloads) or
    resource.status (version 1.0), and anything other than a failure is ignored."""
    if not isinstance(payload, dict) or payload.get("eventType") != "build.complete":
        return None
    res = payload.get("resource") or {}
    outcome = str(res.get("result") or res.get("status") or "").lower()
    if outcome and outcome not in FAILED:
        return None
    try:
        build_id = int(res["id"])
    except (KeyError, TypeError, ValueError):
        return None
    containers = payload.get("resourceContainers") or {}
    base = ((containers.get("collection") or {}).get("baseUrl") or (containers.get("account") or {}).get("baseUrl")
            or res.get("url") or "")
    org = re.search(r"dev\.azure\.com/([^/]+)", base) or re.search(r"//([^./]+)\.visualstudio\.com", base)
    project = ((res.get("project") or {}).get("name") or ((res.get("definition") or {}).get("project") or {}).get("name"))
    return {"build_id": build_id, "org": org.group(1) if org else None, "project": project, "result": outcome or "unknown"}


def is_for(event: dict, org: str, project: str) -> bool:
    """True when the event belongs to the organization and project this agent watches. A missing name is not held against it."""
    same = lambda got, want: not got or got.lower() == want.lower()  # noqa: E731
    return same(event.get("org"), org) and same(event.get("project"), project)
