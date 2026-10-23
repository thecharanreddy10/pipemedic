"""PipeMedic dashboard, kept simple: pick where to look, see the runs and whether they passed, open one to see why it failed.
A second page shows the health of every runner.

Run:  streamlit run dashboard.py      (sign in first with az login)
The earlier, larger dashboard is kept as dashboard_legacy.py.
"""
import html
import os
import sys
from datetime import datetime, timedelta, timezone

import altair as alt
import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ui_data as D  # noqa: E402
from ado_browse import agents_involved, branch_name, duration_label, job_rows, pipeline_rows, seconds_between, status_of  # noqa: E402
import fixplan  # noqa: E402
from fix_review import change_diff  # noqa: E402
from history import availability, bucket_minutes, duration_text, silent_periods  # noqa: E402

st.set_page_config(page_title="PipeMedic", page_icon="🩺", layout="wide", initial_sidebar_state="collapsed")

# ------------------------------------------------------------------ look
DARK = ((getattr(st.context, "theme", None) and st.context.theme.type) or "light") == "dark"
T = {
    "surface": "#1a1a19" if DARK else "#fcfcfb", "ink": "#f0efec" if DARK else "#0b0b0b", "ink2": "#c3c2b7" if DARK else "#52514e",
    "muted": "#898781", "grid": "#2c2c2a" if DARK else "#e1e0d9", "axis": "#383835" if DARK else "#c3c2b7",
    "border": "rgba(255,255,255,0.10)" if DARK else "rgba(11,11,11,0.10)", "chip": "rgba(255,255,255,0.06)" if DARK else "rgba(11,11,11,0.05)",
    "series": ["#3987e5", "#d95926", "#199e70", "#c98500"] if DARK else ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"],
    "good": "#0ca30c", "warning": "#fab219", "critical": "#d03b3b",
}
STATUS_COLOUR = {"good": T["good"], "critical": T["critical"], "warning": T["warning"], "info": T["series"][0], "muted": T["muted"]}
STATUS_ICON = {"good": "✓", "critical": "✕", "warning": "!", "info": "…", "muted": "–"}
VERDICT = {"runner": ("The runner", T["series"][0]), "code-or-config": ("The code or configuration", "#9085e9" if DARK else "#6250d6"),
           "flaky": ("A flaky test", "#199e70" if DARK else "#1baf7a"), "mixed": ("Code, and the runner was strained", "#d55181" if DARK else "#e87ba4"),
           "unknown": ("Not recognised", T["muted"])}
FONT = "system-ui, -apple-system, 'Segoe UI', sans-serif"

st.markdown(f"""<style>
:root {{ --ink:{T['ink']}; --ink2:{T['ink2']}; --muted:{T['muted']}; --surface:{T['surface']}; --border:{T['border']}; --chip:{T['chip']}; }}
html, body, [class*="css"], .stApp {{ font-family:{FONT}; }}
.block-container {{ padding-top:3.5rem; padding-bottom:3rem; max-width:1200px; }}
.title {{ font-size:28px; font-weight:650; margin:0; color:var(--ink); }}
.sub {{ font-size:14px; color:var(--ink2); margin:4px 0 16px 0; }}
.section {{ font-size:17px; font-weight:600; color:var(--ink); margin:26px 0 8px 0; }}
.card {{ background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:16px 18px; }}
.row-title {{ font-size:15px; font-weight:600; color:var(--ink); }}
.meta {{ font-size:13px; color:var(--muted); }}
.cause {{ font-size:16px; color:var(--ink); line-height:1.5; margin:10px 0 4px 0; }}
.stats {{ font-size:13.5px; color:var(--ink2); line-height:1.7; }}
.stats b {{ color:var(--ink); font-weight:600; }}
.badge {{ display:inline-flex; align-items:center; gap:7px; font-size:13px; font-weight:600; color:var(--ink); white-space:nowrap; }}
.badge i {{ font-style:normal; width:20px; height:20px; border-radius:50%; display:inline-flex; align-items:center; justify-content:center; font-size:12px; color:#fff; }}
.pill {{ display:inline-flex; align-items:center; gap:7px; font-size:13px; font-weight:500; background:var(--chip); border-radius:999px; padding:3px 11px 3px 9px; color:var(--ink); }}
.dot {{ width:9px; height:9px; border-radius:50%; display:inline-block; }}
@media (max-width: 720px) {{ .block-container {{ padding-left:1rem; padding-right:1rem; }} }}
</style>""", unsafe_allow_html=True)


