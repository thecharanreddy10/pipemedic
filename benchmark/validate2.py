"""Check locally that each round-2 defect really fails the tool it is meant for (docker ones need the hosted agent)."""
import os
import shutil
import subprocess
import sys

import catalog2

ROOT = os.path.dirname(os.path.abspath(__file__))
SCRATCH = os.environ.get("BENCH_WORK", os.path.join(ROOT, "work"))
CLICK = os.path.join(SCRATCH, "click_src")
WEB = os.path.join(SCRATCH, "web_src")
PY = os.environ.get("BENCH_PY", os.path.join(SCRATCH, "venv", "Scripts", "python.exe"))
WEB_BACKUP = os.path.join(SCRATCH, "web_backup")


def apply(base, edits):
    for e in edits:
        path = os.path.join(base, e["path"])
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


def run(cmd, cwd, timeout=240, shell=False, env=None):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, shell=shell, env=env)
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip().splitlines()


def reset_click():
    subprocess.run(["git", "checkout", "--", "."], cwd=CLICK, capture_output=True)
    subprocess.run(["git", "clean", "-fdq"], cwd=CLICK, capture_output=True)


def main():
    only = set(sys.argv[1:])
    for i, d in enumerate(catalog2.D):
        if only and str(i) not in only:
            continue
        p = d["pipeline"]
        if p == "click-container":
            print(i, d["label"], d["subtype"], "-> hosted agent only")
            continue
        if p == "click-quality2":
            if any(e["path"].startswith("/") for e in d["edits"]):
                print(i, d["label"], d["subtype"], "-> hosted agent only")
                continue
            reset_click()
            apply(CLICK, d["edits"])
            cmds = {"flake8": [PY, "-m", "flake8", "--select=F", "--exclude=src/click/__init__.py", "src"],
                    "black": [PY, "-m", "black", "--check", "src/click/utils.py", "src/click/exceptions.py", "src/click/formatting.py",
                              "src/click/parser.py", "src/click/globals.py", "src/click/decorators.py"],
                    "pylint": [PY, "-m", "pylint", "--errors-only", "src/click"]}
            bad = []
            for name, cmd in cmds.items():
                rc, out = run(cmd, CLICK)
                if rc != 0 and not (name == "pylint" and rc == 4 and "termios" in " ".join(out)):
                    bad.append((name, rc, [ln[:110] for ln in out[-3:]]))
            reset_click()
            print(i, d["label"], d["subtype"], "->", bad if bad else "PASSES (not a defect)")
            continue

        # web project: edit in place with a backup, run the tools from the pre-installed node_modules
        webdir = os.path.join(WEB, "web")
        if os.path.exists(WEB_BACKUP):
            shutil.rmtree(WEB_BACKUP)
        shutil.copytree(webdir, WEB_BACKUP, ignore=shutil.ignore_patterns("node_modules"))
        try:
            apply(WEB, d["edits"]) if not any(e["path"].startswith("/") for e in d["edits"]) else None
            if any(e["path"].startswith("/") for e in d["edits"]):
                print(i, d["label"], d["subtype"], "-> hosted agent only (pipeline YAML change)")
                continue
            sub = d["subtype"]
            if sub == "npm-unreachable-registry":
                continue
            if sub in ("npm-no-matching-version", "npm-package-not-found", "invalid-package-json"):
                rc, out = run("npm install --no-audit --no-fund --dry-run", webdir, shell=True)
                print(i, d["label"], sub, "->", "FAILS" if rc else "PASSES", [ln[:130] for ln in out[-3:]])
                continue
            results = []
            for name, cmd in [("eslint", "npx eslint src"), ("jest", "npx jest --forceExit"), ("tsc", "npx tsc --noEmit -p .")]:
                try:
                    rc, out = run(cmd, webdir, shell=True, timeout=60)
                except subprocess.TimeoutExpired:
                    rc, out = 124, ["(timed out)"]
                if rc != 0:
                    results.append((name, rc, [ln[:110] for ln in out[-3:]]))
            print(i, d["label"], sub, "->", results if results else "PASSES (not a defect)")
        finally:
            for f in os.listdir(WEB_BACKUP):
                src = os.path.join(WEB_BACKUP, f)
                dst = os.path.join(webdir, f)
                if os.path.isdir(src):
                    shutil.rmtree(dst, ignore_errors=True)
                    shutil.copytree(src, dst)
                else:
                    shutil.copy(src, dst)


if __name__ == "__main__":
    main()
