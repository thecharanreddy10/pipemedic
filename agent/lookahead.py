"""Other problems in a failed run that the pipeline never reached.

A pipeline stops at its first error, so later steps never run and their problems stay hidden until the next run. This module
lists the steps that did not run, and checks the files the failed commit changed since the pipeline last passed, with checks
that give evidence rather than guesses: a pinned package version that does not exist on PyPI, a base image tag that does not
exist on Docker Hub, a container registry the pipeline names that does not exist in Azure, a Python or JSON file that does not
parse. These are reported next to the diagnosis; the fix plan still deals only with the first failure.
"""
import ast
import json
import re

MAX_FILES = 20
_SETUP_STEPS = re.compile(r"^(Initialize job|Finalize Job|Get the code|Checkout|Post-job:|Pre-job:)", re.IGNORECASE)


def not_reached(records: list[dict]) -> list[str]:
    """Stages, and steps of the jobs that did run, that were skipped because an earlier step failed."""
    stages = [r for r in records if r.get("type") == "Stage" and r.get("result") == "skipped"]
    tasks = [r for r in records if r.get("type") == "Task" and r.get("result") == "skipped" and not _SETUP_STEPS.match(r.get("name", ""))]
    ordered = sorted(stages, key=lambda r: r.get("order") or 0) + sorted(tasks, key=lambda r: r.get("order") or 0)
    out = []
    for r in ordered:
        label = f"{r['name']} (stage)" if r["type"] == "Stage" else r["name"]
        if label not in out:
            out.append(label)
    return out[:12]


# ---------------- the checks: each returns findings {file, line, message, token} ----------------
_PIN = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9_.\-]*)(?:\[[^\]]*\])?\s*==\s*([A-Za-z0-9_.+\-!]+)\s*(?:[;#].*)?$")


def check_requirements(path: str, text: str, pypi_exists) -> list[dict]:
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        m = _PIN.match(line)
        if m and pypi_exists(m.group(1), m.group(2)) is False:
            out.append({"file": path, "line": n, "token": f"{m.group(1)}=={m.group(2)}",
                        "message": f"{m.group(1)} {m.group(2)} does not exist on PyPI, so installing the requirements will fail"})
    return out


_FROM = re.compile(r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)", re.IGNORECASE)


def docker_hub_ref(image: str) -> tuple[str, str, str] | None:
    """(namespace, repository, tag) of a Docker Hub image, or None for another registry, a digest, or a build argument."""
    if "$" in image or "@" in image or image.lower() == "scratch":
        return None
    name, _, tag = image.rpartition(":") if ":" in image.split("/")[-1] else (image, "", "latest")
    parts = name.split("/")
    if len(parts) > 1 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        if parts[0] not in ("docker.io", "registry-1.docker.io"):
            return None
        parts = parts[1:]
    ns, repo = ("library", parts[0]) if len(parts) == 1 else (parts[0], "/".join(parts[1:]))
    return ns, repo, tag or "latest"


def check_dockerfile(path: str, text: str, hub_exists) -> list[dict]:
    out, stages = [], set()
    for n, line in enumerate(text.splitlines(), 1):
        m = _FROM.match(line)
        if not m:
            continue
        image = m.group(1)
        alias = re.search(r"\s+AS\s+(\S+)", line, re.IGNORECASE)
        if image in stages:  # FROM an earlier build stage
            continue
        if alias:
            stages.add(alias.group(1))
        ref = docker_hub_ref(image)
        if ref and hub_exists(*ref) is False:
            out.append({"file": path, "line": n, "token": image,
                        "message": f"the base image {image} does not exist on Docker Hub, so building the image will fail"})
    return out


_REGISTRY = re.compile(r"(?:\bACR_NAME\s*:\s*['\"]?([a-z0-9]{5,50})['\"]?)|\b([a-z0-9]{5,50})\.azurecr\.io\b")


def check_pipeline(path: str, text: str, registry_exists) -> list[dict]:
    out, seen = [], set()
    for n, line in enumerate(text.splitlines(), 1):
        for m in _REGISTRY.finditer(line):
            name = m.group(1) or m.group(2)
            if name in seen:
                continue
            seen.add(name)
            if registry_exists(name) is False:
                out.append({"file": path, "line": n, "token": name,
                            "message": f"no container registry named '{name}' exists in Azure, so signing in to it or pushing to it will fail"})
    return out


def check_syntax(path: str, text: str) -> list[dict]:
    try:
        if path.endswith(".py"):
            ast.parse(text)
        elif path.endswith(".json"):
            json.loads(text)
    except SyntaxError as e:
        return [{"file": path, "line": e.lineno or 0, "token": path, "message": f"{path} is not valid Python: {e.msg}"}]
    except ValueError as e:
        return [{"file": path, "line": getattr(e, "lineno", 0), "token": path, "message": f"{path} is not valid JSON: {str(e)[:80]}"}]
    return []


def check_file(path: str, text: str, pypi_exists, hub_exists, registry_exists) -> list[dict]:
    name = path.split("/")[-1]
    if re.match(r"requirements.*\.txt$", name):
        return check_requirements(path, text, pypi_exists)
    if name == "Dockerfile" or name.endswith(".Dockerfile") or name.startswith("Dockerfile."):
        return check_dockerfile(path, text, hub_exists)
    if path.endswith((".yml", ".yaml")):
        return check_pipeline(path, text, registry_exists)
    if path.endswith((".py", ".json")):
        return check_syntax(path, text)
    return []


def look_ahead(changed: list[str], read, failure_text: str, pypi_exists, hub_exists, registry_exists) -> list[dict]:
    """Check the changed files; leave out what the failure itself already reports."""
    findings = []
    for path in changed[:MAX_FILES]:
        try:
            text = read(path)
        except Exception:
            continue
        findings += check_file(path, text, pypi_exists, hub_exists, registry_exists)
    return [f for f in findings if f["token"] not in failure_text]


# ---------------- the real lookups (None means "could not tell", which never produces a finding) ----------------
def pypi_exists(name: str, version: str, timeout_s: int = 10) -> bool | None:
    import requests
    try:
        r = requests.get(f"https://pypi.org/pypi/{name}/{version}/json", timeout=timeout_s)
    except requests.RequestException:
        return None
    return True if r.status_code == 200 else False if r.status_code == 404 else None


def hub_exists(namespace: str, repo: str, tag: str, timeout_s: int = 10) -> bool | None:
    import requests
    try:
        r = requests.get(f"https://hub.docker.com/v2/namespaces/{namespace}/repositories/{repo}/tags/{tag}", timeout=timeout_s)
    except requests.RequestException:
        return None
    return True if r.status_code == 200 else False if r.status_code == 404 else None