def esc(x):
    return html.escape(str(x))


def badge(colour, label):
    return f'<span class="badge"><i style="background:{STATUS_COLOUR[colour]}">{STATUS_ICON[colour]}</i>{esc(label)}</span>'


def section(text):
    st.markdown(f'<div class="section">{esc(text)}</div>', unsafe_allow_html=True)


def when(iso):
    if not iso:
        return "-"
    return pd.Timestamp(iso).tz_convert(datetime.now().astimezone().tzinfo).strftime("%d %b %H:%M")


def local_time(series):
    return series.dt.tz_convert(datetime.now().astimezone().tzinfo).dt.tz_localize(None)


def choose(label, names, wanted, key):
    """A dropdown when the choices could be listed, otherwise a text box."""
    if names:
        return st.selectbox(label, names, index=names.index(wanted) if wanted in names else 0, key=key)
    return st.text_input(label, wanted, key=key + "::text")


# ------------------------------------------------------------------ header
st.markdown('<div class="title">🩺 PipeMedic</div><div class="sub">Pipeline runs, why they failed, and how the runners are doing</div>', unsafe_allow_html=True)
if not D.can_sign_in():
    st.error("Not signed in to Azure. Run `az login` in a terminal and reload this page (or set AZDO_PAT to an Azure DevOps token).")
    st.stop()
token = D.auth()
page_runs, page_runners = st.tabs(["Pipelines", "Runners"])


# ================================================================== PIPELINES
def runner_line(h):
    if not h or not h.get("samples"):
        return "No runner health was recorded for this time."
    text = f'CPU peak <b>{h["cpu_max"]:.0f}%</b> · memory peak <b>{h["memory_max"]:.0f}%</b> · disk <b>{h["disk_max"]:.0f}%</b> used'
    flags = [f for f in h.get("flags", []) if f != "no-health-data"]
    return text + (f' · <b style="color:{T["critical"]}">{esc(", ".join(flags))}</b>' if flags else " · no problems seen")


def failure_view(d, source, bid, can_read_logs):
    """The reason a run failed, in plain language, with the evidence behind it."""
    label, colour = VERDICT.get(d["verdict"], VERDICT["unknown"])
    left, right = st.columns([2, 1], gap="large")
    with left:
        st.markdown(f'<span class="pill"><span class="dot" style="background:{colour}"></span>Caused by: {esc(label)}</span>'
                    f'<div class="cause">{esc(d["root_cause"])}</div>'
                    f'<div class="meta">Failed step: <b>{esc(d["failed_step"])}</b> · category: {esc(d["category"])}</div>', unsafe_allow_html=True)
    with right:
        st.markdown(f'<div class="card"><div class="row-title">Runner {esc(d.get("hostname") or d.get("agent") or "unknown")}</div>'
                    f'<div class="stats" style="margin-top:6px">{runner_line(d.get("runner_health"))}</div></div>', unsafe_allow_html=True)

    section("What the logs show")
    st.code("\n".join(d["evidence"]) or "No matching log lines.", language="text")
    section("What to do")
    for s in d["suggestions"][:4]:
        st.markdown(f"- {s}")

    others, skipped = d.get("other_problems") or [], d.get("not_reached") or []
    if others or skipped:
        section("Other problems the pipeline did not reach")
        for o in others:
            st.markdown(f"- `{o['file']}` line {o['line']}: {o['message']}")
        if not others:
            st.markdown("No other problems found in what this commit changed.")
        if skipped:
            st.caption("Steps that did not run, so they were not checked: " + ", ".join(skipped)
                       + ". The fix plan below deals only with the first failure.")

    rl = d.get("runner_logs") or {}
    if rl.get("available"):
        n = len(rl.get("findings", []))
        with st.expander(f"The runner's own logs around the failure ({rl.get('window', '')}): " + (f"{n} problem{'s' if n != 1 else ''} found" if n else "no problems found")):
            for f in rl.get("findings", []):
                st.markdown(f"- **{f['message']}** `{f['line']}`")
            st.code(rl["text"] or "No log lines.", language="text")
    elif can_read_logs and d.get("hostname"):
        if st.button("Read the runner's logs for this run", key=f"rl_btn_{bid}", icon=":material/dns:",
                     help="Runs a read-only command on the runner to read its system, kernel and agent logs for the time of the run."):
            st.session_state[f"rl::{bid}"] = True
            st.rerun()
        if rl.get("error"):
            st.caption(f"The runner's logs were not available: {rl['error']}.")

    ai = d.get("ai") if isinstance(d.get("ai"), dict) and d["ai"].get("likely_cause") else None
    if ai:
        section("The AI's opinion")
        st.markdown(f"{ai['likely_cause']}")
        if ai.get("runner_effect"):
            st.markdown(f"**Did the runner affect it?** {ai['runner_effect']}")
        st.caption(f"Written by {ai.get('model', 'the model')}. The verdict above comes from rules, not from the model.")
    st.caption(source)


