"""PipeMedic dashboard.

Run:  streamlit run dashboard.py
Live runs need AZDO_PAT (or enter it under Settings); without it the sample runs are shown.
Runner health always comes from the live health API.
"""
import html
import os
import sys
from datetime import datetime, timedelta, timezone

import altair as alt
import pandas as pd
import requests
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402
import sample_data  # noqa: E402
from ai_diagnose import ai_diagnose  # noqa: E402
from chat_agent import ask as ask_pipemedic  # noqa: E402
from diagnose import diagnose  # noqa: E402
from llm import AIUnavailable, ai_enabled, get_llm  # noqa: E402
from flaky import assess as assess_flaky  # noqa: E402
from history import availability, bucket_minutes, duration_text, silent_periods  # noqa: E402
from ado_browse import (agents_involved, branch_name, duration_label, job_rows, pipeline_rows, seconds_between,  # noqa: E402
                        status_of, step_rows)
from diagnose import summarize_health  # noqa: E402
from pipemedic_agent import DEFAULT_HEALTH_API, AzureDevOps, collect_failure, fetch_health, list_orgs  # noqa: E402
from pool_status import analyze_pool  # noqa: E402
from predict import analyze_health  # noqa: E402
from remediate import AUTO_ACTIONS, MAX_AUTO_PER_HOUR, load_history  # noqa: E402

st.set_page_config(page_title="PipeMedic", page_icon="🩺", layout="wide", initial_sidebar_state="collapsed")

# ------------------------------------------------------------------ design tokens
THEME = (getattr(st.context, "theme", None) and st.context.theme.type) or "light"
DARK = THEME == "dark"
T = {
    "surface": "#1a1a19" if DARK else "#fcfcfb",
    "plane": "#0d0d0d" if DARK else "#f9f9f7",
    "ink": "#f0efec" if DARK else "#0b0b0b",
    "ink2": "#c3c2b7" if DARK else "#52514e",
    "muted": "#898781",
    "grid": "#2c2c2a" if DARK else "#e1e0d9",
    "axis": "#383835" if DARK else "#c3c2b7",
    "border": "rgba(255,255,255,0.10)" if DARK else "rgba(11,11,11,0.10)",
    "chip": "rgba(255,255,255,0.06)" if DARK else "rgba(11,11,11,0.05)",
    "series": ["#3987e5", "#d95926", "#199e70", "#c98500"] if DARK else ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"],
    # status colours are fixed in both modes and always travel with an icon and a label
    "good": "#0ca30c", "warning": "#fab219", "critical": "#d03b3b",
    "good_text": "#0ca30c" if DARK else "#006300",
}
VERDICT = {  # label, dot colour (identity, not status)
    "runner": ("Runner", T["series"][0]),
    "code-or-config": ("Code / config", "#9085e9" if DARK else "#6250d6"),
    "flaky": ("Flaky test", "#199e70" if DARK else "#1baf7a"),
    "mixed": ("Mixed", "#d55181" if DARK else "#e87ba4"),
    "unknown": ("Unknown", T["muted"]),
}
LEVEL = {"critical": ("✕", "Critical", T["critical"]), "warning": ("!", "Warning", T["warning"]),
         "info": ("i", "Info", T["muted"]), "good": ("✓", "OK", T["good"])}
FONT = "system-ui, -apple-system, 'Segoe UI', sans-serif"

st.markdown(f"""<style>
:root {{ --pm-surface:{T['surface']}; --pm-ink:{T['ink']}; --pm-ink2:{T['ink2']}; --pm-muted:{T['muted']};
  --pm-border:{T['border']}; --pm-chip:{T['chip']}; }}
html, body, [class*="css"], .stApp {{ font-family:{FONT}; }}
.block-container {{ padding-top:4.25rem; padding-bottom:3rem; max-width:1280px; }}
h1,h2,h3 {{ letter-spacing:-0.01em; }}
.pm-title {{ font-size:28px; font-weight:650; line-height:1.15; margin:0; color:var(--pm-ink); }}
.pm-sub {{ font-size:14px; color:var(--pm-ink2); margin-top:4px; }}
.pm-section {{ font-size:17px; font-weight:600; color:var(--pm-ink); margin:28px 0 4px 0; }}
.pm-hint {{ font-size:13px; color:var(--pm-muted); margin:0 0 12px 0; }}
.pm-badge {{ display:inline-flex; align-items:center; gap:6px; font-size:12.5px; color:var(--pm-ink2);
  background:var(--pm-chip); border-radius:999px; padding:4px 10px; }}
.pm-card {{ background:var(--pm-surface); border:1px solid var(--pm-border); border-radius:12px; padding:16px 18px; min-height:142px; }}
.pm-card.tall {{ min-height:160px; }}
.pm-label {{ font-size:13px; color:var(--pm-ink2); }}
.pm-value {{ font-size:30px; font-weight:600; line-height:1.15; margin-top:6px; color:var(--pm-ink); }}
.pm-note {{ font-size:12.5px; color:var(--pm-muted); margin-top:6px; }}
.pm-banner {{ display:flex; gap:14px; align-items:center; background:var(--pm-surface); border:1px solid var(--pm-border);
  border-left-width:4px; border-radius:12px; padding:16px 20px; margin:18px 0 6px 0; }}
.pm-icon {{ width:34px; height:34px; border-radius:50%; display:flex; align-items:center; justify-content:center;
  font-weight:700; font-size:16px; color:#fff; flex:none; }}
.pm-banner-title {{ font-size:18px; font-weight:600; color:var(--pm-ink); }}
.pm-banner-sub {{ font-size:13.5px; color:var(--pm-ink2); margin-top:2px; }}
.pm-pill {{ display:inline-flex; align-items:center; gap:7px; font-size:12.5px; font-weight:500; color:var(--pm-ink);
  background:var(--pm-chip); border-radius:999px; padding:3px 10px 3px 8px; white-space:nowrap; }}
.pm-dot {{ width:9px; height:9px; border-radius:50%; display:inline-block; flex:none; }}
.pm-sev {{ display:inline-flex; align-items:center; gap:6px; font-size:12.5px; font-weight:600; color:var(--pm-ink); white-space:nowrap; }}
.pm-sev i {{ font-style:normal; width:18px; height:18px; border-radius:50%; display:inline-flex; align-items:center;
  justify-content:center; font-size:11px; color:#fff; }}
.pm-item {{ background:var(--pm-surface); border:1px solid var(--pm-border); border-radius:12px; padding:14px 18px; margin-bottom:10px; }}
.pm-item-title {{ font-size:14.5px; color:var(--pm-ink); margin:6px 0 4px 0; line-height:1.4; }}
.pm-item-action {{ font-size:13.5px; color:var(--pm-ink2); line-height:1.4; }}
.pm-meta {{ font-size:12.5px; color:var(--pm-muted); }}
.pm-row-title {{ font-size:15px; font-weight:600; color:var(--pm-ink); }}
.pm-cause {{ font-size:14px; color:var(--pm-ink2); line-height:1.5; margin-top:8px; margin-bottom:12px; }}
.pm-stats {{ font-size:13px; color:var(--pm-ink2); line-height:1.7; }}
.pm-stats b {{ color:var(--pm-ink); font-weight:600; }}
div[data-testid="stTabs"] button {{ font-size:14.5px; }}
@media (max-width: 720px) {{ .pm-value {{ font-size:26px; }} .pm-card, .pm-card.tall {{ min-height:0; }}
  .block-container {{ padding-left:1rem; padding-right:1rem; }} }}
</style>""", unsafe_allow_html=True)


# ------------------------------------------------------------------ helpers
def esc(x) -> str:
    return html.escape(str(x))


def card(label, value, note=""):
    n = f'<div class="pm-note">{esc(note)}</div>' if note else ""
    return f'<div class="pm-card"><div class="pm-label">{esc(label)}</div><div class="pm-value">{esc(value)}</div>{n}</div>'


def sev(level):
    icon, label, colour = LEVEL[level]
    return f'<span class="pm-sev"><i style="background:{colour}">{icon}</i>{label}</span>'


def pill(verdict):
    label, colour = VERDICT.get(verdict, VERDICT["unknown"])
    return f'<span class="pm-pill"><span class="pm-dot" style="background:{colour}"></span>{esc(label)}</span>'


def section(title, hint=""):
    st.markdown(f'<div class="pm-section">{esc(title)}</div>' + (f'<div class="pm-hint">{esc(hint)}</div>' if hint else ""),
                unsafe_allow_html=True)


def local_time(series: pd.Series) -> pd.Series:
    return series.dt.tz_convert(datetime.now().astimezone().tzinfo).dt.tz_localize(None)


def style(chart: alt.Chart) -> alt.Chart:
    return (chart.configure(background="transparent", font=FONT)
            .configure_view(stroke=None)
            .configure_axis(labelColor=T["muted"], titleColor=T["muted"], gridColor=T["grid"], domainColor=T["axis"],
                            tickColor=T["axis"], labelFontSize=12, titleFontSize=12, gridWidth=1)
            .configure_legend(labelColor=T["ink2"], titleColor=T["ink2"], labelFontSize=12, orient="top",
                              padding=0, symbolType="stroke", symbolStrokeWidth=2.5, columns=1, labelLimit=260,
                              rowPadding=2))


