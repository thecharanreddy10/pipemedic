"""Round 2: new tools and new ecosystems, run against a frozen classifier.

Round 1 taught the classifier from ruff, mypy and pytest output. Round 2 asks whether it copes with output it was never
shown: flake8, black, pylint, jest, eslint, tsc, npm and docker. Nothing here was used to write or adjust any rule.
"""
from catalog import PY, _job  # noqa: F401  (same YAML helpers as round 1)

NODE = '''- task: NodeTool@0
  displayName: Use Node 20
  inputs:
    versionSpec: "20.x"
'''


def _job2(name, title, steps, comment, setup=PY, minutes=5):
    body = "".join("      " + ln + "\n" if ln else "\n" for ln in steps.rstrip("\n").split("\n"))
    pre = "".join("      " + ln + "\n" for ln in setup.rstrip("\n").split("\n")) if setup else ""
    return (f"# {comment}\ntrigger: none\npr: none\n\npool:\n  vmImage: ubuntu-latest\n\njobs:\n  - job: {name}\n"
            f"    displayName: {title}\n    timeoutInMinutes: {minutes}\n    steps:\n" + pre + body)


QUALITY2_YML = _job2("quality2", "Style checks", """- script: |
    python -m pip install --upgrade pip
    pip install -e . flake8==7.4.1 black==26.10.1 pylint==4.1.3
  displayName: Install tools
- script: flake8 --select=F --exclude=src/click/__init__.py src
  displayName: flake8
- script: black --check src/click/utils.py src/click/exceptions.py src/click/formatting.py src/click/parser.py src/click/globals.py src/click/decorators.py
  displayName: black
- script: pylint --errors-only src/click/utils.py src/click/exceptions.py src/click/formatting.py src/click/parser.py
  displayName: pylint
""", "Second opinion on style with flake8, black and pylint.")

WEB_YML = _job2("web", "Web widget", """- script: npm install --no-audit --no-fund
  workingDirectory: web
  displayName: Install packages
- script: npx jest
  workingDirectory: web
  displayName: Test
- script: npx eslint src
  workingDirectory: web
  displayName: Lint
- script: npx tsc --noEmit -p .
  workingDirectory: web
  displayName: Type check
""", "Builds and tests the cart widget (Node).", setup=NODE)

CONTAINER_YML = _job2("container", "Container image", """- script: docker build -t click-demo:$(Build.BuildId) .
  displayName: Build image
- script: docker run --rm click-demo:$(Build.BuildId)
  displayName: Run image
""", "Builds the container image and runs it once.", setup="", minutes=8)

DOCKERFILE = """FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE.txt ./
COPY src ./src
RUN pip install --no-cache-dir .
CMD ["python", "-c", "import click; print(click.__version__)"]
"""

BASELINE2_FILES = {
    "/.azure-pipelines/quality2.yml": QUALITY2_YML,
    "/.azure-pipelines/web.yml": WEB_YML,
    "/.azure-pipelines/container.yml": CONTAINER_YML,
    "/Dockerfile": DOCKERFILE,
}
WEB_FILES = ["web/package.json", "web/eslint.config.js", "web/tsconfig.json", "web/src/cart.js", "web/src/cart.test.js", "web/src/price.ts"]

Q = "/.azure-pipelines/quality2.yml"
W = "/.azure-pipelines/web.yml"

D = []


def add(label, pipeline, edits, message, subtype="", ambiguous=False, note=""):
    D.append(dict(label=label, pipeline=pipeline, edits=edits, message=message, subtype=subtype, ambiguous=ambiguous, note=note))


def rep(path, old, new):
    return {"path": path, "old": old, "new": new}


def app(path, text):
    return {"path": path, "append": text}


def new(path, content):
    return {"path": path, "create": content}


# ------------------------------------------------------------------ Python style tools (flake8, black, pylint)
add("lint", "click-quality2", [rep("src/click/utils.py", "import typing as t\nfrom functools import update_wrapper", "import json\nimport typing as t\nfrom functools import update_wrapper")],
    "Import json for the debug helper", "flake8-unused-import")
add("lint", "click-quality2", [app("src/click/utils.py", "\n\ndef _join_path(parts: list[str]) -> str:\n    return os_path_join(*parts)\n")],
    "Add a path joining helper", "flake8-undefined-name")
add("lint", "click-quality2", [app("src/click/utils.py", "\n\n_LIMITS = {'short': 45,'long':  80}\n")],
    "Record the help length limits", "black-format")
add("lint", "click-quality2", [app("src/click/utils.py", "\n\ndef _shout() -> str:\n    return \"abc\".nothing()\n")],
    "Add a shouting helper", "pylint-no-member")
add("lint", "click-quality2", [app("src/click/utils.py", "\n\ndef _short() -> str:\n    return _make_default_short_help()\n")],
    "Add a default short help helper", "pylint-missing-argument")
