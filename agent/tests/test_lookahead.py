"""Problems the pipeline did not reach: steps that never ran, and checked problems in what the commit changed."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import lookahead  # noqa: E402
from notify import render_email  # noqa: E402

PYPI = {("httpx", "0.27.2"): True, ("httpx", "9.9.9"): False, ("fastapi", "0.115.0"): True}
HUB = {("library", "python", "3.12-slim"): True, ("library", "python", "3.99-slim"): False}
REGISTRIES = {"pipemedicweather": True, "pipemedicweathr": False}
pypi = lambda n, v: PYPI.get((n, v))  # noqa: E731
hub = lambda ns, r, t: HUB.get((ns, r, t))  # noqa: E731
registry = lambda name: REGISTRIES.get(name)  # noqa: E731


def test_steps_and_stages_that_were_skipped_are_listed_but_not_the_agents_own_steps():
    records = [{"type": "Stage", "name": "Lint and test", "result": "failed", "order": 1},
               {"type": "Stage", "name": "Build and push the image", "result": "skipped", "order": 2},
               {"type": "Stage", "name": "Deploy to Container Apps", "result": "skipped", "order": 3},
               {"type": "Task", "name": "Unit tests", "result": "failed", "order": 4},
               {"type": "Task", "name": "Publish test results", "result": "skipped", "order": 5},
               {"type": "Task", "name": "Post-job: Get the code", "result": "skipped", "order": 6}]
    assert lookahead.not_reached(records) == ["Build and push the image (stage)", "Deploy to Container Apps (stage)", "Publish test results"]


def test_a_pin_that_does_not_exist_on_pypi_is_found_and_real_ones_are_not():
    found = lookahead.check_requirements("requirements.txt", "fastapi==0.115.0\nhttpx==9.9.9  # bump\nuvicorn\n", pypi)
    assert [(f["line"], f["token"]) for f in found] == [(2, "httpx==9.9.9")]


def test_a_base_image_that_does_not_exist_on_docker_hub_is_found():
    text = "FROM python:3.99-slim AS build\nRUN pip install x\nFROM build\nFROM ghcr.io/org/tool:1\n"
    found = lookahead.check_dockerfile("Dockerfile", text, hub)
    assert [(f["line"], f["token"]) for f in found] == [(1, "python:3.99-slim")]
    assert lookahead.docker_hub_ref("ghcr.io/org/tool:1") is None and lookahead.docker_hub_ref("redis") == ("library", "redis", "latest")


def test_a_registry_the_pipeline_names_that_does_not_exist_is_found():
    text = "variables:\n  ACR_NAME: pipemedicweathr\n  IMAGE: pipemedicweather.azurecr.io/weather-app\n"
    found = lookahead.check_pipeline("pipelines/weather-app.yml", text, registry)
    assert [(f["line"], f["token"]) for f in found] == [(2, "pipemedicweathr")]


def test_python_and_json_that_do_not_parse_are_found():
    assert lookahead.check_syntax("app/x.py", "def f(:\n")[0]["line"] == 1
    assert lookahead.check_syntax("config/a.json", "{bad")[0]["message"].startswith("config/a.json is not valid JSON")
    assert lookahead.check_syntax("app/ok.py", "x = 1\n") == []


def test_looking_ahead_leaves_out_the_failure_itself_and_an_unknown_answer_finds_nothing():
    files = {"requirements.txt": "httpx==9.9.9\n", "Dockerfile": "FROM python:3.99-slim\n", "README.md": "# x\n",
             "requirements-dev.txt": "mystery==1.0\n"}
    found = lookahead.look_ahead(list(files), files.__getitem__, "Could not find a version that satisfies the requirement httpx==9.9.9",
                                 pypi, hub, registry)
    assert [f["token"] for f in found] == ["python:3.99-slim"]  # httpx is the failure itself; "mystery" could not be checked


def test_the_email_lists_the_other_problems_and_the_steps_that_did_not_run():
    d = {"pipeline": "weather-app", "build_id": 130, "failed_step": "Unit tests", "verdict": "code-or-config", "root_cause": "rc",
         "evidence": [], "suggestions": [], "runner_health": {},
         "other_problems": [{"file": "Dockerfile", "line": 1, "message": "the base image python:3.99-slim does not exist on Docker Hub"}],
         "not_reached": ["Build and push the image (stage)"]}
    subject, text, body = render_email(d, "https://run")
    assert "Other problems in this commit that the pipeline did not reach" in text and "python:3.99-slim" in text
    assert "Steps that did not run, so they were not checked: Build and push the image (stage)" in text
    assert "python:3.99-slim" in body