PLAN_STATUS = {"proposed": ("info", "Waiting for approval"), "approved": ("info", "Approved, starting"), "running": ("info", "Being carried out"),
               "rejected": ("muted", "Rejected"), "superseded": ("muted", "Not needed: another plan was approved"), "failed": ("critical", "Could not be carried out")}
VERIFIED = {"passed": ("good", "Done, and the pipeline passed"), "failed": ("critical", "Done, but the pipeline failed again"),
            "next-problem": ("warning", "Fixed; the pipeline now stops at a later problem"),
            "pending": ("info", "Done, waiting for the pipeline result")}


def plan_badge(p):
    if p.get("status") == "done":
        return badge(*VERIFIED.get(p.get("verified"), VERIFIED["pending"]))
    if p.get("status") == "proposed" and fixplan.is_runnable(p):
        return badge("muted", "Expired" if p.get("kind") != "manual" else "For a person to do")
    return badge(*PLAN_STATUS.get(p.get("status"), ("muted", p.get("status", "-"))))


def fix_section(org, project, bid):
    """The fixes PipeMedic offers for this run. Nothing is done until someone approves one here or from the email."""
    try:
        plans = D.fix_plans(org, project, bid)
    except Exception as e:
        st.caption(f"Could not load the fix plans: {e}")
        return
    if not plans:
        return
    section("Fix it")
    st.caption("PipeMedic made these plans when the run failed. Read a plan, then approve it to let PipeMedic carry it out. "
               "Code changes arrive as a pull request that a person still has to merge.")
    who = D.signed_in_user()
    for p in plans:
        with st.container(border=True):
            st.markdown(f'{plan_badge(p)}<div class="row-title" style="margin-top:8px">{esc(p["title"])}</div>'
                        f'<div class="meta">Risk: {esc(p.get("risk") or "-")}</div>', unsafe_allow_html=True)
            with st.expander("What it will do"):
                for i, s in enumerate(p.get("steps", []), 1):
                    st.markdown(f"{i}. {s}")
                st.markdown(f"**How the result is checked:** {p.get('check', '')}  \n**How to undo it:** {p.get('undo', '')}")
                if p.get("reason"):
                    st.markdown(f"**Why PipeMedic will not change this itself:** {p['reason']}")
                if p.get("explanation"):
                    st.markdown(f"**Why this change** (AI, {p.get('confidence', '')} confidence): {p['explanation']}")
                for c in p.get("changes") or []:
                    st.markdown(f"`{c['action']} {c['path']}`")
                    st.code(change_diff(c, c.get("old_content", "")), language="diff")
                if p.get("process"):
                    pr = p["process"]
                    st.markdown(f"**Process to stop:** `{pr.get('comm')}` (process {pr.get('pid')}, user {pr.get('user')}): `{pr.get('args', '')}`")
            r = p.get("result") or {}
            if r:
                facts = []
                if r.get("pr_id"):
                    repo = (p.get("diagnosis") or {}).get("repository") or project
                    facts.append(f"[Pull request {r['pr_id']}](https://dev.azure.com/{org}/{project}/_git/{repo}/pullrequest/{r['pr_id']}) "
                                 f"into {r.get('base')}; proving run {r.get('proving_run')}")
                if r.get("disk"):
                    facts.append(f"Free disk went from {r['disk'].get('free_mb_before')} MB to {r['disk'].get('free_mb_after')} MB")
                if r.get("process"):
                    facts.append(f"Stopping the process: {r['process'].get('detail')}")
                if r.get("docker"):
                    facts.append("Docker is running again" if r["docker"].get("ok") else f"Docker is {r['docker'].get('state')}")
                if r.get("rerun"):
                    facts.append(f"[Re-run {r['rerun']}](https://dev.azure.com/{org}/{project}/_build/results?buildId={r['rerun']})")
                if r.get("error"):
                    facts.append(f"Error: {r['error']}")
                st.markdown("  \n".join(facts))
            if p.get("decided_by"):
                st.caption(f"{'Approved' if p.get('status') != 'rejected' else 'Rejected'} by {p['decided_by']} from the {p.get('decided_via')}.")
            if not fixplan.is_runnable(p):
                if not who:
                    st.caption("Sign in with az login to approve plans here, or use the link in the failure email.")
                    continue
                key = f"plan::{p['plan_id']}"
                if st.session_state.get(key) == "confirm":
                    st.warning(f"Approve this plan as {who}? PipeMedic will start right away.")
                    c1, c2, _ = st.columns([1, 1, 4])
                    if c1.button("Yes, run it", key=key + "::yes", type="primary"):
                        ok, msg = D.decide_plan(bid, p["plan_id"], True, who)
                        st.session_state[key] = None
                        st.toast(msg, icon=":material/check_circle:" if ok else ":material/error:")
                        st.rerun()
                    if c2.button("Cancel", key=key + "::no"):
                        st.session_state[key] = None
                        st.rerun()
                else:
                    c1, c2, _ = st.columns([1, 1, 4])
                    if c1.button("Approve", key=key + "::approve", type="primary", icon=":material/check:"):
                        st.session_state[key] = "confirm"
                        st.rerun()
                    if c2.button("Reject", key=key + "::reject"):
                        ok, msg = D.decide_plan(bid, p["plan_id"], False, who)
                        st.toast(msg, icon=":material/check_circle:" if ok else ":material/error:")
                        st.rerun()


