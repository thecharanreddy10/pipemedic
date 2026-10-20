"""Catalog of real defects introduced into click, each with a ground-truth cause label.

Each defect is a small change a developer could plausibly commit by mistake. The label records the *cause* of the
failure as a person triaging it would name it. It is never shown to the classifier: spec.json (what gets pushed) has no
labels, answers.json (the truth) is kept apart.

Edit operations: {"path", "old", "new"} replaces exactly one occurrence, {"path", "append"} adds text at the end of a file,
{"path", "create"} adds a new file.
"""

PY = '''- task: UsePythonVersion@0
  displayName: Use Python 3.12
  inputs:
    versionSpec: "3.12"
'''


def _job(name, title, steps, comment):
    body = "".join("      " + ln + "\n" if ln else "\n" for ln in steps.rstrip("\n").split("\n"))
    py = "".join("      " + ln + "\n" for ln in PY.rstrip("\n").split("\n"))
    return (f"# {comment}\ntrigger: none\npr: none\n\npool:\n  vmImage: ubuntu-latest\n\njobs:\n  - job: {name}\n"
            f"    displayName: {title}\n    timeoutInMinutes: 5\n    steps:\n" + py + body)


TESTS_YML = _job("tests", "Tests", """- script: |
    python -m pip install --upgrade pip
    pip install -e . pytest==9.0.2
  displayName: Install click and test dependencies
- script: python -m pytest -q --tb=short -p no:cacheprovider
  displayName: Run tests
""", "Runs the click test suite on a Microsoft-hosted agent.")

LINT_YML = _job("lint", "Lint", """- script: pip install ruff==0.15.9
  displayName: Install ruff
- script: ruff check --no-fix src tests
  displayName: Lint
- script: ruff format --check src tests
  displayName: Check formatting
""", "Lints and format-checks the source with ruff.")

TYPECHECK_YML = _job("typecheck", "Type check", """- script: |
    python -m pip install --upgrade pip
    pip install -e . mypy==1.20.0 pytest==9.0.2
  displayName: Install click and mypy
- script: mypy
  displayName: Type check
""", "Type-checks the source with mypy (strict, as configured in pyproject.toml).")

PACKAGE_YML = _job("package", "Package", """- script: pip install build twine
  displayName: Install build tools
- script: python -m build
  displayName: Build sdist and wheel
- script: twine check dist/*
  displayName: Check package metadata
- script: |
    python -m venv /tmp/smoke
    /tmp/smoke/bin/pip install dist/*.whl
    /tmp/smoke/bin/python -c "import click; print(click.__version__)"
  displayName: Smoke test the wheel
""", "Builds the sdist and wheel and smoke-tests the wheel.")

BASELINE_PIPELINES = {
    "click-tests": ("/.azure-pipelines/tests.yml", TESTS_YML),
    "click-lint": ("/.azure-pipelines/lint.yml", LINT_YML),
    "click-typecheck": ("/.azure-pipelines/typecheck.yml", TYPECHECK_YML),
    "click-package": ("/.azure-pipelines/package.yml", PACKAGE_YML),
}

T = "/.azure-pipelines/tests.yml"
L = "/.azure-pipelines/lint.yml"
M = "/.azure-pipelines/typecheck.yml"
P = "/.azure-pipelines/package.yml"

INSTALL = "pip install -e . pytest==9.0.2\n"

D = []


def add(label, pipeline, edits, message, subtype="", ambiguous=False, note=""):
    D.append(dict(label=label, pipeline=pipeline, edits=edits, message=message, subtype=subtype,
                  ambiguous=ambiguous, note=note))


def rep(path, old, new):
    return {"path": path, "old": old, "new": new}


def app(path, text):
    return {"path": path, "append": text}


def new(path, content):
    return {"path": path, "create": content}


# ---------------------------------------------------------------- test failures: behaviour changed in the code
add("test-failure", "click-tests", [rep("src/click/exceptions.py", "    exit_code: t.ClassVar[int] = 2\n\n    ctx: Context | None",
                                        "    exit_code: t.ClassVar[int] = 1\n\n    ctx: Context | None")],
    "Use the generic exit code for usage errors", "exit-code")
add("test-failure", "click-tests", [rep("src/click/utils.py", "    if len(text) <= max_length:\n        return text",
                                        "    if len(text) < max_length:\n        return text")],
    "Tighten the short help length check", "off-by-one")