def table_twin(df: pd.DataFrame, label="View as table"):
    with st.expander(label):
        st.dataframe(df, hide_index=True, width="stretch")


# ------------------------------------------------------------------ data
@st.cache_data(ttl=30, show_spinner=False)
def load_health(api_url: str, window: int) -> pd.DataFrame:
    r = requests.get(f"{api_url}/api/health-history", params={"minutes": window}, timeout=30)
    r.raise_for_status()
    df = pd.DataFrame(r.json()["records"])
    if not df.empty:
        df["time"] = pd.to_datetime(df["timestamp_utc"], utc=True)
    return df


def watched_project(org_: str, project_: str) -> bool:
    """True when this is the organization and project the always-on watcher is configured for."""
    import config as _config
    c = _config.settings()
    return (org_.strip().lower(), project_.strip().lower()) == (c.org.lower(), c.project.lower())


@st.cache_resource(show_spinner=False)
def entra():
    from azure_auth import get_credential
    return get_credential()


@st.cache_data(ttl=300, show_spinner=False)
def entra_available() -> bool:
    """True when the Azure sign-in (az login) can get an Azure DevOps token."""
    try:
        from azure_auth import DEVOPS_SCOPE
        entra().get_token(DEVOPS_SCOPE)
        return True
    except Exception:
        return False


def make_ado(org_, project_, token):
    """A token means a personal access token; an empty one means the Azure sign-in."""
    return AzureDevOps(org_, project_, token) if token else AzureDevOps(org_, project_, credential=entra())


@st.cache_data(ttl=300, show_spinner=False)
def list_organizations(token):
    return [] if token else list_orgs(entra())  # a personal access token cannot list organizations


@st.cache_data(ttl=120, show_spinner=False)
def list_projects(org_, token):
    return sorted(p["name"] for p in make_ado(org_, "", token).projects())


@st.cache_data(ttl=120, show_spinner=False)
def list_repos(org_, project_, token):
    return sorted((r["name"], r["id"]) for r in make_ado(org_, project_, token).repos())


@st.cache_data(ttl=120, show_spinner=False)
def list_pools(org_, token):
    """Pool names, with the ones you run yourself before the Microsoft hosted ones."""
    return [n for n, _ in sorted(((p["name"], bool(p.get("isHosted"))) for p in make_ado(org_, "", token).pools()),
                                 key=lambda x: (x[1], x[0].lower()))]


@st.cache_data(ttl=30, show_spinner=False)
def load_pipelines(org_, project_, repo_id, token):
    ado = make_ado(org_, project_, token)
    defs = ado.definitions(repo_id)
    return defs, (ado.runs_for([d["id"] for d in defs], 200) if defs else [])


@st.cache_data(ttl=30, show_spinner=False)
def load_runs(org_, project_, definition_id, token):
    return make_ado(org_, project_, token).runs_for([definition_id], 50)


@st.cache_data(ttl=30, show_spinner=False)
def load_timeline(org_, project_, build_id, token):
    return make_ado(org_, project_, token).timeline(build_id)


@st.cache_data(ttl=120, show_spinner=False)
def load_hosts(org_, project_, pool_name, token):
    try:
        return make_ado(org_, project_, token).agent_hosts(pool_name)
    except Exception:
        return {}


@st.cache_data(ttl=120, show_spinner=False)
def run_health(api_url, host, start, finish):
    try:
        return summarize_health(fetch_health(api_url, host, start), start, finish)
    except Exception:
        return {"samples": 0, "flags": ["no-health-data"]}


@st.cache_data(ttl=300, show_spinner=False)
def run_diagnosis(org_, project_, token, build, pool_name, api_url, flaky, with_runner_logs=False):
    failure = collect_failure(make_ado(org_, project_, token), build, load_hosts(org_, project_, pool_name, token))
    failure["flaky"] = flaky
    logs = None
    if with_runner_logs and failure["hostname"]:
        import runner_logs
        logs = runner_logs.collect(entra(), failure["hostname"], failure["start"], failure["finish"])
    return diagnose(failure, fetch_health(api_url, failure["hostname"], failure["start"]), logs)


@st.cache_data(ttl=60, show_spinner=False)
def saved_diagnoses(org_, project_):
    """What the always-on agent worked out when each run failed (with the runner logs and the AI opinion), keyed by build id.
    Only for the project the agent watches: build ids repeat across projects."""
    try:
        from store import DEFAULT_STATE_URL, TableStore
        if not watched_project(org_, project_):
            return {}
        return {x["build_id"]: x for x in TableStore(DEFAULT_STATE_URL, entra()).diagnoses(200)}
    except Exception:
        return {}


@st.cache_data(ttl=30, show_spinner=False)
def load_live(org_, project_, pool_, api_url, token, n):
    ado = make_ado(org_, project_, token)
    hosts = ado.agent_hosts(pool_)
    builds = ado.recent_builds(n)
    rows = [{"id": b["id"], "pipeline": b["definition"]["name"], "result": b.get("result"),
             "start": b.get("startTime"), "finish": b.get("finishTime")} for b in builds]
    diags = []
    for b in [b for b in builds if b.get("result") in ("failed", "canceled")][:10]:
        failure = collect_failure(ado, b, hosts)
        failure["flaky"] = assess_flaky(b, builds)
        diags.append(diagnose(failure, fetch_health(api_url, failure["hostname"], failure["start"])))
    # Add what the always-on watcher recorded (seen-before notes, AI fixes), when this account can read it.
    # Only for the project the watcher watches: build numbers repeat across projects, so another project's runs must not pick these up.
    try:
        from azure_auth import get_credential
        from store import DEFAULT_STATE_URL, TableStore
        if not watched_project(org_, project_):
            return rows, diags
        saved = {x["build_id"]: x for x in TableStore(DEFAULT_STATE_URL, get_credential()).diagnoses(100)}
        for d in diags:
            extra = saved.get(d["build_id"], {})
            for key in ("similar", "fix"):
                if extra.get(key):
                    d[key] = extra[key]
    except Exception:
        pass
    return rows, diags


@st.cache_data(ttl=300, show_spinner=False)
def investigate_older(org_, project_, pool_, api_url, token, n, skip_ids):
    """Diagnose the failed runs load_live left out (it stops at the 10 newest), the same way."""
    ado = make_ado(org_, project_, token)
    hosts = ado.agent_hosts(pool_)
    builds = ado.recent_builds(n)
    out = []
    for b in [b for b in builds if b.get("result") in ("failed", "canceled") and b["id"] not in skip_ids]:
        failure = collect_failure(ado, b, hosts)
        failure["flaky"] = assess_flaky(b, builds)
        out.append(diagnose(failure, fetch_health(api_url, failure["hostname"], failure["start"])))
    return out


@st.cache_data(ttl=20, show_spinner=False)
def load_pool(org_, project_, pool_, token):
    ado = make_ado(org_, project_, token)
    return ado.pool_agents(pool_), ado.pool_requests(pool_), ado.latest_agent_version(), ado.pool_is_hosted(pool_)


def mttr_minutes(runs: pd.DataFrame):
    gaps = []
    for _, bad in runs[runs["result"].isin(["failed", "canceled"])].iterrows():
        later = runs[(runs["pipeline"] == bad["pipeline"]) & (runs["result"] == "succeeded")
                     & (runs["finish_dt"] > bad["finish_dt"])]
        if not later.empty:
            gaps.append((later["finish_dt"].min() - bad["finish_dt"]).total_seconds() / 60)
    return round(sum(gaps) / len(gaps), 1) if gaps else None


# ------------------------------------------------------------------ settings (rarely changed, so kept out of the way)
cfg = config.settings()
ENTRA_OK = entra_available()
with st.sidebar:
    st.markdown("### Settings")
    use_sample = st.toggle("Use sample runs", value=not (os.getenv("AZDO_PAT") or ENTRA_OK),
                           help="Shows demo runs without calling Azure DevOps. Runner health stays live.")
    auth_mode = st.radio("Sign in with", ["Azure sign-in", "Access token"], index=0 if ENTRA_OK else 1,
                         help="Azure sign-in uses your az login and lists every organization you belong to. "
                              "An access token only works for the one organization it was made in.")
    pat = ""
    if auth_mode == "Access token":
        pat = os.getenv("AZDO_PAT") or st.text_input("Azure DevOps token", type="password",
                                                      help="Needs Build (Read) and Agent Pools (Read).")
    elif not ENTRA_OK:
        st.warning("No Azure sign-in found. Run az login, or choose Access token.")
    api = st.text_input("Health API", os.getenv("HEALTH_API", DEFAULT_HEALTH_API))
    top = st.slider("Recent runs to load", 5, 50, 20)
    st.markdown("### AI")
    ai_on = st.toggle("Enable AI features", value=ai_enabled(),
                      help="Uses the model deployed in Azure AI Foundry. Signs in with your `az login`; no API key. "
                           "Logs are checked for secrets before they are sent.")
    if ai_on:
        st.caption(f"Model: {get_llm().label}")

