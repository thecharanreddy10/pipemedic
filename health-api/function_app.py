import json
import os
from datetime import datetime, timedelta, timezone

import azure.functions as func
from azure.data.tables import TableServiceClient

app = func.FunctionApp()

TABLE_NAME = "AgentHealth"


def _table():
    service = TableServiceClient.from_connection_string(os.environ["AzureWebJobsStorage"])
    return service.create_table_if_not_exists(TABLE_NAME)


def _json(body, status=200):
    return func.HttpResponse(json.dumps(body), status_code=status, mimetype="application/json")


@app.route(route="health", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def receive_health(req: func.HttpRequest) -> func.HttpResponse:
    """Receives a health report from a runner and stores it in Table Storage."""
    try:
        data = req.get_json()
        hostname = data["hostname"]
    except (ValueError, KeyError):
        return _json({"error": "JSON body with 'hostname' is required"}, 400)

    now = datetime.now(timezone.utc)
    entity = {
        "PartitionKey": hostname,
        # inverted ticks are not needed; ISO timestamps sort lexicographically
        "RowKey": now.strftime("%Y%m%dT%H%M%S%fZ"),
        "timestamp_utc": now.isoformat(),
    }
    for key, value in data.items():
        if key != "hostname" and isinstance(value, (int, float, str, bool)):
            entity[key] = value
    _table().create_entity(entity)
    return _json({"status": "stored", "hostname": hostname, "rowKey": entity["RowKey"]}, 201)


@app.route(route="health-history", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def health_history(req: func.HttpRequest) -> func.HttpResponse:
    """Returns stored health reports, optionally filtered by hostname and time window."""
    hostname = req.params.get("hostname")
    try:
        minutes = int(req.params.get("minutes", "60"))
    except ValueError:
        return _json({"error": "minutes must be an integer"}, 400)

    since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y%m%dT%H%M%S%fZ")
    flt = f"RowKey ge '{since}'"
    if hostname:
        safe = hostname.replace("'", "''")
        flt = f"PartitionKey eq '{safe}' and " + flt

    records = []
    for e in _table().query_entities(flt):
        record = {k: v for k, v in e.items() if k not in ("PartitionKey", "RowKey")}
        record["hostname"] = e["PartitionKey"]
        record["rowKey"] = e["RowKey"]
        records.append(record)
    records.sort(key=lambda r: r["rowKey"])
    return _json({"count": len(records), "minutes": minutes, "records": records})


EVENT_QUEUE = "pipemedic-events"


@app.route(route="ado-event", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
@app.queue_output(arg_name="msg", queue_name=EVENT_QUEUE, connection="AzureWebJobsStorage")
def ado_event(req: func.HttpRequest, msg: func.Out[str]) -> func.HttpResponse:
    """Azure DevOps "build completed" service hook. Queues a failed run for investigation and answers at once,
    because the investigation takes longer than a hook is willing to wait."""
    try:
        payload = req.get_json()
    except ValueError:
        return _json({"error": "JSON body required"}, 400)
    from event_hook import parse_event

    event = parse_event(payload)
    if event is None:
        return _json({"status": "ignored"})
    msg.set(json.dumps(event))
    return _json({"status": "queued", "build_id": event["build_id"]}, 202)


@app.queue_trigger(arg_name="msg", queue_name=EVENT_QUEUE, connection="AzureWebJobsStorage")
def process_event(msg: func.QueueMessage) -> None:
    """Investigates the run a hook queued: the failing logs, the runner's health and logs, the fix, and the email."""
    import logging

    try:
        import config
        from event_hook import is_for
        from watcher import handle_event

        event = json.loads(msg.get_body().decode("utf-8"))
        c = config.settings()
        if not is_for(event, c.org, c.project):
            logging.info("PipeMedic event ignored: it belongs to %s/%s", event.get("org"), event.get("project"))
            return
        logging.info("PipeMedic event: %s", handle_event(event["build_id"]))
    except Exception as e:  # not retried: the timer below picks up any run this missed
        logging.exception("PipeMedic event failed")
        try:  # there is no log to read in the cloud, so leave a record next to the run's other notes
            import config
            from azure_auth import get_credential
            from store import TableStore

            TableStore(config.settings().state_table_url, get_credential()).trace(
                json.loads(msg.get_body().decode("utf-8")).get("build_id", 0), f"process_event failed: {e.__class__.__name__}: {str(e)[:400]}")
        except Exception:
            pass


@app.timer_trigger(schedule="0 * * * * *", arg_name="timer", run_on_startup=False, use_monitor=False)
def watch_failures(timer: func.TimerRequest) -> None:
    """Every minute, as a safety net behind the hook: investigate any failed run it missed, act within guardrails, and email."""
    import logging

    try:
        from watcher import run_once

        logging.info("PipeMedic watcher: %s", run_once())
    except Exception:
        logging.exception("PipeMedic watcher failed")
    try:  # approved fixes whose re-run has finished: tell the person who approved it how it went
        from fix_executor import verify_pending

        told = verify_pending()
        if told:
            logging.info("PipeMedic fix results: %s", told)
    except Exception:
        logging.exception("PipeMedic fix check failed")


@app.route(route="version", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def version(req: func.HttpRequest) -> func.HttpResponse:
    """Which build of the code is running (written into the package when it is built). Used to confirm a deploy took effect."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "version.txt")
    try:
        return _json({"version": open(path, encoding="utf-8").read().strip()})
    except OSError:
        return _json({"version": "unknown"})


# ---------------- fix with approval ----------------
FIX_QUEUE = "pipemedic-fixes"


def _html(page: str, status: int = 200) -> func.HttpResponse:
    return func.HttpResponse(page, status_code=status, mimetype="text/html",
                             headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer"})


def _decide(build_id: int, plan_id: str, approve: bool, who: str, via: str, msg: func.Out[str]):
    import config
    from azure_auth import get_credential
    from fix_executor import decide
    from store import TableStore

    store = TableStore(config.settings().state_table_url, get_credential())
    ok, text, plan = decide(store, build_id, plan_id, approve, who, via)
    if ok and approve:
        msg.set(json.dumps({"build_id": build_id, "plan_id": plan_id}))
    return ok, text, plan


@app.route(route="fix/decide", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
@app.queue_output(arg_name="msg", queue_name=FIX_QUEUE, connection="AzureWebJobsStorage")
def fix_decide(req: func.HttpRequest, msg: func.Out[str]) -> func.HttpResponse:
    """The dashboard's Approve and Reject buttons. Body: {build_id, plan_id, approve, who}."""
    try:
        b = req.get_json()
        build_id, plan_id, approve, who = int(b["build_id"]), str(b["plan_id"]), bool(b["approve"]), str(b["who"])
    except (ValueError, KeyError, TypeError):
        return _json({"error": "build_id, plan_id, approve and who are required"}, 400)
    ok, text, plan = _decide(build_id, plan_id, approve, who, "dashboard", msg)
    return _json({"ok": ok, "message": text, "status": (plan or {}).get("status")}, 200 if ok else 409)


@app.route(route="fix/review", methods=["GET", "POST"], auth_level=func.AuthLevel.ANONYMOUS)
@app.queue_output(arg_name="msg", queue_name=FIX_QUEUE, connection="AzureWebJobsStorage")
def fix_review(req: func.HttpRequest, msg: func.Out[str]) -> func.HttpResponse:
    """The page behind an email's approval link. GET only shows the plan; the decision is a POST from its buttons."""
    import fixplan
    import fix_review as pages

    secret = os.getenv("FIX_LINK_SECRET", "")
    token = req.params.get("t") or (req.form.get("t") if req.method == "POST" else "") or ""
    link = fixplan.read_link(secret, token) if secret else None
    if not link:
        return _html(pages.message_page("Link not valid", "This approval link is damaged or has expired. "
                                        "Open the run in the PipeMedic dashboard to see its current fix plans.", False), 403)
    if req.method == "POST":
        action = req.form.get("action")
        if action not in ("approve", "reject"):
            return _html(pages.message_page("Nothing decided", "Press Approve or Reject on the plan page.", False), 400)
        ok, text, plan = _decide(link["build_id"], link["plan_id"], action == "approve", link["recipient"], "email", msg)
        return _html(pages.message_page(("Approved" if action == "approve" else "Rejected") if ok else "Not decided", text, ok),
                     200 if ok else 409)
    import config
    from azure_auth import get_credential
    from store import TableStore

    plan = TableStore(config.settings().state_table_url, get_credential()).get_plan(link["build_id"], link["plan_id"])
    if not plan:
        return _html(pages.message_page("Plan not found", "This fix plan no longer exists.", False), 404)
    return _html(pages.review_page(plan, token, link["recipient"], blocked=fixplan.is_runnable(plan)))


@app.queue_trigger(arg_name="msg", queue_name=FIX_QUEUE, connection="AzureWebJobsStorage")
def execute_fix(msg: func.QueueMessage) -> None:
    """Carries out one approved fix plan with the app's own identity, then reports the result."""
    import logging

    body = json.loads(msg.get_body().decode("utf-8"))
    try:
        from fix_executor import execute

        logging.info("PipeMedic fix: %s", execute(int(body["build_id"]), str(body["plan_id"])))
    except Exception:  # not retried: carry_out records its own failures, and a half-done change must not run twice
        logging.exception("PipeMedic fix failed")