def run_detail(run, name, siblings, org, project):
    bid = run["id"]
    colour, label = status_of(run)
    who = (run.get("requestedFor") or {}).get("displayName", "-")
    took = duration_label(seconds_between(run.get("startTime"), run.get("finishTime")))
    link = f"https://dev.azure.com/{org}/{project}/_build/results?buildId={bid}"
    st.markdown(
        f'<div class="card" style="margin-top:14px">{badge(colour, label)}'
        f'<div class="row-title" style="margin-top:8px">{esc(name)} · run {esc(run["buildNumber"])}</div>'
        f'<div class="meta">Branch <b>{esc(branch_name(run.get("sourceBranch")))}</b> · started {esc(when(run.get("startTime") or run.get("queueTime")))} · '
        f'took {esc(took)} · by {esc(who)} · <a href="{esc(link)}" target="_blank">Open in Azure DevOps</a></div></div>', unsafe_allow_html=True)

    queue_pool = (run.get("queue") or {}).get("pool") or {}
    pool_name = queue_pool["name"] if queue_pool.get("name") and not queue_pool.get("isHosted") else D.cfg.pool

    if run.get("status") != "completed":
        st.info("This run has not finished. Its result and any cause will appear here when it does.")
    elif run.get("result") in ("failed", "canceled"):
        section("Why it failed")
        saved = D.saved_diagnoses(org, project).get(bid)
        with_logs = st.session_state.get(f"rl::{bid}", False)
        try:
            with st.spinner("Reading the logs..."):
                if saved and not with_logs:
                    d, source = saved, "This is the analysis the PipeMedic agent made when the run failed."
                else:
                    d = D.diagnosis(org, project, token, run, pool_name, D.assess_flaky(run, siblings), with_logs)
                    source = "Worked out just now from the run's logs and the runner's health."
            failure_view(d, source, bid, can_read_logs=True)
        except Exception as e:
            st.error(f"Could not work out why this run failed: {e}")
        fix_section(org, project, bid)
    else:
        st.markdown(f'<div class="card" style="margin-top:12px">{badge("good", "This run succeeded, so there is nothing to explain.")}</div>', unsafe_allow_html=True)

    try:
        jobs = job_rows(D.timeline(org, project, bid, token), D.agent_hosts(org, project, pool_name, token))
    except Exception:
        jobs = []
    if jobs:
        with st.expander(f"Where it ran ({len(agents_involved(jobs))} runner{'s' if len(agents_involved(jobs)) != 1 else ''})"):
            st.dataframe(pd.DataFrame([{"Job": j["name"], "Status": j["status"], "Agent": j["agent"] or "-", "Machine": j["host"] or "-",
                                        "Took": duration_label(j["seconds"])} for j in jobs]), hide_index=True, width="stretch")