# ------------------------------------------------------------------ header + the one shared filter row
head, tools = st.columns([4, 1], vertical_alignment="top")
with head:
    st.markdown('<div class="pm-title">🩺 PipeMedic</div>'
                '<div class="pm-sub">Pipeline failures, explained by the health of the runner that ran them</div>',
                unsafe_allow_html=True)
with tools:
    if st.button("Refresh", icon=":material/refresh:", width="stretch"):
        st.cache_data.clear()

# ------------------------------------------------------------------ where to look: organization, project, repository, agent pool
def choose(label, names, wanted, key, help=None):
    """A dropdown when the names could be listed, otherwise a text box with the configured default."""
    if names:
        return st.selectbox(label, names, index=names.index(wanted) if wanted in names else 0, key=key, help=help)
    return st.text_input(label, wanted, key=key + "::text", help=help)


org_names, project_names, repo_list, pool_names = [], [], [], []
c_org, c_project, c_repo, c_pool = st.columns(4, gap="small")
with c_org:
    if not use_sample:
        try:
            org_names = list_organizations(pat)
        except Exception:
            org_names = []
    org = choose("Organization", org_names, cfg.org, "ctx_org", "Every organization you belong to. Needs the Azure sign-in.")
with c_project:
    if not use_sample:
        try:
            project_names = list_projects(org, pat)
        except Exception:
            project_names = []
    project = choose("Project", project_names, cfg.project, f"ctx_project::{org}")
with c_repo:
    repo_id, repo_pick = None, "All repositories"
    if not use_sample:
        try:
            repo_list = list_repos(org, project, pat)
        except Exception:
            repo_list = []
    if repo_list:
        repo_pick = st.selectbox("Repository", ["All repositories"] + [n for n, _ in repo_list], key=f"ctx_repo::{org}::{project}",
                                 help="Shows only the pipelines built from this repository.")
        repo_id = dict(repo_list).get(repo_pick)
with c_pool:
    if not use_sample:
        try:
            pool_names = list_pools(org, pat)
        except Exception:
            pool_names = []
    pool = choose("Agent pool", pool_names, cfg.pool, f"ctx_pool::{org}", "The pool whose agents and queue are shown on the Agents page.")

filt, source = st.columns([3, 2], vertical_alignment="bottom")
with filt:
    window = st.segmented_control("Time window for runner health", [30, 60, 120, 240], default=60,
                                  format_func=lambda m: f"{m} min" if m < 60 else f"{m // 60} h",
                                  help="Applies to runner health charts and predictive alerts") or 60
with source:
    src = "Sample runs · live runner health" if use_sample else f"Live · {org}/{project}"
    dot = T["muted"] if use_sample else T["good"]
    st.markdown(f'<div style="text-align:right;padding-bottom:6px"><span class="pm-badge"><span class="pm-dot" style="background:{dot}"></span>{esc(src)}</span></div>',
                unsafe_allow_html=True)

# ------------------------------------------------------------------ load everything
health = pd.DataFrame()
try:
    health = load_health(api, window)
except Exception as e:
    st.error(f"Could not read runner health ({e}). Check the Health API address under Settings.")

try:
    if use_sample:
        rows, diags = sample_data.BUILDS, sample_data.DIAGNOSES
        agents, reqs, latest, hosted = sample_data.POOL_AGENTS, sample_data.POOL_REQUESTS, None, False
    else:
        if auth_mode == "Access token" and not pat:
            st.info("Enter an Azure DevOps token under Settings (top left arrow), choose Azure sign-in, or switch on 'Use sample runs'.")
            st.stop()
        rows, diags = load_live(org, project, pool, api, pat, top)
        agents, reqs, latest, hosted = load_pool(org, project, pool, pat)
except requests.HTTPError as e:
    code = e.response.status_code
    hint = ("Your sign-in or token has no access to this organization or is missing a permission (Build read, Agent Pools read)."
            if code in (401, 403, 203) else "Try again in a moment.")
    st.error(f"Azure DevOps answered {code}. {hint}")
    st.stop()
except Exception as e:
    st.error(f"Could not load pipeline data: {e}")
    st.stop()

if not use_sample:
    # load_live investigates only the 10 newest failures. One button investigates the rest, and the result is kept for the session.
    more_key = f"older::{org}::{project}"
    done_ids = {d["build_id"] for d in diags}
    kept = [d for d in st.session_state.get(more_key, []) if d["build_id"] not in done_ids]
    pending = [r["id"] for r in rows if r.get("result") in ("failed", "canceled") and r["id"] not in done_ids | {d["build_id"] for d in kept}]
    if pending and st.button(f"Investigate the {len(pending)} older failed runs", help="Reads each run's log and diagnoses it, like the newest ten."):
        try:
            with st.spinner("Reading logs and diagnosing..."):
                st.session_state[more_key] = investigate_older(org, project, pool, api, pat, top, tuple(sorted(done_ids)))
        except Exception as e:
            st.error(f"Could not investigate the older runs: {e}")
    kept = [d for d in st.session_state.get(more_key, []) if d["build_id"] not in done_ids]
    diags = diags + kept

pool_status = analyze_pool(agents, reqs, latest_version=latest)
alerts = analyze_health(health.to_dict("records")) if not health.empty else []
if hosted:
    # Microsoft creates an agent per job and removes it afterwards: offline agents are normal, nobody can restart them, and they
    # report no health. The health feed on screen belongs to our own runners, not to this pool.
    pool_status["findings"] = []
    alerts = []
runs = pd.DataFrame(rows)
if not runs.empty:
    runs["finish_dt"] = pd.to_datetime(runs["finish"], utc=True)
    runs["start_dt"] = pd.to_datetime(runs["start"], utc=True)

total = len(runs)
ok_n = int((runs["result"] == "succeeded").sum()) if total else 0
failed_n = int(runs["result"].isin(["failed", "canceled"]).sum()) if total else 0
diag_by_run = {d["build_id"]: d for d in diags}
count = lambda v: sum(1 for d in diags if d["verdict"] == v)  # noqa: E731
mttr = mttr_minutes(runs) if total else None

# ------------------------------------------------------------------ what needs attention (infrastructure first)
items = []
for f in pool_status["findings"]:
    if f["level"] in ("critical", "warning"):
        items.append({"level": f["level"], "source": "Agent pool", "title": f["message"], "action": f["suggestion"]})
for a in alerts:
    items.append({"level": a["level"], "source": "Prediction", "title": a["message"], "action": a["suggestion"]})
seen_pipelines = set()
for d in sorted(diags, key=lambda d: -d["build_id"]):
    if d["verdict"] in ("runner", "mixed") and d["pipeline"] not in seen_pipelines:
        seen_pipelines.add(d["pipeline"])
        flags = ", ".join(d["runner_health"].get("flags", [])) or "runner issue"
        items.append({"level": "warning", "source": f"Run {d['build_id']}",
                      "title": f"{d['pipeline']} failed while {d.get('hostname') or 'its runner'} showed {flags}.",
                      "action": d["suggestions"][0] if d["suggestions"] else ""})
for d in diags:
    fx = d.get("fix") or {}
    if fx.get("status") == "opened":
        items.append({"level": "warning", "source": f"Run {d['build_id']}", "title": f"An AI-written fix for {d['pipeline']} is waiting for review: {fx.get('summary', '')}",
                      "action": f"Review and decide on the pull request: {fx.get('pr_url', '')}"})
items.sort(key=lambda i: {"critical": 0, "warning": 1}.get(i["level"], 2))
n_crit = sum(1 for i in items if i["level"] == "critical")

if n_crit:
    level, title = "critical", f"{n_crit} critical issue{'s' if n_crit != 1 else ''} need attention now"
elif items:
    level, title = "warning", f"{len(items)} item{'s' if len(items) != 1 else ''} need attention"
else:
    level, title = "good", "All clear: runners and the agent pool are healthy"
icon, _, colour = LEVEL[level]
sub = (f"{total} recent runs · {failed_n} failed, {len(diags)} investigated · "
       f"{len(health['hostname'].unique()) if not health.empty else 0} runners reporting")
st.markdown(f'<div class="pm-banner" style="border-left-color:{colour}"><div class="pm-icon" style="background:{colour}">{icon}</div>'
            f'<div><div class="pm-banner-title">{esc(title)}</div><div class="pm-banner-sub">{esc(sub)}</div></div></div>',
            unsafe_allow_html=True)