add("test-failure", "click-tests", [rep("src/click/types.py", '        "y": True,\n', '        "y": False,\n')],
    "Clean up the boolean string table", "wrong-value")
add("test-failure", "click-tests", [rep("src/click/types.py", "        return bound + dir\n", "        return bound\n")],
    "Simplify the integer range clamp", "off-by-one")
add("test-failure", "click-tests", [rep("src/click/parser.py", "        rv[spos + 1 :] = reversed(rv[spos + 1 :])",
                                        "        rv[spos + 1 :] = rv[spos + 1 :]")],
    "Avoid an extra reversal when unpacking arguments", "logic")
add("test-failure", "click-tests", [rep("src/click/types.py", "        if not self.case_sensitive:\n            normed_value = normed_value.casefold()",
                                        "        if self.case_sensitive:\n            normed_value = normed_value.casefold()")],
    "Reorder the choice normalisation", "inverted-condition")
add("test-failure", "click-tests", [rep("src/click/formatting.py", "        col_spacing: int = 2,", "        col_spacing: int = 3,")],
    "Give definition lists a little more room", "wrong-default")
add("test-failure", "click-tests", [rep("src/click/termui.py", 'bits.append(f"\\033[{1 if bold else 22}m")',
                                        'bits.append(f"\\033[{1 if bold else 21}m")')],
    "Adjust the bold reset code", "wrong-constant")
add("test-failure", "click-tests", [rep("src/click/shell_completion.py", "    return c in ctx._opt_prefixes", "    return c not in ctx._opt_prefixes")],
    "Simplify the option prefix check", "inverted-condition")
add("test-failure", "click-tests", [rep("src/click/termui.py", '        elif default is not None and value == "":\n            rv = default',
                                        '        elif default is not None and value == "":\n            rv = not default')],
    "Handle an empty confirmation answer", "inverted-condition")

# ---------------------------------------------------------------- dependency problems
add("dependency", "click-tests", [rep(T, INSTALL, "pip install -e . pytest==99.0.2\n")],
    "Pin pytest", "nonexistent-version")
add("dependency", "click-tests", [rep(T, INSTALL, "pip install -e . pytest==9.0.2 pytest-timeouts\n")],
    "Add a timeout plugin for the tests", "nonexistent-package")
add("dependency", "click-tests", [rep(T, INSTALL, "pip install -e . pytest==9.0.2 pluggy==0.13.1\n")],
    "Pin pluggy for reproducible installs", "conflicting-pins")
add("dependency", "click-tests", [rep(T, INSTALL, "pip install -e .\n")],
    "Install only the package", "missing-package")
add("dependency", "click-tests", [rep("src/click/utils.py", "import typing as t\nfrom functools import update_wrapper",
                                      "import typing as t\n\nimport yaml\nfrom functools import update_wrapper")],
    "Load defaults from a YAML file", "undeclared-dependency")
add("dependency", "click-tests", [rep("pyproject.toml", 'requires-python = ">=3.10"', 'requires-python = ">=3.14"')],
    "Raise the minimum supported Python", "python-version-constraint", ambiguous=True,
    note="could also be read as a packaging/config error")

# ---------------------------------------------------------------- network problems
add("network", "click-tests", [rep(T, INSTALL, "pip install --index-url https://pypi.corp.invalid/simple -e . pytest==9.0.2\n")],
    "Use the internal package mirror", "unreachable-index")
add("network", "click-tests", [rep(T, "- script: python -m pytest",
                                   "- script: curl -fsS --max-time 20 https://artifacts.corp.invalid/fixtures/sample-data.tar.gz -o fixtures.tar.gz\n"
                                   "        displayName: Download test fixtures\n      - script: python -m pytest")],
    "Download shared fixtures before the tests", "unreachable-host")
add("network", "click-tests", [new("tests/test_remote_defaults.py",
                                   'import urllib.request\n\n\ndef test_remote_defaults_are_reachable():\n'
                                   '    with urllib.request.urlopen("http://config.corp.invalid/v1/defaults", timeout=5) as response:\n'
                                   '        assert response.status == 200\n')],
    "Add a test for the remote defaults service", "test-needs-network")
add("network", "click-tests", [rep(T, INSTALL, "pip install --proxy http://127.0.0.1:3128 -e . pytest==9.0.2\n")],
    "Route installs through the build proxy", "refused-proxy")

