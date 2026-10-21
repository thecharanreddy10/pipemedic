"""Failures of a container pipeline: build the image with Docker on the runner, push it to ACR, deploy it to Container Apps."""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import fixplan  # noqa: E402
import runner_logs  # noqa: E402
from ai_fix import pick_context, repo_code_roots, roots  # noqa: E402
from config import under_code_root  # noqa: E402
from diagnose import classify, diagnose  # noqa: E402

HEALTHY = [{"timestamp_utc": "2026-10-21T09:00:30+00:00", "cpu_percent": 5.0, "memory_percent": 20.0, "disk_percent": 15,
            "disk_free_gb": 24, "agent_service_running": True}]
NOW = datetime(2026, 10, 21, 9, 5, tzinfo=timezone.utc)


def failure(log, step="Build the image"):
    return {"pipeline": "weather-app", "pipeline_id": 15, "build_id": 90, "failed_step": step, "issues": [], "log_text": log,
            "agent_name": "a1", "hostname": "vm1", "start": "2026-10-21T09:00:00Z", "finish": "2026-10-21T09:01:00Z",
            "repository": "weather-app", "source_branch": "refs/heads/main"}


def lines(*xs):
    return "\n".join(f"2026-10-21T09:00:{i:02d}.0Z {x}" for i, x in enumerate(xs))


DOCKER_DOWN = lines("Demo scenario in effect: the Docker service on the runner is stopped",
                    "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?",
                    "##[error]Bash exited with code '1'.")
BUILD_DISK_FULL = lines("Step 5/12 : RUN pip install --disable-pip-version-check -r requirements.txt",
                        "ERROR: Could not install packages due to an OSError: [Errno 28] No space left on device",
                        "##[error]Bash exited with code '1'.")
BAD_BASE_IMAGE = lines("Step 1/12 : FROM python:3.99-slim",
                       "manifest for python:3.99-slim not found: manifest unknown: manifest unknown",
                       "##[error]Bash exited with code '1'.")
BUILD_NETWORK = lines("Step 5/12 : RUN pip install --disable-pip-version-check -r requirements.txt",
                      "WARNING: Retrying (Retry(total=0)) after connection broken by 'ReadTimeoutError(\"HTTPSConnectionPool(host='pypi.org', port=443): Read timed out. (read timeout=15)\")'",
                      "The command '/bin/sh -c pip install --disable-pip-version-check -r requirements.txt' returned a non-zero code: 1",
                      "##[error]Bash exited with code '1'.")
REGISTRY_MISSING = lines("ERROR: The resource with name 'pipemedicweathr' and type 'Microsoft.ContainerRegistry/registries' could not be found in subscription 'Team subscription (0000)'.",
                         "##[error]Bash exited with code '1'.")
PUSH_DENIED = lines("The push refers to repository [pipemedicweather.azurecr.io/weather-app]",
                    "denied: requested access to the resource is denied",
                    "##[error]Bash exited with code '1'.")
REVISION_FAILED = lines("09:10:02 weather-app--b91: provisioning=Provisioned running=Failed health=Unhealthy",
                        "--- Container log for weather-app--b91",
                        "2026-10-21T09:09:58Z stderr F RuntimeError: WEATHER_MODE must be one of live, mock (got '')",
                        "##[error]Revision weather-app--b91 did not start: running=Failed health=Unhealthy",
                        "##[error]Bash exited with code '1'.")


def test_docker_not_running_is_the_runners_fault_and_offers_a_docker_restart():
    d = diagnose(failure(DOCKER_DOWN), HEALTHY)
    assert (d["category"], d["kind"], d["verdict"]) == ("runner-environment", "docker", "runner")
    assert "Docker is not running" in d["root_cause"]
    assert [p["kind"] for p in fixplan.build_plans(d, now=NOW)][:2] == ["restart-docker", "restart-vm"]