def diag_card(d, where):
    """One failed run: cause, runner health, evidence, fixes and the optional AI second opinion."""
    h = d["runner_health"]
    with st.container(border=True):
        a, b = st.columns([2.2, 1], gap="large")
        with a:
            st.markdown(f'<div class="pm-row-title">Run {esc(d["build_id"])} · {esc(d["pipeline"])}</div>'
                        f'<div class="pm-meta" style="margin:2px 0 8px 0">{esc(d["failed_step"])}</div>'
                        f'{pill(d["verdict"])} <span class="pm-meta">&nbsp;{esc(d["category"])}</span>'
                        f'<div class="pm-cause">{esc(d["root_cause"])}</div>', unsafe_allow_html=True)
        with b:
            if h.get("samples"):
                st.markdown(f'<div class="pm-stats">Runner <b>{esc(d.get("hostname") or d.get("agent") or "?")}</b><br>'
                            f'CPU peak <b>{h["cpu_max"]:.0f}%</b> · Memory peak <b>{h["memory_max"]:.0f}%</b><br>'
                            f'Disk used <b>{h["disk_max"]:.0f}%</b> · {h["samples"]} samples'
                            + (f'<br>Flags: <b>{esc(", ".join(h["flags"]))}</b>' if h["flags"] else "") + "</div>", unsafe_allow_html=True)
            else:
                st.markdown('<div class="pm-stats">No runner health samples were recorded for this run, '
                            'so the runner could not be ruled in or out.</div>', unsafe_allow_html=True)
        if d.get("similar"):
            st.markdown(f'<div class="pm-item"><span class="pm-pill"><span class="pm-dot" style="background:{T["series"][1]}"></span>Seen before</span>'
                        f'<div class="pm-item-title">{esc(d["similar"]["summary"])}</div></div>', unsafe_allow_html=True)
        fx = d.get("fix") or {}
        if fx.get("status") in ("opened", "proposed"):
            link = (f' <a href="{esc(fx["pr_url"])}" target="_blank">Review the pull request</a>' if fx.get("pr_url") else "")
            st.markdown(f'<div class="pm-item"><span class="pm-pill"><span class="pm-dot" style="background:{T["good"]}"></span>'
                        f'AI fix {"opened for review" if fx["status"] == "opened" else "drafted"}</span>'
                        f'<div class="pm-item-title">{esc(fx.get("summary", ""))}</div>'
                        f'<div class="pm-item-action">{esc(", ".join(fx.get("files", [])))} &middot; confidence {esc(fx.get("confidence", ""))} &middot; '
                        f'written by AI, never merged automatically.{link}</div></div>', unsafe_allow_html=True)
        with st.expander("Evidence and suggested fixes"):
            st.markdown("**Evidence from the log**")
            st.code("\n".join(d["evidence"]) or "No matching log lines.", language="text")
            st.markdown("**Suggested fixes**")
            for s in d["suggestions"]:
                st.markdown(f"- {s}")
        rl = d.get("runner_logs") or {}
        if rl.get("available"):
            n = len(rl.get("findings", []))
            with st.expander(f"Runner logs around the failure ({rl.get('window', '')}): " + (f"{n} problem{'s' if n != 1 else ''} found" if n else "no problems found")):
                for f in rl.get("findings", []):
                    st.markdown(f"- **{f['message']}** `{f['line']}`")
                st.code(rl["text"] or "No log lines.", language="text")
                st.caption("Read from the runner itself (system, kernel and agent logs) for the time of the run. Read-only.")
        elif d.get("hostname"):
            st.caption("The runner's own logs were not read for this run" + (f" ({rl['error']})." if rl.get("error") else "."))
        if ai_on or d.get("ai"):
            key = f"ai_{d['build_id']}"
            if ai_on and st.button("Get AI second opinion", key=f"btn_{where}_{key}", icon=":material/auto_awesome:"):
                with st.spinner("Asking the model. This takes a few seconds."):
                    try:
                        st.session_state[key] = ai_diagnose(get_llm(), d)
                    except (AIUnavailable, RuntimeError) as e:
                        st.session_state[key] = {"error": str(e)}
            written = d.get("ai") if isinstance(d.get("ai"), dict) and d["ai"].get("likely_cause") else None  # the agent's own, from failure time
            res = st.session_state.get(key) or written
            if res and res.get("error"):
                st.warning(f"The AI second opinion is not available: {res['error']}")
            elif res:
                agree = ("Agrees with the rules" if res["agrees_with_rules"]
                         else f"Differs from the rules ({d['category']})")
                quotes = "\n".join(res["evidence_verified"]) or "No quoted line could be confirmed in the log."
                st.markdown(f'<div class="pm-item"><span class="pm-pill"><span class="pm-dot" style="background:{T["series"][0]}"></span>'
                            f'AI second opinion</span> <span class="pm-meta">&nbsp;{esc(res["category"])} · confidence {esc(res["confidence"])} · '
                            f'{esc(agree)}</span><div class="pm-item-title">{esc(res["likely_cause"])}</div></div>',
                            unsafe_allow_html=True)
                st.code(quotes, language="text")
                if res.get("runner_effect"):
                    st.markdown(f"**Did the runner affect it?** {res['runner_effect']}")
                for s in res["suggested_fixes"][:3]:
                    st.markdown(f"- {s}")
                st.caption(f"Written by {res['model']}. Quoted lines are checked against the real log. "
                           "The verdict above comes from the rules and is unchanged.")


tab_over, tab_pipes, tab_fail, tab_agents, tab_ask, tab_act = st.tabs(
    ["Overview", "Pipelines", "Failures", "Agents", "Ask PipeMedic", "Remediation"])
with tab_agents:
    tab_runner, tab_hist = st.tabs(["Live now", "History"])

# ================================================================== OVERVIEW
with tab_over:
    k = st.columns(5)
    k[0].markdown(card("Success rate", f"{100 * ok_n / total:.0f}%" if total else "n/a", f"{ok_n} of {total} runs"), unsafe_allow_html=True)
    k[1].markdown(card("Failures", failed_n, f"{count('code-or-config') + count('mixed')} code · {count('runner')} runner · {count('flaky')} flaky"),
                  unsafe_allow_html=True)
    k[2].markdown(card("Needs attention", len(items), "infrastructure items" if items else "nothing open"), unsafe_allow_html=True)
    k[3].markdown(card("Mean time to recover", f"{mttr} min" if mttr is not None else "n/a",
                       "failure to next pass" if mttr is not None else "no failed pipeline has passed since"), unsafe_allow_html=True)
    k[4].markdown(card("Pool utilization", f"{pool_status['utilization_pct']}%",
                       f"{pool_status['agents_busy']} of {pool_status['agents_online']} agents busy · {pool_status['jobs_queued']} queued"),
                  unsafe_allow_html=True)

    left, right = st.columns([1.35, 1], gap="large")
    with left:
        section("Run history", "Each mark is one finished run. Hover for details.")
        if runs.empty:
            st.info("No runs yet. Queue a pipeline in Azure DevOps and refresh.")
        else:
            h = runs.copy()
            h["finished"] = local_time(h["finish_dt"])
            h["result"] = h["result"].fillna("unknown")
            def _cause(i, result):
                if i in diag_by_run:
                    d_ = diag_by_run[i]
                    return f"{VERDICT.get(d_['verdict'], VERDICT['unknown'])[0]} · {d_.get('category', 'unknown')}"
                return "not investigated" if result in ("failed", "canceled") else ""
            h["cause"] = [_cause(i, r) for i, r in zip(h["id"], h["result"])]
            shapes = {"succeeded": "circle", "failed": "cross", "canceled": "diamond", "unknown": "square"}
            colours = {"succeeded": T["good"], "failed": T["critical"], "canceled": T["warning"], "unknown": T["muted"]}
            present = [r for r in shapes if r in set(h["result"])]
            chart = alt.Chart(h).mark_point(filled=True, size=150, strokeWidth=2, opacity=1).encode(
                x=alt.X("finished:T", title="Finished (local time)", axis=alt.Axis(format="%H:%M", grid=False)),
                y=alt.Y("pipeline:N", title=None, sort=alt.SortField("id", order="descending"), axis=alt.Axis(labelLimit=240, grid=True)),
                color=alt.Color("result:N", scale=alt.Scale(domain=present, range=[colours[r] for r in present]),
                                legend=alt.Legend(title=None)),
                shape=alt.Shape("result:N", scale=alt.Scale(domain=present, range=[shapes[r] for r in present]), legend=alt.Legend(title=None)),
                tooltip=[alt.Tooltip("id:Q", title="Run"), alt.Tooltip("pipeline:N", title="Pipeline"),
                         alt.Tooltip("result:N", title="Result"), alt.Tooltip("cause:N", title="Cause"),
                         alt.Tooltip("finished:T", title="Finished", format="%b %d, %H:%M")],
            ).properties(height=max(150, 44 * h["pipeline"].nunique() + 40))
            with st.container(border=True):
                st.altair_chart(style(chart), width="stretch")
            table_twin(h[["id", "pipeline", "result", "cause", "finished"]].rename(columns={"id": "run"}))
    with right:
        section("Needs attention", "Most urgent first. Each item says what to do.")
        if not items:
            st.markdown(f'<div class="pm-item">{sev("good")}<div class="pm-item-title">Nothing needs attention right now.</div>'
                        f'<div class="pm-item-action">Runners are within normal ranges and no jobs are waiting on an idle agent.</div></div>',
                        unsafe_allow_html=True)
        for it in items[:6]:
            st.markdown(f'<div class="pm-item">{sev(it["level"])} <span class="pm-meta">· {esc(it["source"])}</span>'
                        f'<div class="pm-item-title">{esc(it["title"])}</div>'
                        f'<div class="pm-item-action"><b>Do this:</b> {esc(it["action"])}</div></div>', unsafe_allow_html=True)
        if len(items) > 6:
            st.caption(f"{len(items) - 6} more on the Runners & pool tab.")

