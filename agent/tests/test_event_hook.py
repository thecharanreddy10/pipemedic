import ast
import importlib.util
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from event_hook import is_for, parse_event  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT = os.path.join(HERE, "..")


def payload(**res):
    base = {"id": 27, "status": "completed", "result": "failed", "url": "https://dev.azure.com/Hackathon925/abc-guid/_apis/build/Builds/27",
            "project": {"id": "abc-guid", "name": "Pipemedic"}}
    return {"eventType": "build.complete", "resource": {**base, **res},
            "resourceContainers": {"collection": {"baseUrl": "https://dev.azure.com/Hackathon925/"}}}


def test_a_failed_build_completed_event_gives_the_run_org_and_project():
    assert parse_event(payload()) == {"build_id": 27, "org": "Hackathon925", "project": "Pipemedic", "result": "failed"}


REAL_PAYLOAD = {  # what Azure DevOps actually sent for a failed run (version 1.0): the outcome is in status, there is no result
    "id": "e116ab4e-a71c-4b3a-8eef-80b8dd0cbd00", "eventType": "build.complete", "publisherId": "tfs", "resourceVersion": "1.0",
    "resource": {"uri": "vstfs:///Build/Build/30", "id": 30, "buildNumber": "20261020.3", "reason": "manual", "status": "failed",
                 "url": "https://dev.azure.com/Hackathon925/d719f110-94d0-4af2-9a34-87480130e84e/_apis/build/Builds/30",
                 "definition": {"definitionType": "xaml", "id": 2, "name": "azure-pipelines-script-failure"}},
    "resourceContainers": {"collection": {"baseUrl": "https://dev.azure.com/Hackathon925/"},
                           "project": {"id": "d719f110-94d0-4af2-9a34-87480130e84e"}},
}


MINIMAL_PAYLOAD = {  # the request body Azure DevOps really sent to the endpoint (details set to minimal): no outcome at all
    "subscriptionId": "1a900728-8302-4350-a43a-05852ad090a2", "notificationId": 4, "id": "d978a030-40fc-45d7-9a5f-024a63225ad8",
    "eventType": "build.complete", "publisherId": "tfs", "message": None, "detailedMessage": None, "resourceVersion": "1.0",
    "resource": {"id": 31, "url": "https://dev.azure.com/Hackathon925/d719f110-94d0-4af2-9a34-87480130e84e/_apis/build/Builds/31"},
    "resourceContainers": {"collection": {"id": "c", "baseUrl": "https://dev.azure.com/Hackathon925/"},
                           "account": {"id": "a", "baseUrl": "https://dev.azure.com/Hackathon925/"},
                           "project": {"id": "d719f110-94d0-4af2-9a34-87480130e84e", "baseUrl": "https://dev.azure.com/Hackathon925/"}},
}


def test_the_minimal_payload_azure_devops_really_sends_is_accepted_and_the_run_is_checked_later():
    assert parse_event(MINIMAL_PAYLOAD) == {"build_id": 31, "org": "Hackathon925", "project": None, "result": "unknown"}
    assert is_for(parse_event(MINIMAL_PAYLOAD), "Hackathon925", "Pipemedic")
    assert not is_for(parse_event(MINIMAL_PAYLOAD), "SomeoneElse", "Pipemedic")


def test_the_payload_azure_devops_really_sends_is_understood():
    assert parse_event(REAL_PAYLOAD) == {"build_id": 30, "org": "Hackathon925", "project": None, "result": "failed"}
    stopped = {**REAL_PAYLOAD, "resource": {**REAL_PAYLOAD["resource"], "status": "stopped"}}
    assert parse_event(stopped)["result"] == "stopped"
    for outcome in ("succeeded", "partiallySucceeded", "inProgress"):
        assert parse_event({**REAL_PAYLOAD, "resource": {**REAL_PAYLOAD["resource"], "status": outcome}}) is None
    assert is_for(parse_event(REAL_PAYLOAD), "Hackathon925", "Pipemedic")


def test_a_canceled_run_counts_but_a_success_or_a_running_build_does_not():
    assert parse_event(payload(result="canceled"))["result"] == "canceled"
    assert parse_event(payload(result="succeeded")) is None
    assert parse_event(payload(status="inProgress", result=None)) is None


def test_other_events_and_malformed_payloads_are_ignored():
    assert parse_event({"eventType": "git.push"}) is None
    assert parse_event({"eventType": "build.complete", "resource": {"status": "completed", "result": "failed"}}) is None
    assert parse_event(payload(id="not a number")) is None
    assert parse_event(None) is None and parse_event([]) is None and parse_event({}) is None


def test_the_organization_is_found_in_the_older_visualstudio_address_and_in_the_run_url():
    p = payload()
    p["resourceContainers"] = {"account": {"baseUrl": "https://hackathon925.visualstudio.com/"}}
    assert parse_event(p)["org"] == "hackathon925"
    p["resourceContainers"] = {}
    assert parse_event(p)["org"] == "Hackathon925"


def test_events_for_another_organization_or_project_are_not_ours():
    event = parse_event(payload())
    assert is_for(event, "hackathon925", "pipemedic")
    assert not is_for(event, "SomeoneElse", "Pipemedic") and not is_for(event, "Hackathon925", "Other")
    assert is_for({"build_id": 1, "org": None, "project": None}, "Hackathon925", "Pipemedic")


def test_the_deploy_package_contains_every_module_the_function_app_needs():
    """A module added to the agent but left out of the package makes the deployed watcher fail on import."""
    spec = importlib.util.spec_from_file_location("build_package", os.path.join(HERE, "..", "..", "health-api", "build_package.py"))
    build_package = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build_package)
    packaged = set(build_package.AGENT_MODULES)
    local = {f[:-3] for f in os.listdir(AGENT) if f.endswith(".py")}

    def imports(path):
        found = set()
        for node in ast.walk(ast.parse(open(path, encoding="utf-8").read())):
            if isinstance(node, ast.Import):
                found |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                found.add(node.module.split(".")[0])
        return found

    needed, queue = set(), [os.path.join(HERE, "..", "..", "health-api", "function_app.py")]
    while queue:
        for name in imports(queue.pop()) & local:
            if name not in needed:
                needed.add(name)
                queue.append(os.path.join(AGENT, name + ".py"))
    missing = sorted(m for m in needed if m + ".py" not in packaged)
    assert not missing, f"add to AGENT_MODULES in health-api/build_package.py: {missing}"