def test_a_full_disk_during_the_image_build_is_the_runners_disk():
    d = diagnose(failure(BUILD_DISK_FULL), HEALTHY)
    assert (d["category"], d["kind"], d["verdict"]) == ("runner-environment", "disk", "runner")
    assert fixplan.build_plans(d, now=NOW)[0]["kind"] == "clean-disk"


def test_a_base_image_tag_that_does_not_exist_is_a_dependency_in_the_code():
    d = diagnose(failure(BAD_BASE_IMAGE), HEALTHY)
    assert (d["category"], d["verdict"]) == ("dependency", "code-or-config")
    assert any("manifest unknown" in e for e in d["evidence"])


def test_a_download_that_times_out_inside_the_build_is_a_network_problem():
    assert classify(failure(BUILD_NETWORK))["category"] == "network"


def test_a_registry_name_that_does_not_exist_is_a_configuration_fault_the_pipeline_file_can_fix():
    d = diagnose(failure(REGISTRY_MISSING, step="Sign in to the registry"), HEALTHY)
    assert (d["category"], d["kind"], d["verdict"]) == ("configuration", "azure-resource", "code-or-config")
    assert any("pipemedicweathr" in e for e in d["evidence"])


def test_a_refused_push_is_for_a_person_and_is_not_simply_re_run():
    d = diagnose(failure(PUSH_DENIED, step="Push the image"), HEALTHY)
    assert d["category"] == "permissions"
    plans = fixplan.build_plans(d, now=NOW)
    assert [p["kind"] for p in plans] == ["manual"] and "access" in plans[0]["reason"]


def test_a_revision_that_does_not_start_is_configuration_and_quotes_the_apps_own_error():
    d = diagnose(failure(REVISION_FAILED, step="Wait for the revision to be healthy"), HEALTHY)
    assert (d["category"], d["kind"], d["verdict"]) == ("configuration", "deployment", "code-or-config")
    assert any("WEATHER_MODE must be one of" in e for e in d["evidence"])
    assert not any(e.startswith("---") for e in d["evidence"])


def test_docker_being_stopped_shows_in_the_runners_journal():
    text = "### docker\nOct 21 09:00:05 vm1 systemd[1]: Stopping docker.service - Docker Application Container Engine...\n### entries\n40"
    found = runner_logs.analyze(text)
    assert [f["flag"] for f in found] == ["docker-stopped (runner log)"] and found[0]["runner_fault"]


# ---- a repository with its app at the root
WEATHER_TREE = ["Dockerfile", "README.md", "requirements.txt", "app/__init__.py", "app/main.py", "app/weather.py", "app/config.py",
                "tests/test_weather.py", "pipelines/weather-app.yml", "pipelines/scripts/smoke_test.sh"]


def test_a_repository_without_the_configured_roots_is_code_as_a_whole():
    assert repo_code_roots(WEATHER_TREE) == [""]
    assert repo_code_roots(["services/orders/app/main.py", "pipelines/orders.yml"]) == ["services"]
    assert under_code_root("Dockerfile", roots(False, [""])) and under_code_root("app/weather.py", [""])
    assert roots(True, [""]) == [""]  # the whole repository already includes pipelines/; pipeline files keep their own rule


def test_a_container_failure_shows_the_model_the_dockerfile_and_the_app():
    chosen = pick_context(WEATHER_TREE, "Revision weather-app--b91 did not start\nRuntimeError: WEATHER_MODE must be one of")
    assert chosen[0] == "Dockerfile" and "requirements.txt" in chosen
    assert "app/config.py" in chosen and not any(p.startswith("tests/") for p in chosen)


BAD_BASE_IMAGE_DOCKER29 = lines("Step 1/12 : FROM python:3.99-slim",
                                'failed to resolve reference "docker.io/library/python:3.99-slim": docker.io/library/python:3.99-slim: not found',
                                "##[error]Bash exited with code '1'.")


def test_docker_29s_wording_for_a_missing_base_image_is_a_dependency_not_a_failed_test():
    c = classify(failure(BAD_BASE_IMAGE_DOCKER29))
    assert c["category"] == "dependency" and "python:3.99-slim" in c["evidence"][0]


