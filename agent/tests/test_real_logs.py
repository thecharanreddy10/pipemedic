"""Regression tests built from real pipeline logs (Microsoft-hosted agents, an imported open-source project).

Each excerpt is the tail of a real failing step, copied from the benchmark in ../benchmark. They pin down behaviour that the
hand-written samples missed: the classifier had never been run on real tool output before.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from diagnose import classify  # noqa: E402


def f(log, step="step"):
    return {"pipeline": "p", "build_id": 1, "failed_step": step, "log_text": log, "issues": ["Bash exited with code '1'."]}


MISSING_MODULE = """Generating script.
Script contents:
python -m pytest -q --tb=short -p no:cacheprovider
========================== Starting Command Output ===========================
[command]/usr/bin/bash --noprofile --norc /home/vsts/work/_temp/bc35d4c1.sh
/opt/hostedtoolcache/Python/3.12.15/x64/bin/python: No module named pytest
##[error]Bash exited with code '1'.
"""

RUFF_LINT = """  --> src/click/utils.py:9:20
   |
 9 | from shutil import get_terminal_size
   |                    ^^^^^^^^^^^^^^^^^
help: Remove unused import: `shutil.get_terminal_size`
Found 2 errors.
[*] 2 fixable with the `--fix` option.
##[error]Bash exited with code '1'.
"""

RUFF_FORMAT = """Would reformat: src/click/formatting.py
1 file would be reformatted, 75 files already formatted
##[error]Bash exited with code '1'.
"""

MYPY = """src/click/utils.py:100: error: Argument 2 to "shorten" has incompatible type
"str"; expected "int"  [arg-type]
        return textwrap.shorten(text, str(max_length), placeholder="...", ...
                                      ^~~~~~~~~~~~~~~
Found 1 error in 1 file (checked 29 source files)
##[error]Bash exited with code '1'.
"""

SYNTAX_ERROR = """src/click/exceptions.py:10: in <module>
    from .utils import echo
E     File "/home/vsts/work/1/s/src/click/utils.py", line 89
E       if len(text) <= max_length
E                                 ^
E   SyntaxError: expected ':'
##[error]Bash exited with code '4'.
"""

BAD_IMPORT_NAME = """ImportError while loading conftest '/home/vsts/work/1/s/tests/conftest.py'.
tests/conftest.py:3: in <module>
    from click.testing import CliRunner
src/click/__init__.py:70: in <module>
    from .utils import echoo as echo
E   ImportError: cannot import name 'echoo' from 'click.utils' (/home/vsts/work/1/s/src/click/utils.py)
##[error]Bash exited with code '4'.
"""

GIT_PUSH_FORBIDDEN = """remote: TF401027: You need the Git 'GenericContribute' permission to perform this action. Details: identity 'Build\\guid', scope 'repository'.
fatal: unable to access 'https://dev.azure.com/org/project/_git/repo/': The requested URL returned error: 403
##[error]Bash exited with code '128'.
"""

PIP_NO_DISTRIBUTION = """ERROR: Could not find a version that satisfies the requirement pytest==99.0.2 (from versions: 2.0.0, 2.0.1)
ERROR: No matching distribution found for pytest==99.0.2
##[error]Bash exited with code '1'.
"""


def test_a_missing_python_module_is_a_dependency_problem():
    assert classify(f(MISSING_MODULE))["category"] == "dependency"


def test_real_linter_and_formatter_output_is_static_analysis():
    assert classify(f(RUFF_LINT))["category"] == "static-analysis"
    assert classify(f(RUFF_FORMAT))["category"] == "static-analysis"


def test_real_type_checker_output_is_static_analysis():
    assert classify(f(MYPY))["category"] == "static-analysis"


def test_code_that_does_not_import_is_a_code_error_not_a_dependency_problem():
    assert classify(f(SYNTAX_ERROR))["category"] == "code-error"
    out = classify(f(BAD_IMPORT_NAME))
    assert out["category"] == "code-error"
    assert any("cannot import name 'echoo'" in e for e in out["evidence"])


def test_azure_devops_permission_message_is_a_permission_failure():
    assert classify(f(GIT_PUSH_FORBIDDEN))["category"] == "permissions"


NPM_UNREACHABLE_THEN_JOB_TIMEOUT = {
    "log_text": ("npm error code ENOTFOUND\nnpm error syscall getaddrinfo\n"
                 "npm error network request to http://registry.corp.invalid/@eslint%2fjs failed, reason: getaddrinfo ENOTFOUND registry.corp.invalid\n"
                 "npm error network This is a problem related to network connectivity.\n"
                 "##[error]Bash exited with code '1'.\n"),
    "issues": ["Bash exited with code '1'.", "The job running on agent Azure Pipelines 1 ran longer than the maximum time of 5 minutes."],
}
CANCELLED_BY_TIMEOUT = {
    "log_text": ("........................................................................ [  6%]\n"
                 "##[error]The Operation will be canceled. The next steps may not contain expected logs.\n"
                 "##[error]The operation was canceled.\n"),
    "issues": ["The Operation will be canceled. The next steps may not contain expected logs.", "The operation was canceled.",
               "The job running on agent Azure Pipelines 1 ran longer than the maximum time of 5 minutes."],
}
NPM_404 = "npm error code E404\nnpm error 404 Not Found - GET https://registry.npmjs.org/@acme%2fui-kit - Not found\n##[error]Bash exited with code '1'.\n"
NPM_NOTARGET = "npm error code ETARGET\nnpm error notarget No matching version found for jest@29.7.99.\n##[error]Bash exited with code '1'.\n"
APT_MISSING = "#10 2.298 E: Unable to locate package libnonexistent-dev\nERROR: failed to build: failed to solve\n##[error]Bash exited with code '100'.\n"
DOCKER_TAG_MISSING = "#2 ERROR: docker.io/library/python:3.99-slim: not found\nERROR: failed to build: failed to solve: python:3.99-slim: failed to resolve source metadata for docker.io/library/python:3.99-slim\n"
JEST_MISSING_RELATIVE = ("  ● Test suite failed to run\n    Cannot find module './cartt' from 'src/cart.test.js'\n"
                         "Test Suites: 1 failed, 1 total\nTests:       0 total\n##[error]Bash exited with code '1'.\n")


def test_a_step_that_failed_on_its_own_is_not_called_a_timeout_just_because_the_job_ran_out_of_time():
    out = classify({"pipeline": "p", "build_id": 1, "failed_step": "Install", **NPM_UNREACHABLE_THEN_JOB_TIMEOUT})
    assert out["category"] == "network"


def test_a_step_cancelled_by_the_job_timeout_is_a_timeout():
    out = classify({"pipeline": "p", "build_id": 1, "failed_step": "Run tests", **CANCELLED_BY_TIMEOUT})
    assert out["category"] == "timeout"


def test_package_or_image_not_found_in_other_package_managers_is_a_dependency_problem():
    for name, log in [("npm 404", NPM_404), ("npm no matching version", NPM_NOTARGET), ("apt", APT_MISSING), ("docker tag", DOCKER_TAG_MISSING)]:
        assert classify(f(log))["category"] == "dependency", name


def test_a_missing_relative_module_is_a_code_error_but_a_missing_package_is_a_dependency():
    assert classify(f(JEST_MISSING_RELATIVE))["category"] == "code-error"
    assert classify(f("Cannot find module 'left-pad' from 'src/cart.js'\nTests: 1 failed, 1 total\n"))["category"] == "dependency"


def test_a_connection_that_timed_out_is_a_network_problem_not_a_job_timeout():
    assert classify(f("curl: (28) Connection timed out after 30001 milliseconds\n##[error]Bash exited with code '28'."))["category"] == "network"


def test_go_java_and_dotnet_failures_are_recognised_in_general_terms():
    # Synthetic, written from each tool's documented message formats, not copied from a real run. The benchmark's third
    # round runs Go and Maven on real agents to check them.
    cases = [
        ("go compile", "./cart.go:12:9: undefined: totall\nFAIL\texample.com/cart [build failed]", "code-error"),
        ("go test", "--- FAIL: TestTotal (0.00s)\n    cart_test.go:11: got 6 want 13\nFAIL\nFAIL\texample.com/cart\t0.002s", "test-failure"),
        ("maven compile", "[ERROR] COMPILATION ERROR :\n[ERROR] /src/Cart.java:[7,5] error: cannot find symbol", "code-error"),
        ("maven test", "[ERROR] Tests run: 3, Failures: 1, Errors: 0, Skipped: 0\n[ERROR] There are test failures.", "test-failure"),
        ("maven dependency", "[ERROR] Failed to execute goal on project cart: Could not resolve dependencies for project", "dependency"),
        ("maven host", "[ERROR] Could not transfer artifact x from/to corp (https://repo.corp.invalid/): Unknown host repo.corp.invalid", "network"),
        ("dotnet compile", "Program.cs(7,5): error CS1002: ; expected", "code-error"),
        ("dotnet package", "error NU1101: Unable to find package Acme.Internal.Kit. No packages exist with this id", "dependency"),
    ]
    wrong = [(n, classify(f(log))["category"], want) for n, log, want in cases if classify(f(log))["category"] != want]
    assert wrong == [], wrong


def test_unsatisfiable_pin_is_still_a_dependency_problem():
    assert classify(f(PIP_NO_DISTRIBUTION))["category"] == "dependency"


def test_a_test_whose_name_mentions_an_error_is_still_a_test_failure():
    # Exception names inside test ids or messages must not be read as an import-time crash.
    log = ("FAILED tests/test_parser.py::test_syntax_error_message - AssertionError: assert 1 == 2\n"
           "FAILED tests/test_utils.py::test_name_error_is_reported - assert 'x' == 'y'\n"
           "2 failed, 100 passed in 3.2s\n##[error]Bash exited with code '1'.")
    assert classify(f(log))["category"] == "test-failure"


def test_ordinary_text_with_found_numbers_is_not_static_analysis():
    log = "Found 3 files to upload\ncurl: (22) failed to upload\n##[error]Bash exited with code '22'."
    assert classify(f(log))["category"] != "static-analysis"
