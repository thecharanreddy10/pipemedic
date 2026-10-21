"""The page behind an email's approval link, and the email that reports how a fix went.

Opening the link only shows the plan. The plan is carried out only when the person presses Approve on the page (a form
POST that carries the same signed token), so a mail scanner that opens links cannot approve anything.
"""
import difflib
import html

e = html.escape

STYLE = ("body{font-family:Segoe UI,Arial,sans-serif;font-size:15px;color:#1f2937;background:#f6f7f9;margin:0;padding:24px 16px}"
         "main{max-width:760px;margin:0 auto;background:#fff;border:1px solid #e3e6ea;border-radius:6px;padding:20px 24px}"
         "h1{font-size:20px;margin:0 0 4px}.sub{color:#6b7280;margin-bottom:16px}dt{font-weight:600;margin-top:12px}dd{margin:4px 0 0}"
         "ol{padding-left:22px}pre{background:#f3f4f6;padding:10px;overflow-x:auto;font-size:12px;white-space:pre}"
         ".risk{border-left:4px solid #c27c0e;background:#fdf8ec;padding:8px 12px;margin:14px 0}"
         ".ok{border-left:4px solid #2f8a4a;background:#eff8f1;padding:10px 14px}.no{border-left:4px solid #b42318;background:#fdf1f0;padding:10px 14px}"
         "form{display:flex;gap:10px;flex-wrap:wrap;margin-top:18px}button{font-size:15px;padding:9px 18px;border-radius:5px;cursor:pointer;border:1px solid #9ca3af;background:#fff}"
         "button.approve{background:#1f6f43;border-color:#1f6f43;color:#fff}button:focus-visible{outline:3px solid #7aa7e0;outline-offset:2px}")


def _page(title: str, inner: str) -> str:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<meta name="robots" content="noindex"><title>{e(title)}</title><style>{STYLE}</style></head><body><main>{inner}</main></body></html>')


def change_diff(change: dict, old: str = "") -> str:
    """A unified diff of one proposed file change (old is empty for a new file)."""
    lines = difflib.unified_diff(old.splitlines(), change["new_content"].splitlines(), "a/" + change["path"], "b/" + change["path"], lineterm="")
    return "\n".join(list(lines)[:400])


def plan_details(plan: dict) -> str:
    """The plan in full, as HTML: what will happen, what it changes, how it is checked, how to undo it."""
    steps = "".join(f"<li>{e(s)}</li>" for s in plan.get("steps", []))
    out = (f"<dl><dt>What PipeMedic will do</dt><dd><ol>{steps}</ol></dd>"
           f"<dt>How the result is checked</dt><dd>{e(plan.get('check', ''))}</dd>"
           f"<dt>How to undo it</dt><dd>{e(plan.get('undo', ''))}</dd></dl>"
           f'<div class="risk"><b>Risk:</b> {e(plan.get("risk", ""))}</div>')
    if plan.get("explanation"):
        out += f"<p><b>Why this change</b> (written by AI, {e(plan.get('confidence', ''))} confidence): {e(plan['explanation'])}</p>"
    for c in plan.get("changes") or []:
        out += f"<p><b>{e(c['action'])} {e(c['path'])}</b></p><pre>{e(change_diff(c, c.get('old_content', '')))}</pre>"
    if plan.get("process"):
        p = plan["process"]
        out += f"<p><b>Process to stop:</b> {e(str(p.get('comm')))} (process {e(str(p.get('pid')))}, user {e(str(p.get('user')))}): <code>{e(str(p.get('args', '')))}</code></p>"
    return out


def review_page(plan: dict, token: str, recipient: str, blocked: str | None = None) -> str:
    head = (f"<h1>{e(plan['title'])}</h1><div class=\"sub\">{e(str(plan.get('pipeline')))} run {plan['build_id']}"
            f" &middot; branch {e((plan.get('source_branch') or '').removeprefix('refs/heads/'))}</div>")
    if blocked:
        return _page(plan["title"], head + f'<div class="no">{e(blocked)}</div>' + plan_details(plan))
    form = (f'<form method="post"><input type="hidden" name="t" value="{e(token)}">'
            f'<button class="approve" type="submit" name="action" value="approve">Approve and run this fix</button>'
            f'<button type="submit" name="action" value="reject">Reject</button></form>'
            f'<p class="sub" style="margin-top:10px">You are deciding as {e(recipient)}. Nothing has happened yet.</p>')
    return _page(plan["title"], head + plan_details(plan) + form)


def message_page(title: str, text: str, good: bool) -> str:
    return _page(title, f"<h1>{e(title)}</h1><div class=\"{'ok' if good else 'no'}\">{e(text)}</div>")


def result_email(plan: dict, run_link: str, base_url: str) -> tuple[str, str, str]:
    """(subject, text, html) telling the person who approved a plan how it went."""
    r = plan.get("result") or {}
    status, verified = plan.get("status"), plan.get("verified", "")
    if status == "failed":
        head = f"The fix could not be carried out: {r.get('error', 'unknown error')}"
    elif verified == "passed":
        head = "The fix worked: the pipeline passed afterwards."
    elif verified == "failed":
        head = "The fix was carried out, but the pipeline failed again. PipeMedic will investigate the new run."
    else:
        head = "The fix was carried out. PipeMedic will report whether the pipeline passes."
    facts = []
    if r.get("pr_id"):
        facts.append(f"Pull request {r['pr_id']} into {r.get('base')}: {base_url}/pullrequest/{r['pr_id']}")
        facts.append(f"Proving run on {r.get('branch')}: run {r.get('proving_run')}")
    if r.get("disk"):
        facts.append(f"Free disk space went from {r['disk'].get('free_mb_before')} MB to {r['disk'].get('free_mb_after')} MB.")
    if r.get("process"):
        facts.append(f"Stopping the process: {r['process'].get('detail')}")
    if r.get("docker"):
        facts.append("Docker is running again." if r["docker"].get("ok") else f"Docker is {r['docker'].get('state')}.")
    if "agent_online" in r:
        facts.append("The agent is online again." if r["agent_online"] else "The agent did not come back online in time.")
    if r.get("rerun"):
        facts.append(f"Re-run: run {r['rerun']}")
    subject = f"[PipeMedic] Fix for {plan.get('pipeline')} run {plan['build_id']}: " + (
        "worked" if verified == "passed" else "failed" if status == "failed" or verified == "failed" else "carried out")
    text = "\n".join([plan["title"], "", head, ""] + [f"  {f}" for f in facts] + ["", f"Original failed run: {run_link}",
                     f"Approved by {plan.get('decided_by')} via {plan.get('decided_via')}."])
    body = (f'<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#1f2937;max-width:640px">'
            f'<h2 style="margin:0 0 6px">{e(plan["title"])}</h2>'
            f'<div style="border-left:4px solid {"#2f8a4a" if verified == "passed" else "#b42318" if status == "failed" or verified == "failed" else "#6b7280"};'
            f'background:#f9fafb;padding:10px 14px;margin-bottom:12px">{e(head)}</div>'
            + ("<ul>" + "".join(f"<li>{e(f)}</li>" for f in facts) + "</ul>" if facts else "")
            + f'<p><a href="{e(run_link)}">Original failed run</a></p><p style="color:#9ca3af;font-size:12px">Approved by '
            f'{e(str(plan.get("decided_by")))} via {e(str(plan.get("decided_via")))}. Sent by PipeMedic.</p></div>')
    return subject, text, body