# ================================================================== PIPELINES
STATUS_COLOUR = {"good": T["good"], "critical": T["critical"], "warning": T["warning"], "info": T["series"][0], "muted": T["muted"]}
STATUS_ICON = {"good": "✓", "critical": "✕", "warning": "!", "info": "…", "muted": "–"}


def status_badge(colour, label):
    return f'<span class="pm-sev"><i style="background:{STATUS_COLOUR[colour]}">{STATUS_ICON[colour]}</i>{esc(label)}</span>'


def fmt_time(iso):
    if not iso:
        return "-"
    return pd.Timestamp(iso).tz_convert(datetime.now().astimezone().tzinfo).strftime("%d %b %H:%M")


def health_line(h):
    """One line about a runner's health during a run."""
    if not h or not h.get("samples"):
        return "No runner health was recorded for this time."
    text = f'CPU peak <b>{h["cpu_max"]:.0f}%</b> · Memory peak <b>{h["memory_max"]:.0f}%</b> · Disk <b>{h["disk_max"]:.0f}%</b> used'
    flags = [f for f in h.get("flags", []) if f != "no-health-data"]
    return text + (f' · <b style="color:{T["critical"]}">{esc(", ".join(flags))}</b>' if flags else " · no problems seen")


def render_run(run, siblings):
    """Everything about one run: status, jobs, the agents involved and their health, and what went wrong."""
    bid = run["id"]
    colour, label = status_of(run)
    # A YAML pipeline reports the default hosted queue, not the pool named in its file, so the pool picked above is used
    # unless the run itself says it used a pool you host.
    queue_pool = (run.get("queue") or {}).get("pool") or {}
    pool_name = queue_pool["name"] if queue_pool.get("name") and not queue_pool.get("isHosted") else pool
    link = f"https://dev.azure.com/{org}/{project}/_build/results?buildId={bid}"
    who = (run.get("requestedFor") or {}).get("displayName", "-")
    took = duration_label(seconds_between(run.get("startTime"), run.get("finishTime")))
    st.markdown(
        f'<div class="pm-item" style="margin-top:14px">{status_badge(colour, label)}'
        f'<div class="pm-row-title" style="margin-top:6px">Run {esc(run["buildNumber"])} (build {bid}) · {esc(run["definition"]["name"])}</div>'
        f'<div class="pm-meta">Branch <b>{esc(branch_name(run.get("sourceBranch")))}</b> · commit {esc((run.get("sourceVersion") or "")[:7] or "-")} · '
        f'triggered by {esc(who)} · started {esc(fmt_time(run.get("startTime") or run.get("queueTime")))} · took {esc(took)} · '
        f'<a href="{esc(link)}" target="_blank">Open in Azure DevOps</a></div></div>', unsafe_allow_html=True)

    try:
        timeline = load_timeline(org, project, bid, pat)
    except Exception as e:
        st.warning(f"Could not read the jobs of this run ({e}).")
        timeline = []
    jobs = job_rows(timeline, load_hosts(org, project, pool_name, pat))
    involved = agents_involved(jobs)

    section("Agents involved", "The agents that ran this run's jobs, and how each machine was doing at the time.")
    if not involved:
        st.info("No agent has picked up a job of this run yet." if run.get("status") != "completed" else "This run has no job on an agent.")
    now_iso = datetime.now(timezone.utc).isoformat()
    cols = st.columns(min(len(involved), 3) or 1, gap="medium")
    for i, a in enumerate(involved):
        h = run_health(api, a["host"], a["start"], a["finish"] or now_iso) if a["host"] and a["start"] else None
        machine = f' on <b>{esc(a["host"])}</b>' if a["host"] else ""
        note = health_line(h) if a["host"] else "No runner health: this agent's machine is not known, or it is a Microsoft hosted agent."
        state = status_badge("critical", "A job failed here") if a["failed"] else ""
        cols[i % len(cols)].markdown(
            f'<div class="pm-card"><div class="pm-row-title">{esc(a["agent"])}</div>'
            f'<div class="pm-meta">{esc(", ".join(a["jobs"]))}{machine}</div>'
            f'<div style="margin:8px 0 2px 0">{state}</div><div class="pm-stats">{note}</div></div>', unsafe_allow_html=True)

    if jobs:
        section("Jobs")
        st.dataframe(pd.DataFrame([{"Job": j["name"], "Status": j["status"], "Agent": j["agent"] or "-", "Machine": j["host"] or "-",
                                    "Started": fmt_time(j["start"]), "Took": duration_label(j["seconds"])} for j in jobs]),
                     hide_index=True, width="stretch")

    if run.get("status") == "completed" and run.get("result") in ("failed", "canceled"):
        section("What went wrong", "The failing step's log and the runner's health, read together.")
        try:
            saved = saved_diagnoses(org, project).get(bid)  # the agent's own analysis from the time of the failure, if it made one
            with_logs = st.session_state.get(f"rl::{bid}", False)
            with st.spinner("Reading the logs" + (" and the runner's logs" if with_logs else "") + "..."):
                d = saved if saved and not with_logs else run_diagnosis(org, project, pat, run, pool_name, api, assess_flaky(run, siblings), with_logs)
            diag_card(d, "pipelines")
            if saved and not with_logs:
                st.caption("This is the analysis the PipeMedic agent made when the run failed.")
            if not (d.get("runner_logs") or {}).get("available") and d.get("hostname"):
                if st.button("Read the runner's logs for this run", key=f"btn_rl_{bid}", icon=":material/dns:",
                             help="Runs a read-only command on the runner to read its system, kernel and agent logs for the time of the run. "
                                  "Needs the runner to be on; a window with no system log entries is reported as no logs."):
                    st.session_state[f"rl::{bid}"] = True
                    st.rerun()
        except Exception as e:
            st.error(f"Could not investigate this run: {e}")
    elif run.get("result") == "succeeded":
        st.markdown(f'<div class="pm-item">{sev("good")}<div class="pm-item-title">This run succeeded, so there is nothing to investigate.</div></div>',
                    unsafe_allow_html=True)
    elif run.get("status") != "completed":
        st.info("This run has not finished. Its result and any cause will appear here when it does; refresh to check.")

    steps = step_rows(timeline)
    if steps:
        with st.expander(f"Steps ({len(steps)})"):
            st.dataframe(pd.DataFrame([{"Job": x["job"], "Step": x["name"], "Status": x["status"], "Took": duration_label(x["seconds"])}
                                       for x in steps]), hide_index=True, width="stretch")


with tab_pipes:
    if use_sample:
        st.info("Sample mode only has demo runs. Switch off 'Use sample runs' in Settings to browse the pipelines of an organization.")
    else:
        try:
            defs, builds_all = load_pipelines(org, project, repo_id, pat)
        except Exception as e:
            st.error(f"Could not list the pipelines of {org}/{project} ({e}).")
            defs, builds_all = [], []
        prow = pipeline_rows(defs, builds_all)
        scope = repo_pick if repo_id else "all repositories"
        section("Pipelines", f"{len(prow)} in {project} ({scope}). Click a row to open a pipeline.")
        if not prow:
            st.info("No pipelines were found here. Check the project and repository above.")
        else:
            table = pd.DataFrame([{
                "Pipeline": r["name"],
                "Last run": status_of(r["last"])[1] if r["last"] else "No runs yet",
                "Branch": branch_name(r["last"].get("sourceBranch")) if r["last"] else "-",
                "Started": fmt_time(r["last"].get("startTime") or r["last"].get("queueTime")) if r["last"] else "-",
                "Pass rate": f'{r["pass_rate"]}% of last {r["finished"]}' if r["pass_rate"] is not None else "-",
                "Runs": r["runs"]} for r in prow])
            picked = st.dataframe(table, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                                  key=f"pipe_tbl::{org}::{project}::{repo_id}")
            chosen = prow[min(picked.selection.rows[0] if picked.selection.rows else 0, len(prow) - 1)]

            section(chosen["name"], "Runs of this pipeline, newest first. Click a run to see its agents and what went wrong.")
            try:
                runs_p = load_runs(org, project, chosen["id"], pat)
            except Exception as e:
                st.error(f"Could not list the runs ({e}).")
                runs_p = []
            if not runs_p:
                st.info("This pipeline has not run yet.")
            else:
                run_table = pd.DataFrame([{
                    "Run": b["buildNumber"], "Status": status_of(b)[1], "Branch": branch_name(b.get("sourceBranch")),
                    "Triggered by": (b.get("requestedFor") or {}).get("displayName", "-"),
                    "Started": fmt_time(b.get("startTime") or b.get("queueTime")),
                    "Took": duration_label(seconds_between(b.get("startTime"), b.get("finishTime")))} for b in runs_p])
                run_pick = st.dataframe(run_table, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                                        key=f"run_tbl::{org}::{project}::{chosen['id']}")
                render_run(runs_p[min(run_pick.selection.rows[0] if run_pick.selection.rows else 0, len(runs_p) - 1)], runs_p)

