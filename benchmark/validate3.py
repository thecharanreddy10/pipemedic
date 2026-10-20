"""Check locally the round-3 defects whose tools are installed here (.NET SDK, mypy, ruff, pytest). Go and Maven need the agent."""
import os
import shutil
import subprocess
import sys

import catalog3
from validate2 import apply, reset_click, run  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
SCRATCH = os.environ.get("BENCH_WORK", os.path.join(ROOT, "work"))
CLICK = os.path.join(SCRATCH, "click_src")
R3 = os.path.join(ROOT, "baseline_r3")
PY = os.environ.get("BENCH_PY", os.path.join(SCRATCH, "venv", "Scripts", "python.exe"))
ENV = dict(os.environ, DOTNET_CLI_TELEMETRY_OPTOUT="1", DOTNET_NOLOGO="1", PYTHONIOENCODING="utf-8")


def main():
    only = set(sys.argv[1:])
    for i, d in enumerate(catalog3.D):
        if only and str(i) not in only:
            continue
        p = d["pipeline"]
        label = f"{i} {d['label']} {d['subtype']}"
        if p == "click-dotnet":
            if any(e["path"].startswith("/") for e in d["edits"]):
                print(label, "-> agent only (pipeline YAML change)")
                continue
            work = os.path.join(SCRATCH, "dotnet_work")
            shutil.rmtree(work, ignore_errors=True)
            shutil.copytree(os.path.join(R3, "dotnet"), work, ignore=shutil.ignore_patterns("bin", "obj"))
            apply(os.path.dirname(work) + os.sep + "dotnet_work_root" if False else SCRATCH, [dict(e, path="dotnet_work/" + e["path"][len("dotnet/"):]) for e in d["edits"]])
            steps = [("restore", "dotnet restore Cart.Tests/Cart.Tests.csproj"), ("build", "dotnet build Cart.Tests/Cart.Tests.csproj --no-restore"),
                     ("test", "dotnet test Cart.Tests/Cart.Tests.csproj --no-build"), ("format", "dotnet format Cart/Cart.csproj --verify-no-changes")]
            for name, cmd in steps:
                rc, out = run(cmd, work, shell=True, timeout=300, env=ENV)
                if rc != 0:
                    print(label, "-> FAILS at", name, [ln[:150] for ln in out[-3:]])
                    break
            else:
                print(label, "-> PASSES (not a defect)")
            shutil.rmtree(work, ignore_errors=True)
        elif p in ("click-typecheck", "click-lint", "click-tests") and not any(e["path"].startswith("/") for e in d["edits"]):
            reset_click()
            apply(CLICK, d["edits"])
            cmd = {"click-typecheck": [PY, "-m", "mypy", "--no-pretty"],
                   "click-lint": [PY, "-m", "ruff", "check", "--no-fix", "src", "tests"],
                   "click-tests": [PY, "-m", "pytest", "-q", "--tb=line", "-p", "no:cacheprovider", "-x"]}[p]
            rc, out = run(cmd, CLICK, timeout=300)
            reset_click()
            print(label, "->", "FAILS" if rc else "PASSES (not a defect)", [ln[:120] for ln in out[-3:]] if rc else "")
        else:
            print(label, "-> agent only")


if __name__ == "__main__":
    main()
