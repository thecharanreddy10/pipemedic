"""Failure emails through Azure Communication Services (sign-in with managed identity, no keys).

The email says what failed, why, and what to do. It is advice for a person: the verdict comes from the rules,
and any AI opinion is labelled as AI-written.
"""
import html
import re

VERDICT_LABEL = {"runner": "Runner problem", "code-or-config": "Code or configuration", "flaky": "Flaky test",
                 "mixed": "Code problem, runner also strained", "unknown": "Needs a person: cause not recognised"}
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def recipient_for(d: dict, default: str) -> str:
    """The person who triggered the run, if that is an email address; otherwise the default owner."""
    who = (d.get("requested_for") or "").strip()
    return who if _EMAIL.match(who) else default


def run_url(org: str, project: str, build_id: int) -> str:
    return f"https://dev.azure.com/{org}/{project}/_build/results?buildId={build_id}"


def render_email(d: dict, url: str, actions: list[str] | None = None) -> tuple[str, str, str]:
    """Return (subject, plain text, html)."""
    label = VERDICT_LABEL.get(d["verdict"], d["verdict"])
    subject = f"[PipeMedic] {d['pipeline']} run {d['build_id']} failed: {label}"
    h = d.get("runner_health", {})
    health = (f"CPU peak {h['cpu_max']}%, memory peak {h['memory_max']}%, disk {h['disk_max']}% used"
              + (f" (flags: {', '.join(h['flags'])})" if h.get("flags") else "")) if h.get("samples") else \
        "no runner health samples for this run"
    fixes = d.get("suggestions", [])[:3]
    evidence = d.get("evidence", [])[:2]
    ai = d.get("ai") if d.get("ai") and not d["ai"].get("error") else None
    similar = d.get("similar")

    lines = [f"{d['pipeline']} (run {d['build_id']}) failed at: {d['failed_step']}", "",
             f"Verdict: {label}", f"Why: {d['root_cause']}", f"Runner: {d.get('hostname') or d.get('agent') or 'unknown'} - {health}", ""]
    if evidence:
        lines += ["Evidence:"] + [f"  {e}" for e in evidence] + [""]
    if fixes:
        lines += ["What to do:"] + [f"  {i}. {f}" for i, f in enumerate(fixes, 1)] + [""]
    if similar:
        lines += [f"Seen before: {similar['summary']}", ""]
    if ai:
        lines += [f"AI second opinion ({ai['model']}): {ai['likely_cause']}", ""]
    fix = d.get("fix") if d.get("fix") and d["fix"].get("status") in ("opened", "proposed") else None
    if fix:
        head = (f"A fix was opened for review: {fix['pr_url']}" if fix["status"] == "opened"
                else "A fix was drafted (not opened)")
        lines += [f"{head}", f"  {fix['summary']}"] + [f"  {f}" for f in fix["files"]] + [
            "  AI-written: a person must review it. PipeMedic never merges.", ""]
    plans = [p for p in d.get("plans") or [] if p["kind"] != "manual"]
    if plans:
        lines += ["PipeMedic can fix this once you approve. Open a plan to see exactly what it will do:"]
        for i, p in enumerate(plans, 1):
            lines += [f"  {i}. {p['title']}"] + ([f"     Review and approve: {p['link']}"] if p.get("link") else [])
        lines += [
            "  Or use the Fix it section for this run in the PipeMedic dashboard. Nothing happens until someone approves.", ""]
    if actions:
        lines += ["Automatic actions:"] + [f"  {a}" for a in actions] + [""]
    lines += [f"Open the run: {url}", "", "Sent by PipeMedic. The verdict comes from rules; AI text is labelled."]
    text = "\n".join(lines)

    e = html.escape
    li = lambda items: "".join(f"<li>{e(x)}</li>" for x in items)  # noqa: E731
    body = (f'<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#1f2937;max-width:640px">'
            f'<h2 style="margin:0 0 4px 0">{e(d["pipeline"])} run {d["build_id"]} failed</h2>'
            f'<div style="color:#6b7280;margin-bottom:14px">{e(d["failed_step"])}</div>'
            f'<div style="border-left:4px solid #d03b3b;background:#f9fafb;padding:10px 14px;margin-bottom:14px">'
            f'<b>{e(label)}</b><br>{e(d["root_cause"])}</div>'
            f'<div><b>Runner:</b> {e(d.get("hostname") or d.get("agent") or "unknown")} &middot; {e(health)}</div>')
    if evidence:
        body += f'<p><b>Evidence</b></p><pre style="background:#f3f4f6;padding:8px;white-space:pre-wrap">{e(chr(10).join(evidence))}</pre>'
    if fixes:
        body += f"<p><b>What to do</b></p><ol>{li(fixes)}</ol>"
    if similar:
        body += f'<p><b>Seen before:</b> {e(similar["summary"])}</p>'
    if ai:
        body += (f'<p style="background:#eef4fb;padding:8px 12px"><b>AI second opinion</b> ({e(ai["model"])}): '
                 f'{e(ai["likely_cause"])}</p>')
    if fix:
        link = f' <a href="{e(fix["pr_url"])}">Review the pull request</a>' if fix["status"] == "opened" and fix.get("pr_url") else ""
        body += (f'<p style="background:#f0f7ee;padding:8px 12px"><b>Proposed fix</b> ({e(fix["confidence"])} confidence): '
                 f'{e(fix["summary"])}<br>{e(", ".join(fix["files"]))}<br>AI-written. A person must review it; '
                 f'PipeMedic never merges.{link}</p>')
    if plans:
        rows = "".join(f'<li style="margin-bottom:6px">{e(p["title"])} <span style="color:#6b7280">({e(p.get("risk") or "")})</span>'
                       + (f'<br><a href="{e(p["link"])}">Review and approve</a>' if p.get("link") else "") + "</li>" for p in plans)
        body += (f'<div style="border:1px solid #d1d5db;padding:8px 14px;margin:14px 0"><b>PipeMedic can fix this once you approve</b>'
                 f'<ol style="padding-left:20px">{rows}</ol><div style="color:#6b7280;font-size:12px">Each link opens a page that shows '
                 f'the plan in full. Nothing happens until someone presses Approve there or in the dashboard.</div></div>')
    if actions:
        body += f"<p><b>Automatic actions</b></p><ul>{li(actions)}</ul>"
    body += (f'<p><a href="{e(url)}">Open the run in Azure DevOps</a></p>'
             '<p style="color:#9ca3af;font-size:12px">Sent by PipeMedic. The verdict comes from rules; AI text is labelled.</p></div>')
    return subject, text, body