# ================================================================== FAILURES
with tab_fail:
    section("Failures and why they happened", "The most recent failed runs are investigated (log, runner health, cause). Older ones are marked not investigated.")
    if not diags:
        st.success("No failed runs to investigate.")
    else:
        options = ["All"] + [VERDICT[v][0] for v in VERDICT if any(d["verdict"] == v for d in diags)]
        pick = st.segmented_control("Show", options, default="All", label_visibility="collapsed") or "All"
        shown = [d for d in sorted(diags, key=lambda d: -d["build_id"]) if pick == "All" or VERDICT[d["verdict"]][0] == pick]
        if not shown:
            st.info("No failures match this filter.")
        for d in shown:
            diag_card(d, "failures")

# ================================================================== RUNNERS & POOL
with tab_runner:
    section("Agent pool", f"Capacity and queue for {pool}.")
    p = st.columns(5)
    p[0].markdown(card("Agents online", f"{pool_status['agents_online']} / {pool_status['agents_total']}"), unsafe_allow_html=True)
    p[1].markdown(card("Busy · idle", f"{pool_status['agents_busy']} · {pool_status['agents_idle']}"), unsafe_allow_html=True)
    p[2].markdown(card("Jobs queued", pool_status["jobs_queued"], f"oldest waited {pool_status['oldest_wait_s']}s" if pool_status["jobs_queued"] else "none waiting"),
                  unsafe_allow_html=True)
    p[3].markdown(card("Average queue wait", f"{pool_status['avg_wait_s']}s" if pool_status["avg_wait_s"] is not None else "n/a", "recent jobs"),
                  unsafe_allow_html=True)
    p[4].markdown(card("Agent version", ", ".join(pool_status["versions"]) or "unknown"), unsafe_allow_html=True)
    st.write("")
    if hosted:
        st.markdown('<div class="pm-item"><div class="pm-item-title">Microsoft-hosted pool: no runner health is available.</div>'
                    '<div class="pm-item-action">Microsoft creates an agent for each job and removes it afterwards, so agents look offline between jobs. '
                    'Failure diagnoses here say the runner health is unknown and judge the code, configuration and log only.</div></div>', unsafe_allow_html=True)
    elif not pool_status["findings"]:
        st.markdown(f'<div class="pm-item">{sev("good")}<div class="pm-item-title">The pool looks healthy.</div></div>', unsafe_allow_html=True)
    for f in pool_status["findings"]:
        st.markdown(f'<div class="pm-item">{sev(f["level"])}<div class="pm-item-title">{esc(f["message"])}</div>'
                    f'<div class="pm-item-action"><b>Do this:</b> {esc(f["suggestion"])}</div></div>', unsafe_allow_html=True)

    section("Predictive alerts", "Warns when a runner is heading for trouble, before jobs fail.")
    if not alerts:
        st.markdown(f'<div class="pm-item">{sev("good")}<div class="pm-item-title">No runner is trending towards trouble.</div></div>', unsafe_allow_html=True)
    for a in alerts:
        st.markdown(f'<div class="pm-item">{sev(a["level"])}<div class="pm-item-title">{esc(a["message"])}</div>'
                    f'<div class="pm-item-action"><b>Do this:</b> {esc(a["suggestion"])}</div></div>', unsafe_allow_html=True)

    section("Runner health", f"Last {window if window < 60 else str(window // 60) + ' h'}. One line per runner; hover for exact values.")
    if hosted:
        st.info("Microsoft-hosted agents report no runner health, so there is nothing to chart for this pool.")
    elif health.empty:
        st.info("No health samples in this window. Check that the VMs are running and the health timer is active.")
    else:
        hosts = sorted(health["hostname"].unique())
        colour_of = {h: T["series"][i % len(T["series"])] for i, h in enumerate(hosts)}  # colour follows the runner
        latest_rows = health.sort_values("time").groupby("hostname").tail(1).set_index("hostname")
        cols = st.columns(len(hosts))
        for col, hname in zip(cols, hosts):
            r = latest_rows.loc[hname]
            age = (datetime.now(timezone.utc) - r["time"].to_pydatetime()).total_seconds()
            if age > 180:
                lvl, text = "warning", "Not reporting"
            elif max(r["cpu_percent"], r["memory_percent"], r["disk_percent"]) >= 90:
                lvl, text = "warning", "Under strain"
            else:
                lvl, text = "good", "Healthy"
            icon, _, c = LEVEL[lvl]
            col.markdown(f'<div class="pm-card"><div style="display:flex;justify-content:space-between;align-items:center">'
                         f'<span class="pm-row-title"><span class="pm-dot" style="background:{colour_of[hname]};margin-right:8px"></span>{esc(hname)}</span>'
                         f'<span class="pm-sev"><i style="background:{c}">{icon}</i>{text}</span></div>'
                         f'<div class="pm-stats" style="margin-top:10px">CPU <b>{r["cpu_percent"]:.0f}%</b> · Memory <b>{r["memory_percent"]:.0f}%</b> · '
                         f'Disk <b>{r["disk_percent"]:.0f}%</b> ({r["disk_free_gb"]:.0f} GB free)</div></div>', unsafe_allow_html=True)
        st.write("")

        def line(field, title, limit):
            d = health[["time", "hostname", field]].dropna()
            d = d.set_index("time").groupby("hostname")[field].resample("1min").mean().reset_index()
            d.columns = ["runner", "time", "value"]
            d["time"] = local_time(d["time"])
            nearest = alt.selection_point(nearest=True, on="pointerover", fields=["time"], empty=False)
            base = alt.Chart(d).encode(
                x=alt.X("time:T", title=None, axis=alt.Axis(format="%H:%M", grid=False)),
                y=alt.Y("value:Q", title=None, scale=alt.Scale(domain=[0, 100]), axis=alt.Axis(values=[0, 25, 50, 75, 100], format=".0f")),
                color=alt.Color("runner:N", scale=alt.Scale(domain=hosts, range=[colour_of[x] for x in hosts]), legend=alt.Legend(title=None)))
            lines = base.mark_line(strokeWidth=2)
            hit = base.mark_point(opacity=0, size=400).add_params(nearest)
            dots = base.mark_point(filled=True, size=70, stroke=T["surface"], strokeWidth=2).encode(
                opacity=alt.condition(nearest, alt.value(1), alt.value(0)))
            rule = alt.Chart(d).mark_rule(color=T["axis"], strokeWidth=1).encode(
                x="time:T", tooltip=[alt.Tooltip("time:T", title="Time", format="%H:%M")] +
                [alt.Tooltip("runner:N", title="Runner"), alt.Tooltip("value:Q", title=title, format=".0f")]).transform_filter(nearest)
            limit_rule = alt.Chart(pd.DataFrame({"y": [limit], "t": [f"{limit}% alert level"]})).mark_rule(color=T["muted"], strokeWidth=1).encode(y="y:Q")
            limit_text = alt.Chart(pd.DataFrame({"y": [limit], "t": [f"{limit}% alert level"]})).mark_text(
                align="left", dx=4, dy=-6, color=T["muted"], fontSize=11).encode(y="y:Q", text="t:N", x=alt.value(0))
            return alt.layer(limit_rule, limit_text, lines, hit, rule, dots).properties(height=200), d

        c1, c2, c3 = st.columns(3, gap="medium")
        for col, (field, title, limit) in zip((c1, c2, c3), (("cpu_percent", "CPU %", 90), ("memory_percent", "Memory %", 90), ("disk_percent", "Disk used %", 90))):
            ch, data = line(field, title, limit)
            with col:
                st.markdown(f'<div class="pm-label" style="margin-bottom:4px"><b style="color:var(--pm-ink)">{esc(title)}</b></div>', unsafe_allow_html=True)
                with st.container(border=True):
                    st.altair_chart(style(ch), width="stretch")
                table_twin(data.rename(columns={"value": title}), "Table")