def test_only_pytests_upper_case_failed_marker_counts_as_a_failed_test():
    assert classify(failure(lines("FAILED tests/test_weather.py::test_x - assert 1 == 2"), step="Unit tests"))["category"] == "test-failure"
    assert classify(failure(lines("failed to copy files: something else went wrong"), step="Package"))["category"] != "test-failure"


def test_a_test_failure_in_an_app_at_the_root_shows_the_model_all_of_the_apps_code():
    chosen = pick_context(WEATHER_TREE, "FAILED tests/test_weather.py::test_celsius_to_fahrenheit[0-32.0] - assert 32.0 == 32")
    assert {"app/weather.py", "app/main.py", "app/config.py"} <= set(chosen) and "tests/test_weather.py" in chosen


def test_an_edit_to_a_file_the_model_was_not_shown_is_refused():
    from ai_fix import validate
    change = {"path": "app/weather.py", "action": "edit", "new_content": "x = 1\n"}
    problems = validate({"changes": [change]}, set(WEATHER_TREE), {}, False, [""], shown={"app/main.py"})
    assert any("was not shown" in p for p in problems)


def test_an_edit_that_throws_away_most_of_a_file_is_refused():
    from ai_fix import validate
    old = "\n".join(f"line_{i} = {i}" for i in range(20))
    rewrite = {"path": "app/weather.py", "action": "edit", "new_content": "\n".join(f"other_{i} = {i}" for i in range(20))}
    small_fix = {"path": "app/weather.py", "action": "edit", "new_content": old.replace("line_3 = 3", "line_3 = 4")}
    assert any("mostly rewritten" in p for p in validate({"changes": [rewrite]}, set(WEATHER_TREE), {"app/weather.py": old}, code_roots=[""]))
    assert validate({"changes": [small_fix]}, set(WEATHER_TREE), {"app/weather.py": old}, code_roots=[""], shown={"app/weather.py"}) == []


def test_terminal_colour_codes_are_not_kept_in_the_evidence():
    c = classify(failure(lines("\x1b[91mERROR: Could not install packages due to an OSError: [Errno 28] No space left on device\x1b[0m")))
    assert c["evidence"][0] == "ERROR: Could not install packages due to an OSError: [Errno 28] No space left on device"


NO_ROLE = lines("WARNING: Unable to get admin user credentials with message: The resource with name 'pipemedicweather' and type "
                "'Microsoft.ContainerRegistry/registries' could not be found in subscription 'Team subscription (0000)'.",
                "ERROR: Unable to authenticate using AAD or admin login credentials. Please specify both username and password in non-interactive mode.",
                "##[error]Bash exited with code '1'.")


def test_a_not_found_registry_that_does_exist_is_an_access_problem_for_a_person():
    from diagnose import azure_resource_named, confirm_azure_resource
    d = diagnose(failure(NO_ROLE, step="Sign in to the registry"), HEALTHY)
    assert azure_resource_named(d) == ("pipemedicweather", "Microsoft.ContainerRegistry/registries")
    seen = confirm_azure_resource(d, True)
    assert (seen["category"], seen["kind"], seen["resource_checked"]) == ("permissions", "no-role", "exists")
    assert "has no role" in seen["root_cause"] and "AcrPush" in seen["suggestions"][0]
    assert [p["kind"] for p in fixplan.build_plans(seen, now=NOW)] == ["manual"]


def test_a_not_found_registry_that_does_not_exist_stays_a_wrong_name():
    from diagnose import confirm_azure_resource
    d = confirm_azure_resource(diagnose(failure(REGISTRY_MISSING, step="Sign in to the registry"), HEALTHY), False)
    assert (d["category"], d["resource_checked"]) == ("configuration", "missing") and "no resource by that name exists" in d["root_cause"]
    unchecked = confirm_azure_resource(diagnose(failure(REGISTRY_MISSING, step="Sign in to the registry"), HEALTHY), None)
    assert unchecked["category"] == "configuration" and "resource_checked" not in unchecked