add("code-error", "click-quality2", [rep("src/click/utils.py", "    if len(text) <= max_length:\n        return text", "    if len(text) <= max_length\n        return text")],
    "Short help length check", "black-syntax-error", ambiguous=True, note="a formatter cannot parse the file: lint or code error")
add("dependency", "click-quality2", [rep(Q, "pip install -e . flake8==7.4.1 black==26.10.1 pylint==4.1.3\n", "pip install -e . flake8==7.4.1 black==26.10.1 pylint==4.1.3 pycodestyle==2.5.0\n")],
    "Pin pycodestyle", "conflicting-pins")

# ------------------------------------------------------------------ Node project (jest, eslint, tsc, npm)
add("test-failure", "click-web", [rep("web/src/cart.js", "sum + item.price * item.qty", "sum + item.price")],
    "Simplify the total", "jest-assertion")
add("test-failure", "click-web", [rep("web/src/cart.test.js", '  test("applies a discount", () => {',
                                       '  test("waits for the price feed", async () => {\n    await new Promise((resolve) => setTimeout(resolve, 9000));\n  });\n\n  test("applies a discount", () => {')],
    "Add a test for the price feed", "jest-test-timeout")
add("code-error", "click-web", [rep("web/src/cart.js", "return [...cart, item];", "return [...cart, item;")],
    "Return the extended cart", "js-syntax-error")
add("code-error", "click-web", [rep("web/src/cart.test.js", 'require("./cart")', 'require("./cartt")')],
    "Load the cart helpers", "js-missing-relative-module")
add("dependency", "click-web", [rep("web/package.json", '"jest": "29.7.0"', '"jest": "29.7.99"')],
    "Pin jest", "npm-no-matching-version")
add("dependency", "click-web", [rep("web/package.json", '"typescript": "5.6.3"', '"typescript": "5.6.3",\n    "@acme-internal/ui-kit": "1.2.0"')],
    "Use the shared UI kit", "npm-package-not-found")
add("network", "click-web", [rep(W, "npm install --no-audit --no-fund", "npm install --no-audit --no-fund --registry=http://registry.corp.invalid/")],
    "Use the internal npm mirror", "npm-unreachable-registry")
add("lint", "click-web", [rep("web/src/cart.js", '"use strict";\n', '"use strict";\n\nconst unused = 1;\n')],
    "Add a default quantity", "eslint-unused-variable")
add("lint", "click-web", [rep("web/src/cart.js", "  return [...cart, item];", "  return [...cart, item]")],
    "Tidy the add helper", "eslint-missing-semicolon")
add("code-error", "click-web", [rep("web/src/price.ts", "return line.price * line.qty;", 'return line.price * line.qty + "";')],
    "Format the line total", "tsc-type-error", ambiguous=True, note="tsc is the compiler: compile error or type check")
add("config", "click-web", [rep("web/package.json", '"private": true,', '"private": true')],
    "Tidy package metadata", "invalid-package-json")
add("timeout", "click-web", [rep("web/src/cart.test.js", 'describe("cart", () => {', 'setInterval(() => {}, 1000);\n\ndescribe("cart", () => {')],
    "Poll the price feed in the background", "jest-open-handle")

# ------------------------------------------------------------------ container build (docker)
add("config", "click-container", [rep("Dockerfile", "COPY pyproject.toml README.md LICENSE.txt ./", "COPY pyproject.toml README.rst LICENSE.txt ./")],
    "Copy the rst readme", "docker-copy-missing-file")
add("dependency", "click-container", [rep("Dockerfile", "FROM python:3.12-slim", "FROM python:3.99-slim")],
    "Move to the newest Python image", "docker-image-tag-missing")
add("dependency", "click-container", [rep("Dockerfile", "RUN pip install --no-cache-dir .", "RUN pip install --no-cache-dir . click-extras-zzz")],
    "Install the extras", "pip-package-missing")
add("dependency", "click-container", [rep("Dockerfile", "RUN pip install --no-cache-dir .", "RUN apt-get update && apt-get install -y libnonexistent-dev\nRUN pip install --no-cache-dir .")],
    "Install the native headers", "apt-package-missing")
add("network", "click-container", [rep("Dockerfile", "RUN pip install --no-cache-dir .", "RUN pip install --no-cache-dir --index-url https://pypi.corp.invalid/simple .")],
    "Install from the internal mirror", "pip-unreachable-index")
add("config", "click-container", [rep("Dockerfile", "FROM python:3.12-slim", "FORM python:3.12-slim")],
    "Update the base image line", "dockerfile-syntax")
add("permissions", "click-container", [rep("Dockerfile", "FROM python:3.12-slim", "FROM acmecorp/private-python:3.12")],
    "Use the company base image", "pull-denied", ambiguous=True, note="a private image that does not exist or needs a login")