def render_pool_email(raised: list[dict], resolved: list[dict], pool: str) -> tuple[str, str, str]:
    """Return (subject, plain text, html) for new and cleared pool or runner problems."""
    if raised:
        worst = "critical" if any(r["level"] == "critical" for r in raised) else "warning"
        subject = f"[PipeMedic] Pool {pool}: {len(raised)} new problem{'s' if len(raised) != 1 else ''} ({worst})"
    else:
        subject = f"[PipeMedic] Pool {pool}: resolved ({len(resolved)})"

    lines = [f"Agent pool: {pool}", ""]
    if raised:
        lines.append("New problems:")
        for r in raised:
            lines += [f"  [{r['level']}] {r['message']}" + (" (now critical)" if r.get("escalated") else ""), f"      What to do: {r['suggestion']}"]
        lines.append("")
    if resolved:
        lines.append("Cleared:")
        lines += [f"  {r['message']} (since {r['since']})" for r in resolved]
        lines.append("")
    lines.append("Sent by PipeMedic. Each problem is sent once when it appears and once when it clears.")
    text = "\n".join(lines)

    e = html.escape
    colour = {"critical": "#d03b3b", "warning": "#c98a1a"}
    body = (f'<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#1f2937;max-width:640px">'
            f'<h2 style="margin:0 0 12px 0">Agent pool {e(pool)}</h2>')
    for r in raised:
        note = " (now critical)" if r.get("escalated") else ""
        body += (f'<div style="border-left:4px solid {colour.get(r["level"], "#6b7280")};background:#f9fafb;padding:10px 14px;margin-bottom:10px">'
                 f'<b>{e(r["level"].capitalize())}{note}</b><br>{e(r["message"])}<br>'
                 f'<span style="color:#6b7280">What to do: {e(r["suggestion"])}</span></div>')
    if resolved:
        body += "<p><b>Cleared</b></p><ul>" + "".join(f'<li>{e(r["message"])}</li>' for r in resolved) + "</ul>"
    body += '<p style="color:#9ca3af;font-size:12px">Sent by PipeMedic. Each problem is sent once when it appears and once when it clears.</p></div>'
    return subject, text, body


def send_email(endpoint: str, sender: str, to: str, subject: str, text: str, body_html: str, credential, wait_s: int = 40):
    """Send through Azure Communication Services. Returns the service's status string."""
    from azure.communication.email import EmailClient

    import time

    from azure.core.exceptions import HttpResponseError

    client = EmailClient(endpoint, credential)
    message = {"senderAddress": sender, "recipients": {"to": [{"address": to}]},
               "content": {"subject": subject, "plainText": text, "html": body_html}}
    for attempt in range(3):  # the service limits how many emails a domain sends a minute; a short wait usually clears it
        try:
            poller = client.begin_send(message)
            break
        except HttpResponseError as e:
            if e.status_code != 429 or attempt == 2:
                raise
            time.sleep(8 * (attempt + 1))
    result = poller.result(timeout=wait_s)
    return str(result.get("status", "unknown"))
