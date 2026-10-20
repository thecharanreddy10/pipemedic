"""Data the dashboard shows, loaded from Azure DevOps and the health API and cached for a short while.

Signing in: the Azure sign-in (az login) is used when it works, because it can list every organization a person belongs to.
Otherwise the token in AZDO_PAT is used, which only works for the organization it was made in.
"""
import os
import sys

import pandas as pd
import requests
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402
from diagnose import diagnose, summarize_health  # noqa: E402
from flaky import assess as assess_flaky  # noqa: E402
from pipemedic_agent import DEFAULT_HEALTH_API, AzureDevOps, collect_failure, fetch_health, list_orgs  # noqa: E402

cfg = config.settings()
HEALTH_API = os.getenv("HEALTH_API", DEFAULT_HEALTH_API)


@st.cache_resource(show_spinner=False)
def entra():
    from azure_auth import get_credential
    return get_credential()


@st.cache_data(ttl=300, show_spinner=False)
def entra_available() -> bool:
    try:
        from azure_auth import DEVOPS_SCOPE
        entra().get_token(DEVOPS_SCOPE)
        return True
    except Exception:
        return False


def auth() -> str:
    """"" means the Azure sign-in; anything else is the access token from AZDO_PAT."""
    return "" if entra_available() else os.getenv("AZDO_PAT", "")


def can_sign_in() -> bool:
    return entra_available() or bool(os.getenv("AZDO_PAT"))


def make_ado(org, project, token):
    return AzureDevOps(org, project, token) if token else AzureDevOps(org, project, credential=entra())


@st.cache_data(ttl=300, show_spinner=False)
def organizations(token):
    return [] if token else list_orgs(entra())  # an access token cannot list organizations


@st.cache_data(ttl=120, show_spinner=False)
def projects(org, token):
    return sorted(p["name"] for p in make_ado(org, "", token).projects())


@st.cache_data(ttl=120, show_spinner=False)
def repos(org, project, token):
    return sorted((r["name"], r["id"]) for r in make_ado(org, project, token).repos())


@st.cache_data(ttl=20, show_spinner=False)
def repo_runs(org, project, repo_id, token, top=100):
    """The newest runs of every pipeline built from this repository, and the pipelines' names."""
    ado = make_ado(org, project, token)
    defs = ado.definitions(repo_id)
    runs = ado.runs_for([d["id"] for d in defs], top) if defs else []
    return {d["id"]: d["name"] for d in defs}, runs


@st.cache_data(ttl=20, show_spinner=False)
def pipeline_runs(org, project, definition_id, token, top=50):
    """The newest runs of one pipeline."""
    return make_ado(org, project, token).runs_for([definition_id], top)


@st.cache_data(ttl=30, show_spinner=False)
def timeline(org, project, build_id, token):
    return make_ado(org, project, token).timeline(build_id)


@st.cache_data(ttl=120, show_spinner=False)
def agent_hosts(org, project, pool_name, token):
    try:
        return make_ado(org, project, token).agent_hosts(pool_name)
    except Exception:
        return {}


@st.cache_data(ttl=120, show_spinner=False)
def window_health(host, start, finish):
    try:
        return summarize_health(fetch_health(HEALTH_API, host, start), start, finish)
    except Exception:
        return {"samples": 0, "flags": ["no-health-data"]}


@st.cache_data(ttl=300, show_spinner=False)
def diagnosis(org, project, token, build, pool_name, flaky, with_runner_logs=False):
    """Why a run failed, worked out now (rules, runner health and optionally the runner's own logs)."""
    failure = collect_failure(make_ado(org, project, token), build, agent_hosts(org, project, pool_name, token))
    failure["flaky"] = flaky
    logs = None
    if with_runner_logs and failure["hostname"]:
        import runner_logs
        logs = runner_logs.collect(entra(), failure["hostname"], failure["start"], failure["finish"])
    return diagnose(failure, fetch_health(HEALTH_API, failure["hostname"], failure["start"]), logs)


def watched_project(org, project) -> bool:
    """True when this is the organization and project the always-on agent watches. Build ids repeat across projects,
    so another project's runs must never be matched with its saved analysis."""
    return (org.strip().lower(), project.strip().lower()) == (cfg.org.lower(), cfg.project.lower())


@st.cache_data(ttl=60, show_spinner=False)
def saved_diagnoses(org, project):
    """What the always-on agent worked out when each run failed, by build id. Only for the project the agent watches."""
    try:
        from store import DEFAULT_STATE_URL, TableStore
        if not watched_project(org, project):
            return {}
        return {x["build_id"]: x for x in TableStore(DEFAULT_STATE_URL, entra()).diagnoses(300)}
    except Exception:
        return {}


@st.cache_data(ttl=30, show_spinner=False)
def health_history(minutes):
    r = requests.get(f"{HEALTH_API}/api/health-history", params={"minutes": minutes}, timeout=60)
    r.raise_for_status()
    df = pd.DataFrame(r.json()["records"])
    if not df.empty:
        df["time"] = pd.to_datetime(df["timestamp_utc"], utc=True)
    return df


__all__ = ["cfg", "assess_flaky"]


# ---------------- fix plans ----------------
@st.cache_data(ttl=10, show_spinner=False)
def fix_plans(org, project, build_id):
    """The fixes PipeMedic offered for a run, with their current status. Only for the project the agent watches."""
    if not watched_project(org, project):
        return []
    from store import DEFAULT_STATE_URL, TableStore
    return TableStore(DEFAULT_STATE_URL, entra()).plans_for(build_id)


@st.cache_data(ttl=600, show_spinner=False)
def signed_in_user() -> str:
    """The Azure sign-in's user name, recorded as the approver. Empty when it cannot be read."""
    import base64
    import json as _json
    from azure_auth import ARM_SCOPE
    try:
        claims = entra().get_token(ARM_SCOPE).token.split(".")[1]
        c = _json.loads(base64.urlsafe_b64decode(claims + "=" * (-len(claims) % 4)))
        return c.get("upn") or c.get("unique_name") or c.get("preferred_username") or c.get("oid", "")
    except Exception:
        return ""


@st.cache_data(ttl=3600, show_spinner=False)
def _function_key() -> str:
    from azure_auth import ARM_SCOPE
    url = (f"https://management.azure.com/subscriptions/{cfg.subscription_id}/resourceGroups/{cfg.resource_group}/providers/"
           f"Microsoft.Web/sites/{cfg.function_app}/host/default/listKeys?api-version=2023-12-01")
    r = requests.post(url, headers={"Authorization": "Bearer " + entra().get_token(ARM_SCOPE).token}, timeout=30)
    r.raise_for_status()
    return r.json()["functionKeys"]["default"]


def decide_plan(build_id, plan_id, approve, who) -> tuple[bool, str]:
    """Send a decision to the agent in Azure, which records it once and carries out an approved plan."""
    r = requests.post(f"{HEALTH_API.rstrip('/')}/api/fix/decide", params={"code": _function_key()}, timeout=30,
                      json={"build_id": int(build_id), "plan_id": plan_id, "approve": bool(approve), "who": who})
    try:
        body = r.json()
    except ValueError:
        return False, f"The agent answered HTTP {r.status_code}."
    fix_plans.clear()
    return bool(body.get("ok")), body.get("message") or body.get("error") or f"HTTP {r.status_code}"
