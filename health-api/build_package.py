"""Builds health-api.zip: the Function App code plus the agent modules the watcher needs.

Run from anywhere:  python health-api/build_package.py
Then deploy:        az functionapp deployment source config-zip -n pipemedic-api-16135 -g pipemedic-rg --src health-api/health-api.zip --build-remote true
"""
import hashlib
import os
import subprocess
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT = os.path.join(HERE, "..", "agent")
FUNCTION_FILES = ["function_app.py", "host.json", "requirements.txt"]
AGENT_MODULES = ["pipemedic_agent.py", "watcher.py", "diagnose.py", "flaky.py", "ai_diagnose.py", "llm.py", "remediate.py",
                 "pool_status.py", "predict.py", "azure_auth.py", "store.py", "notify.py", "ai_fix.py", "memory.py", "config.py", "chat_agent.py",
                 "pool_alerts.py", "runner_logs.py", "event_hook.py", "fixplan.py", "runner_actions.py", "fix_executor.py", "fix_review.py", "lookahead.py"]


def version_stamp() -> str:
    """A short id of exactly what is in the package: the code files' hash plus the git commit, so a deploy can be checked."""
    h = hashlib.sha256()
    for f in FUNCTION_FILES:
        h.update(open(os.path.join(HERE, f), "rb").read())
    for m in AGENT_MODULES:
        h.update(open(os.path.join(AGENT, m), "rb").read())
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=HERE, capture_output=True, text=True).stdout.strip()
    except OSError:
        commit = "nogit"
    return f"{commit or 'nogit'}-{h.hexdigest()[:10]}"


def main() -> str:
    out = os.path.join(HERE, "health-api.zip")
    stamp = version_stamp()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("version.txt", stamp)
        for f in FUNCTION_FILES:
            z.write(os.path.join(HERE, f), f)
        for m in AGENT_MODULES:
            z.write(os.path.join(AGENT, m), m)  # one entry per module
    print("built", out, stamp, "with", len(FUNCTION_FILES) + len(AGENT_MODULES), "files")
    return out


if __name__ == "__main__":
    main()