with page_runs:
    try:
        org_names = D.organizations(token)
    except Exception:
        org_names = []
    c_org, c_project, c_repo = st.columns(3)
    with c_org:
        org = choose("Organization", org_names, D.cfg.org, "org")
    try:
        project_names = D.projects(org, token)
    except Exception:
        project_names = []
    with c_project:
        project = choose("Project", project_names, D.cfg.project, f"project::{org}")
    try:
        repo_list = D.repos(org, project, token)
    except Exception as e:
        repo_list = []
        st.error(f"Could not list the repositories of {org}/{project}: {e}")
    repo_name = repo_id = None
    with c_repo:
        if repo_list:
            repo_name = st.selectbox("Repository", [n for n, _ in repo_list], key=f"repo::{org}::{project}")
            repo_id = dict(repo_list)[repo_name]
        else:
            st.selectbox("Repository", [], key=f"repo_none::{org}::{project}", placeholder="No repositories found")

    if repo_id:
        try:
            names, runs = D.repo_runs(org, project, repo_id, token)
        except Exception as e:
            names, runs = {}, []
            st.error(f"Could not read the runs: {e}")
        rows = pipeline_rows([{"id": i, "name": n} for i, n in names.items()], runs)
        if not rows:
            st.info("No pipelines were found for this repository.")
        else:
            base = f"{org}::{project}::{repo_id}"
            pick_p, pick_r = f"pipeline::{base}", f"run::{base}"
            failed = ("failed", "canceled")
            if st.session_state.get(pick_p) not in [r["id"] for r in rows]:  # first visit: the newest pipeline whose last run failed
                st.session_state[pick_p] = next((r["id"] for r in rows if r["last"] and r["last"].get("result") in failed), rows[0]["id"])
            chosen = next(r for r in rows if r["id"] == st.session_state[pick_p])

            left, right = st.columns([5, 7], gap="large")
            with left:
                section("Pipelines")
                st.caption("Click a pipeline to see its runs.")
                for r in rows:
                    colour, label = status_of(r["last"]) if r["last"] else ("muted", "No runs yet")
                    if st.button(f"{STATUS_ICON[colour]}  {r['name']}", key=f"p::{base}::{r['id']}", width="stretch",
                                 type="primary" if r["id"] == chosen["id"] else "secondary"):
                        st.session_state[pick_p] = r["id"]
                        st.session_state.pop(pick_r, None)
                        st.rerun()
                    rate = f" · passed {r['pass_rate']}% of the last {r['finished']}" if r["pass_rate"] is not None else ""
                    st.markdown(f'<div class="meta" style="margin:-8px 0 10px 4px">last run {esc(label.lower())}{esc(rate)}</div>', unsafe_allow_html=True)

                try:
                    p_runs = D.pipeline_runs(org, project, chosen["id"], token)
                except Exception as e:
                    p_runs = []
                    st.error(f"Could not list the runs: {e}")
                section(f"Runs of {chosen['name']}")
                show = st.segmented_control("Show", ["All", "Failed", "Succeeded"], default="All", key=f"filter::{base}", label_visibility="collapsed") or "All"
                shown = [r for r in p_runs if show == "All" or (show == "Failed" and r.get("result") in failed)
                         or (show == "Succeeded" and r.get("result") == "succeeded")]
                if not shown:
                    st.info("No runs match." if p_runs else "This pipeline has not run yet.")
                else:
                    if st.session_state.get(pick_r) not in [r["id"] for r in shown]:  # the newest failed run, else the newest run
                        st.session_state[pick_r] = next((r["id"] for r in shown if r.get("result") in failed), shown[0]["id"])
                    st.caption("Click a run to see what happened.")
                    for r in shown[:25]:
                        colour, label = status_of(r)
                        text = (f"{STATUS_ICON[colour]}  {r['buildNumber']}  ·  {label}  ·  {branch_name(r.get('sourceBranch'))}  ·  "
                                f"{when(r.get('startTime') or r.get('queueTime'))}  ·  {duration_label(seconds_between(r.get('startTime'), r.get('finishTime')))}")
                        if st.button(text, key=f"r::{base}::{r['id']}", width="stretch",
                                     type="primary" if r["id"] == st.session_state[pick_r] else "secondary"):
                            st.session_state[pick_r] = r["id"]
                            st.rerun()

            with right:
                run = next((r for r in p_runs if r["id"] == st.session_state.get(pick_r)), None)
                if run:
                    run_detail(run, chosen["name"], p_runs, org, project)
                else:
                    st.info("Select a run to see its details.")



