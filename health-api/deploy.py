"""Deploys the Function App and proves the new code is the code running.

    python health-api/deploy.py [--app NAME] [--group RG] [--subscription SUB] [--store-key-in KEY_VAULT]

Builds the package (stamped with a version), uploads it, restarts the app, and then polls /api/version until it reports the
same stamp. An old worker can keep running for a while after an upload, so "the upload succeeded" is not proof; this is.
The app, group and subscription default to FUNCTION_APP, AZURE_RESOURCE_GROUP and AZURE_SUBSCRIPTION (or AZURE_SUBSCRIPTION_ID),
from the environment or agent/local.env.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

import build_package

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent"))
import config  # noqa: E402,F401  (reads agent/local.env into the environment)

APP = os.getenv("FUNCTION_APP", "")
GROUP = os.getenv("AZURE_RESOURCE_GROUP", "pipemedic-rg")


def az(*args: str, sub: str) -> str:
    cmd = ["az", *args, "--subscription", sub]
    r = subprocess.run(cmd, capture_output=True, text=True, shell=(sys.platform == "win32"), env={**os.environ, "MSYS_NO_PATHCONV": "1"})
    if r.returncode:
        sys.exit(f"az failed: {' '.join(args[:3])}\n{r.stderr[-400:]}")
    return r.stdout


def running_version() -> str:
    try:
        with urllib.request.urlopen(f"https://{APP}.azurewebsites.net/api/version", timeout=20) as r:  # noqa: S310
            return json.load(r).get("version", "")
    except Exception:
        return ""


def main() -> None:
    global APP, GROUP
    ap = argparse.ArgumentParser()
    ap.add_argument("--subscription", default=os.getenv("AZURE_SUBSCRIPTION") or os.getenv("AZURE_SUBSCRIPTION_ID"))
    ap.add_argument("--app", default=APP, help="Function App name (or FUNCTION_APP)")
    ap.add_argument("--group", default=GROUP, help="resource group (or AZURE_RESOURCE_GROUP)")
    ap.add_argument("--store-key-in", metavar="KEY_VAULT", help="also save the Function key as the runners' secret health-function-key in this vault")
    ap.add_argument("--wait", type=int, default=360, help="seconds to wait for the new version to appear")
    a = ap.parse_args()
    if not (a.app and a.subscription):
        sys.exit("Say which Function App and subscription: --app and --subscription, or FUNCTION_APP and AZURE_SUBSCRIPTION in agent/local.env.")
    APP, GROUP = a.app, a.group

    zip_path = build_package.main()
    want = build_package.version_stamp()
    print("uploading", want)
    az("functionapp", "deployment", "source", "config-zip", "-n", APP, "-g", GROUP, "--src", zip_path, "--build-remote", "true", sub=a.subscription)
    print("restarting")
    az("functionapp", "restart", "-n", APP, "-g", GROUP, sub=a.subscription)

    deadline = time.time() + a.wait
    while time.time() < deadline:
        got = running_version()
        print("  running:", got or "(not answering yet)")
        if got == want:
            print("OK: the new code is live:", want)
            if a.store_key_in:
                # The runners post their readings with this key; they fetch it from the vault with their own identity.
                key = az("functionapp", "keys", "list", "-n", APP, "-g", GROUP, "--query", "functionKeys.default", "-o", "tsv", sub=a.subscription).strip()
                az("keyvault", "secret", "set", "--vault-name", a.store_key_in, "--name", "health-function-key", "--value", key, "-o", "none",
                   sub=a.subscription)
                print("saved the Function key as health-function-key in", a.store_key_in)
            return
        time.sleep(15)
    sys.exit(f"The app still reports {got or 'nothing'}, not {want}. Wait a few minutes and run this again, or restart the app.")


if __name__ == "__main__":
    main()