# ================================================================== AGENT HISTORY
HIST_RANGES = {60: "1 h", 360: "6 h", 1440: "24 h", 4320: "3 days", 10080: "7 days"}
with tab_hist:
    section("Agent history", "How each runner is doing now and how it did in the past. Failed runs are marked on the charts.")
    if hosted:
        st.info("Microsoft-hosted agents report no runner health, so there is no history to show for this pool.")
    else:
        hist_range = st.segmented_control("Range", list(HIST_RANGES), default=1440, format_func=HIST_RANGES.get, key="hist_range") or 1440
        hist = pd.DataFrame()
        try:
            hist = load_health(api, hist_range)
        except Exception as e:
            st.error(f"Could not read runner health ({e}). Check the Health API address under Settings.")
        if hist.empty:
            st.info("No health samples in this range. Check that the VMs are running and the health timer is active.")
        else:
            now_utc = datetime.now(timezone.utc)
            start_utc = now_utc - timedelta(minutes=hist_range)
            names = sorted(hist["hostname"].unique())
            pick = st.selectbox("Runner", ["All runners"] + names, key="hist_runner")
            shown = names if pick == "All runners" else [pick]
            hist_colour = {h: T["series"][i % len(T["series"])] for i, h in enumerate(names)}
            gaps = silent_periods(hist.to_dict("records"), now_utc, start_utc)

            cols = st.columns(len(shown))
            for col, hname in zip(cols, shown):
                rows_h = hist[hist["hostname"] == hname].sort_values("time")
                last = rows_h.iloc[-1]
                age = (now_utc - last["time"].to_pydatetime()).total_seconds()
                if age > 180:
                    lvl, text = "warning", f"Not reporting for {duration_text(age)}"
                elif max(last["cpu_percent"], last["memory_percent"], last["disk_percent"]) >= 90:
                    lvl, text = "warning", "Under strain"
                else:
                    lvl, text = "good", "Healthy"
                icon, _, c = LEVEL[lvl]
                avail = availability(gaps, hname, start_utc, now_utc)
                col.markdown(
                    f'<div class="pm-card tall"><div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">'
                    f'<span class="pm-row-title"><span class="pm-dot" style="background:{hist_colour[hname]};margin-right:8px"></span>{esc(hname)}</span>'
                    f'<span class="pm-sev"><i style="background:{c}">{icon}</i>{esc(text)}</span></div>'
                    f'<div class="pm-stats" style="margin-top:10px">Reporting <b>{avail:.0f}%</b> of the last {HIST_RANGES[hist_range]}<br>'
                    f'CPU average <b>{rows_h["cpu_percent"].mean():.0f}%</b>, peak <b>{rows_h["cpu_percent"].max():.0f}%</b><br>'
                    f'Memory average <b>{rows_h["memory_percent"].mean():.0f}%</b>, peak <b>{rows_h["memory_percent"].max():.0f}%</b><br>'
                    f'Disk now <b>{last["disk_percent"]:.0f}%</b>, peak <b>{rows_h["disk_percent"].max():.0f}%</b> ({last["disk_free_gb"]:.0f} GB free)</div></div>',
                    unsafe_allow_html=True)
            st.write("")

            # failed runs that finished on the shown runners inside this range
            marks = pd.DataFrame([
                {"t_utc": pd.to_datetime(d["finish"], utc=True), "run": f"Run {d['build_id']}", "pipeline": d["pipeline"],
                 "verdict": VERDICT.get(d["verdict"], VERDICT["unknown"])[0], "runner": d["hostname"]}
                for d in diags if d.get("hostname") in shown and d.get("finish")
                and start_utc <= pd.to_datetime(d["finish"], utc=True) <= now_utc])
            if not marks.empty:
                marks["time"] = local_time(marks["t_utc"])

            lo, hi = (local_time(pd.Series([pd.Timestamp(x)])).iloc[0] for x in (start_utc, now_utc))
            dt = lambda x: alt.DateTime(year=x.year, month=x.month, date=x.day, hours=x.hour, minutes=x.minute)  # noqa: E731
            bucket = bucket_minutes(hist_range)

            def history_line(field, title, limit):
                d = hist[hist["hostname"].isin(shown)][["time", "hostname", field]].dropna()
                d = d.set_index("time").groupby("hostname")[field].resample(f"{bucket}min").mean().reset_index()
                d.columns = ["runner", "time", "value"]
                d["time"] = local_time(d["time"])
                # A bucket with no report breaks the line instead of being bridged, so an outage shows as a gap.
                d["seg"] = d["value"].isna().groupby(d["runner"]).cumsum()
                d = d.dropna(subset=["value"])
                d["key"] = d["runner"] + "#" + d["seg"].astype(str)
                nearest = alt.selection_point(nearest=True, on="pointerover", fields=["time"], empty=False)
                base = alt.Chart(d).encode(
                    x=alt.X("time:T", title=None, scale=alt.Scale(domain=[dt(lo), dt(hi)]),
                            axis=alt.Axis(format="%H:%M" if hist_range <= 1440 else "%d %b %H:%M", grid=False, labelOverlap=True)),
                    y=alt.Y("value:Q", title=None, scale=alt.Scale(domain=[0, 100]), axis=alt.Axis(values=[0, 25, 50, 75, 100], format=".0f")),
                    color=alt.Color("runner:N", scale=alt.Scale(domain=names, range=[hist_colour[x] for x in names]), legend=alt.Legend(title=None)))
                lines = base.mark_line(strokeWidth=2).encode(detail="key:N")
                hit = base.mark_point(opacity=0, size=400).add_params(nearest)
                dots = base.mark_point(filled=True, size=70, stroke=T["surface"], strokeWidth=2).encode(
                    opacity=alt.condition(nearest, alt.value(1), alt.value(0)))
                tip = [alt.Tooltip("time:T", title="Time", format="%d %b %H:%M"), alt.Tooltip("runner:N", title="Runner"),
                       alt.Tooltip("value:Q", title=f"{title} (average of {bucket} min)" if bucket > 1 else title, format=".0f")]
                rule = alt.Chart(d).mark_rule(color=T["axis"], strokeWidth=1).encode(x="time:T", tooltip=tip).transform_filter(nearest)
                limit_df = pd.DataFrame({"y": [limit]})
                layers = [alt.Chart(limit_df).mark_rule(color=T["muted"], strokeWidth=1).encode(y="y:Q"), lines, hit, rule, dots]
                if not marks.empty:
                    mtip = [alt.Tooltip("time:T", title="Finished", format="%d %b %H:%M"), alt.Tooltip("run:N", title="Failed run"),
                            alt.Tooltip("pipeline:N", title="Pipeline"), alt.Tooltip("verdict:N", title="Verdict"), alt.Tooltip("runner:N", title="Runner")]
                    layers += [alt.Chart(marks).mark_rule(color=T["critical"], strokeDash=[4, 3], strokeWidth=1.2, opacity=0.7).encode(x="time:T"),
                               alt.Chart(marks).mark_point(filled=True, shape="triangle-down", color=T["critical"], size=90).encode(
                                   x="time:T", y=alt.value(7), tooltip=mtip)]
                return alt.layer(*layers).properties(height=200), d

            hint = f"Each point averages {bucket} minutes." if bucket > 1 else "One point per minute."
            if not marks.empty:
                hint += " Red triangles are failed runs."
            section("Health over time", f"{HIST_RANGES[hist_range]} · {hint} A broken line means the runner sent no reports; hover for values.")
            c1, c2, c3 = st.columns(3, gap="medium")
            for col, (field, title, limit) in zip((c1, c2, c3), (("cpu_percent", "CPU %", 90), ("memory_percent", "Memory %", 90), ("disk_percent", "Disk used %", 90))):
                ch, data = history_line(field, title, limit)
                with col:
                    st.markdown(f'<div class="pm-label" style="margin-bottom:4px"><b style="color:var(--pm-ink)">{esc(title)}</b></div>', unsafe_allow_html=True)
                    with st.container(border=True):
                        st.altair_chart(style(ch), width="stretch")
                    table_twin(data[["runner", "time", "value"]].rename(columns={"value": title}), "Table")

            section("Times a runner stopped reporting", "A gap longer than 3 minutes: the VM was off, or the health timer or the network stopped.")
            shown_gaps = [g for g in gaps if g["host"] in shown]
            if not shown_gaps:
                st.markdown(f'<div class="pm-item">{sev("good")}<div class="pm-item-title">No gaps in this range.</div></div>', unsafe_allow_html=True)
            else:
                def when(x):
                    return local_time(pd.Series([pd.Timestamp(x)])).iloc[0].strftime("%d %b %H:%M")
                st.dataframe(pd.DataFrame([{
                    "Runner": g["host"], "From": ("start of range (no data yet)" if g["before_window"] else when(g["start"])),
                    "To": ("still silent" if g["ongoing"] else when(g["end"])), "Length": duration_text(g["seconds"]),
                } for g in sorted(shown_gaps, key=lambda g: g["start"], reverse=True)]), hide_index=True, width="stretch")