# ---------------------------------------------------------------- permission problems
add("permissions", "click-tests", [rep(T, "- script: python -m pytest",
                                       '- script: echo "mode=ci" > /etc/click-ci.conf\n'
                                       "        displayName: Write CI settings\n      - script: python -m pytest")],
    "Write the CI settings file", "file-permission")
add("permissions", "click-tests", [rep(T, INSTALL, "pip install pytest==9.0.2 --target /usr/lib/python3/dist-packages\n")],
    "Install the test tools system wide", "protected-directory")
add("permissions", "click-package", [new("scripts/smoke.sh", '#!/usr/bin/env bash\nset -e\npython -c "import click; print(click.__version__)"\n'),
                                     rep(P, "- script: twine check dist/*", "- script: ./scripts/smoke.sh\n        displayName: Smoke script\n      - script: twine check dist/*")],
    "Move the smoke check into a script", "no-execute-bit")
add("permissions", "click-package", [rep(P, "    steps:\n", "    steps:\n      - checkout: self\n        persistCredentials: true\n"),
                                      rep(P, "  displayName: Smoke test the wheel\n",
                                          "  displayName: Smoke test the wheel\n"
                                          "      - script: git tag ci-$(Build.BuildId) && git push origin ci-$(Build.BuildId)\n"
                                          "        displayName: Tag the build\n")],
    "Tag every packaged build", "repository-permission")
add("permissions", "click-tests", [rep(T, "- script: |\n          python -m pip install --upgrade pip",
                                       "- task: PipAuthenticate@1\n        displayName: Authenticate to the package feed\n        inputs:\n          artifactFeeds: click-internal\n      - script: |\n          python -m pip install --upgrade pip")],
    "Authenticate to the internal package feed", "feed-access", ambiguous=True, note="feed not found vs not authorised")

# ---------------------------------------------------------------- timeouts
add("timeout", "click-tests", [rep("src/click/utils.py", "import typing as t\nfrom functools import update_wrapper", "import threading\nimport typing as t\nfrom functools import update_wrapper"),
                               rep("src/click/utils.py", 'R = t.TypeVar("R")\n', 'R = t.TypeVar("R")\n\n_echo_lock = threading.Lock()\n'),
                               rep("src/click/utils.py", "    if file is None:\n        if err:\n            file = _default_text_stderr()", "    _echo_lock.acquire()\n    if file is None:\n        if err:\n            file = _default_text_stderr()")],
    "Serialise output from several threads", "lock-never-released")
add("timeout", "click-tests", [new("tests/test_background_flush.py",
                                   "import threading\n\n\ndef test_background_flush_completes():\n    done = threading.Event()\n    done.wait()\n")],
    "Add a test for the background flush", "blocked-wait")
add("timeout", "click-tests", [rep(T, "- script: python -m pytest",
                                   "- script: until curl -fs http://localhost:8080/health; do sleep 2; done\n"
                                   "        displayName: Wait for the mock service\n      - script: python -m pytest")],
    "Wait for the mock service before the tests", "waiting-for-service")

# ---------------------------------------------------------------- lint violations
add("lint", "click-lint", [rep("src/click/utils.py", "import typing as t\nfrom functools import update_wrapper",
                               "import typing as t\nfrom functools import update_wrapper\nfrom shutil import get_terminal_size")],
    "Import the terminal size helper", "unused-import")
add("lint", "click-lint", [rep("src/click/utils.py", "    last_index = len(words) - 1\n",
                               "    last_index = len(words) - 1\n    spare_width = max_length * 2\n")],
    "Keep a spare width for later", "unused-variable")
add("lint", "click-lint", [app("src/click/utils.py", "\n\ndef _collect(item: str, bucket: list[str] = []) -> list[str]:\n    bucket.append(item)\n    return bucket\n")],
    "Add a collection helper", "mutable-default")
add("lint", "click-lint", [app("src/click/utils.py", "\n\ndef _safe_int(value: str) -> int:\n    try:\n        return int(value)\n    except:\n        return 0\n")],
    "Add a forgiving integer parser", "bare-except")
add("lint", "click-lint", [rep("src/click/exceptions.py", "    exit_code: t.ClassVar[int] = 2\n\n    ctx: Context | None",
                               "    exit_code: t.ClassVar[int] = 2  # exit code 2 is what shells and wrappers expect for a usage error, see the documentation\n\n    ctx: Context | None")],
    "Explain the usage error exit code", "line-too-long")