# ================================================================== RUNNERS
RANGES = {60: "1 hour", 360: "6 hours", 1440: "24 hours", 4320: "3 days", 10080: "7 days"}

with page_runners:
    minutes = st.segmented_control("Show the last", list(RANGES), default=1440, format_func=RANGES.get, key="range") or 1440
    try:
        hist = D.health_history(minutes)
    except Exception as e:
        hist = pd.DataFrame()
        st.error(f"Could not read runner health ({e}).")
    if hist.empty:
        st.info("No health readings in this time. Check that the runner VMs are running and their health timer is active.")
    else:
        now = datetime.now(timezone.utc)
        start = now - timedelta(minutes=minutes)
        names_r = sorted(hist["hostname"].unique())
        colour_of = {h: T["series"][i % len(T["series"])] for i, h in enumerate(names_r)}
        gaps = silent_periods(hist.to_dict("records"), now, start)

        section("Now")
        for col, host in zip(st.columns(len(names_r)), names_r):
            rows = hist[hist["hostname"] == host].sort_values("time")
            last = rows.iloc[-1]
            age = (now - last["time"].to_pydatetime()).total_seconds()
            if age > 180:
                colour, text = "warning", f"Not reporting for {duration_text(age)}"
            elif max(last["cpu_percent"], last["memory_percent"], last["disk_percent"]) >= 90:
                colour, text = "warning", "Under strain"
            else:
                colour, text = "good", "Healthy"
            col.markdown(
                f'<div class="card"><div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">'
                f'<span class="row-title"><span class="dot" style="background:{colour_of[host]};margin-right:8px"></span>{esc(host)}</span>{badge(colour, text)}</div>'
                f'<div class="stats" style="margin-top:10px">CPU <b>{last["cpu_percent"]:.0f}%</b> · memory <b>{last["memory_percent"]:.0f}%</b> · '
                f'disk <b>{last["disk_percent"]:.0f}%</b> ({last["disk_free_gb"]:.0f} GB free)<br>'
                f'Reporting <b>{availability(gaps, host, start, now):.0f}%</b> of the last {RANGES[minutes]} · peak CPU <b>{rows["cpu_percent"].max():.0f}%</b>, '
                f'peak memory <b>{rows["memory_percent"].max():.0f}%</b></div></div>', unsafe_allow_html=True)

        bucket = bucket_minutes(minutes)
        lo = local_time(pd.Series([pd.Timestamp(start)])).iloc[0]
        hi = local_time(pd.Series([pd.Timestamp(now)])).iloc[0]
        dt = lambda x: alt.DateTime(year=x.year, month=x.month, date=x.day, hours=x.hour, minutes=x.minute)  # noqa: E731

        def line_chart(field, title):
            d = hist[["time", "hostname", field]].dropna().set_index("time").groupby("hostname")[field].resample(f"{bucket}min").max().reset_index()  # the highest reading, so a short peak is not averaged away
            d.columns = ["runner", "time", "value"]
            d["time"] = local_time(d["time"])
            d["seg"] = d["value"].isna().groupby(d["runner"]).cumsum()  # a gap in the readings breaks the line
            d = d.dropna(subset=["value"])
            d["key"] = d["runner"] + "#" + d["seg"].astype(str)
            base = alt.Chart(d).encode(
                x=alt.X("time:T", title=None, scale=alt.Scale(domain=[dt(lo), dt(hi)]),
                        axis=alt.Axis(format="%H:%M" if minutes <= 1440 else "%d %b %H:%M", grid=False, labelOverlap=True)),
                y=alt.Y("value:Q", title=None, scale=alt.Scale(domain=[0, 100]), axis=alt.Axis(values=[0, 25, 50, 75, 100], format=".0f")),
                color=alt.Color("runner:N", scale=alt.Scale(domain=names_r, range=[colour_of[x] for x in names_r]), legend=alt.Legend(title=None, orient="top")))
            line = base.mark_line(strokeWidth=2).encode(detail="key:N")
            tip = [alt.Tooltip("time:T", title="Time", format="%d %b %H:%M"), alt.Tooltip("runner:N"), alt.Tooltip("value:Q", title=(f"{title}, highest in {bucket} min" if bucket > 1 else title), format=".0f")]
            points = base.mark_point(filled=True, size=40, opacity=0).encode(tooltip=tip)
            limit = alt.Chart(pd.DataFrame({"y": [90]})).mark_rule(color=T["muted"], strokeWidth=1, strokeDash=[4, 4]).encode(y="y:Q")
            return (limit + line + points).properties(height=200).configure(background="transparent", font=FONT).configure_view(stroke=None).configure_axis(
                labelColor=T["muted"], gridColor=T["grid"], domainColor=T["axis"], tickColor=T["axis"], labelFontSize=12)

        section(f"Over the last {RANGES[minutes]}")
        st.caption(("Each point is the highest reading in " + (f"{bucket} minutes" if bucket > 1 else "that minute") + ", so short peaks show. ")
                   + "Each line is one runner. A broken line means the runner sent no readings. The dashed line is 90%.")
        for col, (field, title) in zip(st.columns(3, gap="medium"), (("cpu_percent", "CPU %"), ("memory_percent", "Memory %"), ("disk_percent", "Disk used %"))):
            with col:
                st.markdown(f'<div class="meta" style="margin-bottom:4px"><b style="color:var(--ink)">{esc(title)}</b></div>', unsafe_allow_html=True)
                with st.container(border=True):
                    st.altair_chart(line_chart(field, title), width="stretch")

        section("Times a runner stopped reporting")
        if not gaps:
            st.markdown(f'<div class="card">{badge("good", "No gaps in this time.")}</div>', unsafe_allow_html=True)
        else:
            st.caption("A gap longer than 3 minutes: the VM was off, or its health timer or the network stopped.")
            st.dataframe(pd.DataFrame([{
                "Runner": g["host"], "From": "start of this time (no readings yet)" if g["before_window"] else when(g["start"].isoformat()),
                "To": "still silent" if g["ongoing"] else when(g["end"].isoformat()), "Length": duration_text(g["seconds"])}
                for g in sorted(gaps, key=lambda g: g["start"], reverse=True)]), hide_index=True, width="stretch")
