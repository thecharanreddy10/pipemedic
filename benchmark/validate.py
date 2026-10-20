"""Apply each code-level defect to a local copy of click and check that the matching tool really fails.

Only validates that a defect is a real failure; it does not look at the classifier. YAML-only defects need the hosted agent.
"""
import json
import os
import subprocess
import sys

import catalog

ROOT = os.path.dirname(os.path.abspath(__file__))
WORK = os.environ.get("BENCH_WORK", os.path.join(ROOT, "work"))
SRC = os.path.join(WORK, "click_src")
PY = os.environ.get("BENCH_PY", os.path.join(WORK, "venv", "Scripts", "python.exe"))

COMMANDS = {
    "click-tests": [PY, "-m", "pytest", "-q", "--tb=line", "-p", "no:cacheprovider", "-x", "--maxfail=1"],
    "click-lint": None,
    "click-typecheck": [PY, "-m", "mypy", "--no-pretty"],
    "click-package": None,
}


def git(*args):
    return subprocess.run(["git", *args], cwd=SRC, capture_output=True, text=True)


def apply(edits):
    for e in edits:
        path = os.path.join(SRC, e["path"])
        if "create" in e:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "w", encoding="utf-8", newline="\n").write(e["create"])
            continue
        text = open(path, encoding="utf-8").read()
        if "append" in e:
            text += e["append"]
        else:
            assert text.count(e["old"]) == 1, (e["path"], text.count(e["old"]), e["old"][:60])
            text = text.replace(e["old"], e["new"], 1)
        open(path, "w", encoding="utf-8", newline="\n").write(text)


def run(i, d):
    if any(e["path"].startswith("/.azure") for e in d["edits"]):
        return "yaml-only"
    git("checkout", "--", ".")
    git("clean", "-fdq")
    try:
        apply(d["edits"])
    except AssertionError as exc:
        return f"EDIT FAILED {exc}"
    outs = []
    p = d["pipeline"]
    if p == "click-lint":
        for cmd in ([PY, "-m", "ruff", "check", "--no-fix", "src", "tests"], [PY, "-m", "ruff", "format", "--check", "src", "tests"]):
            r = subprocess.run(cmd, cwd=SRC, capture_output=True, text=True)
            outs.append((r.returncode, (r.stdout + r.stderr).strip().splitlines()[-3:]))
        failed = any(rc != 0 for rc, _ in outs)
    elif p == "click-typecheck":
        r = subprocess.run(COMMANDS[p], cwd=SRC, capture_output=True, text=True, timeout=300)
        out = (r.stdout + r.stderr).strip().splitlines()
        base = [ln for ln in out if "Found" in ln]
        outs.append((r.returncode, [ln for ln in out if "error" in ln][-9:] + base))
        failed = r.returncode != 0
    elif p == "click-package":
        return "package-only"
    else:
        cmd = list(COMMANDS[p])
        if d["subtype"] in ("lock-never-released", "blocked-wait"):
            try:
                r = subprocess.run(cmd, cwd=SRC, capture_output=True, text=True, timeout=40)
                outs.append((r.returncode, (r.stdout + r.stderr).strip().splitlines()[-3:]))
                failed = False
            except subprocess.TimeoutExpired:
                return "HANGS (expected)"
        else:
            r = subprocess.run(cmd, cwd=SRC, capture_output=True, text=True, timeout=300)
            outs.append((r.returncode, (r.stdout + r.stderr).strip().splitlines()[-4:]))
            failed = r.returncode != 0
    git("checkout", "--", ".")
    git("clean", "-fdq")
    return ("FAILS" if failed else "PASSES (not a defect)") + " | " + " || ".join(" / ".join(x[1])[:300] for x in outs)


if __name__ == "__main__":
    only = set(sys.argv[1:])
    for i, d in enumerate(catalog.D):
        if only and str(i) not in only:
            continue
        print(i, d["label"], d["subtype"], "->", run(i, d)[:420], flush=True)
    git("checkout", "--", ".")
    git("clean", "-fdq")