# ================================================================== ASK PIPEMEDIC
with tab_ask:
    section("Ask PipeMedic", "Answers come from the runs, runner health and pool data on this page. "
                             "It can read but never change anything.")
    if not ai_on:
        st.info("AI features are off. Turn on 'Enable AI features' under Settings, and make sure you are signed in with `az login`.")
    else:
        chat_ctx = {"runs": rows, "diags": diags, "pool": pool_status, "alerts": alerts,
                    "health": health.to_dict("records") if not health.empty else []}
        history = st.session_state.setdefault("chat", [])

        def run_question(q: str):
            with st.chat_message("user"):
                st.write(q)
            with st.chat_message("assistant"):
                with st.spinner("Checking the data. This can take 10 to 30 seconds."):
                    try:
                        prior = [{"role": m["role"], "content": m["content"]} for m in history[-6:]]
                        out = ask_pipemedic(get_llm(), q, chat_ctx, prior)
                    except (AIUnavailable, RuntimeError) as e:
                        out = {"answer": f"I could not reach the model: {e}", "trace": []}
                st.write(out["answer"])
                if out["trace"]:
                    with st.expander("How I got this"):
                        for t in out["trace"]:
                            st.markdown(f"**{t['tool']}** `{t['args']}`")
                            st.code(t["result"][:1500], language="json")
            history.append({"role": "user", "content": q})
            history.append({"role": "assistant", "content": out["answer"], "trace": out["trace"]})

        for m in history:
            with st.chat_message(m["role"]):
                st.write(m["content"])
                if m.get("trace"):
                    with st.expander("How I got this"):
                        for t in m["trace"]:
                            st.markdown(f"**{t['tool']}** `{t['args']}`")
                            st.code(t["result"][:1500], language="json")

        if not history:
            st.markdown('<div class="pm-hint">Try one of these:</div>', unsafe_allow_html=True)
            ideas = ["Why did run 9 fail?", "Which failures were the runner's fault?", "Is anything about to break?",
                     "Which runner is busiest right now?", "Summarize what happened in three lines."]
            for row in (ideas[:3], ideas[3:]):
                for col, idea in zip(st.columns(3), row):
                    if col.button(idea, key=f"idea_{idea}", width="stretch"):
                        st.session_state["pending_q"] = idea
                        st.rerun()
        question = st.chat_input("Ask about runs, runners or the pool") or st.session_state.pop("pending_q", None)
        if question:
            run_question(question)
        if history and st.button("Clear conversation", icon=":material/delete:"):
            st.session_state["chat"] = []
            st.rerun()

# ================================================================== REMEDIATION
with tab_act:
    section("What PipeMedic may do on its own", "Everything else waits for a person.")
    g1, g2 = st.columns(2, gap="large")
    with g1:
        st.markdown(f'<div class="pm-card tall"><div class="pm-row-title">Allowed automatically</div><div class="pm-cause">'
                    f'<b>Retry a failed run</b>, once per run and never a retry of a retry.<br>'
                    f'<b>Clean a low-disk runner</b>, only when it is idle.<br>'
                    f'<b>Open a work item</b> for the person who triggered a failing run.<br>'
                    f'<span class="pm-meta">Allowlist: {esc(", ".join(sorted(AUTO_ACTIONS)))}</span></div></div>', unsafe_allow_html=True)
    with g2:
        st.markdown(f'<div class="pm-card tall"><div class="pm-row-title">Always needs approval</div><div class="pm-cause">'
                    f'Installing tools on a runner, scaling or restarting, quarantining a test, and any cause the rules do not recognise.<br>'
                    f'It also stops when runner health is unknown, and after {MAX_AUTO_PER_HOUR} automatic actions in one hour. '
                    f'Every action is logged.</div></div>', unsafe_allow_html=True)

    section("AI fixes as pull requests", "The owner decides how far PipeMedic goes with an AI-written fix. It never merges anything.")
    FIX_CHOICES = {"off": "Off: no fix is written", "propose": "Propose: describe the fix in the email only",
                   "open": "Open pull requests: write the fix, verify it with a pipeline run, and open a PR for review"}
    _scope_project = f"{org}/{project}"
    if st.session_state.get("flash"):  # shown after the page reloads, so the "current" lines below are up to date
        st.success(st.session_state.pop("flash"))
    try:
        from azure_auth import get_credential
        from store import DEFAULT_STATE_URL, TableStore
        _store = TableStore(DEFAULT_STATE_URL, get_credential())
        current = _store.get_setting("fix_mode", "", scope=_scope_project) or "(not set: the app setting applies)"
    except Exception as e:
        _store, current = None, f"unavailable ({str(e)[:80]})"
    _apply = "This project"
    if _store is not None:
        _apply = st.radio("These settings apply to", ["This project", "All projects"], horizontal=True,
                          help=f"A project's own choice wins over the all-projects default. This project: {_scope_project}.")
    _scope = _scope_project if _apply == "This project" else None

    def _src(name):
        """Where the value in force for this project comes from."""
        return {"project": "set for this project", "all": "the all-projects default", "none": "not set"}[_store.setting_source(name, _scope_project)]

    st.markdown(f'<div class="pm-item"><div class="pm-item-title">Current choice: {esc(current)}{" (" + _src("fix_mode") + ")" if _store is not None else ""}</div>'
                '<div class="pm-item-action">Only test failures and script failures with a code or config verdict get a fix. '
                'A pipeline may have up to three open fixes, but never two for the same failure or the same file. The watcher reads this every minute.</div></div>', unsafe_allow_html=True)
    if _store is not None:
        pick = st.radio("When a failure can be fixed by a code change", list(FIX_CHOICES), format_func=FIX_CHOICES.get,
                        index=list(FIX_CHOICES).index(current) if current in FIX_CHOICES else 1)
        if st.button("Save preference"):
            _store.set_setting("fix_mode", pick, who=str(st.session_state.get("who", "dashboard")), scope=_scope)
            st.session_state["flash"] = f"Saved: {FIX_CHOICES[pick]}"
            st.rerun()

    section("Automatic actions", "Choose which allowlisted actions may run for real. The rest only show what they would do.")
    if _store is None:
        st.info("Sign in to Azure (and install the Azure packages) to change the automatic actions.")
    else:
        ACTION_HELP = {"create-work-item": "Open a work item for the person who triggered a failing run",
                       "retry-run": "Re-run a failed run once, after the runner recovered or for an intermittent test",
                       "clean-workdir": "Clear old job folders on a low-disk runner (only when idle)"}
        saved_actions = _store.get_setting("actions", None, scope=_scope_project)
        on = [a for a in (saved_actions or "").split(",") if a in ACTION_HELP] if saved_actions is not None else []
        st.markdown(f'<div class="pm-item"><div class="pm-item-title">Running for real: {esc(", ".join(on)) if on else "none (all dry runs)" if saved_actions is not None else "the app setting applies"}</div>'
                    '<div class="pm-item-action">Every action is logged and limited to 5 an hour. Anything off the allowlist always needs a person. '
                    'The watcher reads this every minute.</div></div>', unsafe_allow_html=True)
        picked = st.multiselect("Actions allowed to run", list(ACTION_HELP), default=on, format_func=ACTION_HELP.get)
        if st.button("Save automatic actions"):
            _store.set_setting("actions", ",".join(picked), who=str(st.session_state.get("who", "dashboard")), scope=_scope)
            st.session_state["flash"] = "Saved. Running for real: " + (", ".join(picked) or "none")
            st.rerun()

    section("Email alerts", "Where PipeMedic sends its failure emails.")
    if _store is None:
        st.info("Sign in to Azure (and install the Azure packages) to change the alert address.")
    else:
        import re as _re
        saved_to = _store.get_setting("notify_to", None, scope=_scope_project)
        st.markdown(f'<div class="pm-item"><div class="pm-item-title">Current: {esc(saved_to) if saved_to else "the person who triggered the run, or the default address"}</div>'
                    '<div class="pm-item-action">An address here receives every alert. Leave it empty to email the person who triggered each run. '
                    'The watcher reads this every minute.</div></div>', unsafe_allow_html=True)
        new_to = st.text_input("Send every alert to", value=saved_to or "", placeholder="name@company.com")
        if st.button("Save alert address"):
            if new_to.strip() and not _re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", new_to.strip()):
                st.error("That does not look like an email address.")
            else:
                _store.set_setting("notify_to", new_to.strip(), who=str(st.session_state.get("who", "dashboard")), scope=_scope)
                st.session_state["flash"] = "Saved. Alerts go to " + (new_to.strip() or "the person who triggered the run") + "."
                st.rerun()

    section("Action log")
    log = load_history()
    if log:
        df = pd.DataFrame(log)
        df["time"] = pd.to_datetime(df["time"], utc=True)
        df["time"] = local_time(df["time"]).dt.strftime("%b %d, %H:%M")
        st.dataframe(df[["time", "build_id", "pipeline", "action", "status", "detail"]].iloc[::-1], hide_index=True, width="stretch")
    else:
        st.markdown('<div class="pm-item"><div class="pm-item-title">No automatic actions have run yet.</div>'
                    '<div class="pm-item-action">Run <code>python pipemedic_agent.py --last 3 --act</code> to let the agent act within the allowlist. '
                    'Use <code>--remediate</code> instead to preview the plan without changing anything.</div></div>', unsafe_allow_html=True)