add("lint", "click-lint", [app("src/click/utils.py", "\n\ndef _quote(value: str) -> str:\n    return shlex.quote(value)\n")],
    "Add a quoting helper", "undefined-name")
add("lint", "click-lint", [app("src/click/utils.py", "\n\n_LIMITS = {'short': 45,'long':  80}\n")],
    "Record the help length limits", "formatting")
add("lint", "click-lint", [rep("src/click/formatting.py", "        col_spacing: int = 2,", "        col_spacing:int=2,")],
    "Compact the signature", "formatting")

# ---------------------------------------------------------------- type errors
add("type-error", "click-typecheck", [rep("src/click/utils.py", "def _make_default_short_help(help: str, max_length: int = 45) -> str:",
                                          "def _make_default_short_help(help: str, max_length: int = 45) -> int:")],
    "Correct the short help return annotation", "wrong-return-type")
add("type-error", "click-typecheck", [rep("src/click/utils.py", 'textwrap.shorten(text, max_length, placeholder="...", break_on_hyphens=False)',
                                          'textwrap.shorten(text, str(max_length), placeholder="...", break_on_hyphens=False)')],
    "Pass the width as text", "wrong-argument-type")
add("type-error", "click-typecheck", [app("src/click/utils.py", "\n\ndef _clamp_width(width: int) -> int:\n    if width > 0:\n        return width\n")],
    "Add a width clamp", "missing-return")
add("type-error", "click-typecheck", [app("src/click/utils.py", "\n\ndef _current_obj() -> object:\n    from .globals import get_current_context\n\n    ctx = get_current_context(silent=True)\n    return ctx.obj\n")],
    "Expose the current context object", "optional-misuse")
add("type-error", "click-typecheck", [app("src/click/utils.py", "\n\ndef _join(parts, sep=' '):\n    return sep.join(parts)\n")],
    "Add a join helper", "missing-annotation")

# ---------------------------------------------------------------- code errors that stop the tests running at all
add("code-error", "click-tests", [rep("src/click/utils.py", "    if len(text) <= max_length:\n        return text", "    if len(text) <= max_length\n        return text")],
    "Short help length check", "syntax-error")
add("code-error", "click-tests", [rep("src/click/__init__.py", "from .utils import echo as echo", "from .utils import echoo as echo")],
    "Re-export echo from utils", "bad-import-name")
add("code-error", "click-tests", [rep("src/click/utils.py", "R = t.TypeVar(\"R\")\n", "R = t.TypeVar(\"R\")\n\n_CACHE = _REGISTRY\n")],
    "Cache the registry at import time", "name-error")
add("code-error", "click-tests", [rep("src/click/exceptions.py", "from ._compat import get_text_stderr", "from .core import Context\nfrom ._compat import get_text_stderr")],
    "Import Context for the type hints", "circular-import")
add("code-error", "click-tests", [rep("src/click/parser.py", "    rv: list[str | tuple[str | T_UNSET, ...] | T_UNSET] = []\n",
                                      "    rv: list[str | tuple[str | T_UNSET, ...] | T_UNSET] = []\n      spare = None\n")],
    "Tidy the unpack helper", "indentation-error")

# ---------------------------------------------------------------- build and pipeline configuration mistakes
add("config", "click-tests", [rep("pyproject.toml", 'requires-python = ">=3.10"', 'requires-python = ">=3.10')],
    "Update project metadata", "invalid-toml")
add("config", "click-tests", [rep(T, "- script: python -m pytest -q --tb=short -p no:cacheprovider", "- script: python -m pytest -q --tb=short -p no:cacheprovider tests_unit")],
    "Point the tests at the unit test folder", "wrong-path")
add("config", "click-package", [rep("pyproject.toml", '[tool.flit.module]\nname = "click"', '[tool.flit.module]\nname = "clik"')],
    "Rename the module entry", "wrong-module-name")
add("config", "click-package", [rep("pyproject.toml", 'readme = "README.md"', 'readme = "README.rst"')],
    "Switch the readme format", "missing-file")
add("config", "click-package", [rep("pyproject.toml", 'license-files = ["LICENSE.txt"]', 'license-files = ["LICENSE.md"]')],
    "Point at the markdown licence", "missing-file")
add("config", "click-tests", [rep(T, "  displayName: Install click and test dependencies\n", "  displayName: Install click and test dependencies\n        workingDirectory: src\n")],
    "Run the install from the source folder", "wrong-working-directory")
