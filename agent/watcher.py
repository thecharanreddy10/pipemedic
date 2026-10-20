"""The always-on watcher: runs once a minute inside the Function App.

For each new failed run it investigates (rules, optional AI), plans and, if allowed, performs guardrailed actions,
emails the person responsible, and records the result. It never repeats work: every failed run is marked as handled
before any action is taken, so a crash cannot cause repeated emails or repeated retries.

Settings (Function App settings):
  ADO_ORG, ADO_PROJECT, ADO_POOL     what to watch
  HEALTH_API                         base URL of the health API
  STATE_TABLE_URL                    Table Storage endpoint for state
  AI_IN_WATCHER=1                    add an AI second opinion to each diagnosis
  NOTIFY_ENABLED=1, NOTIFY_ENDPOINT, NOTIFY_FROM, NOTIFY_DEFAULT_TO   email settings
  NOTIFY_OVERRIDE_TO                 send every email to this address instead of the person who triggered the run
  ACT_ENABLED=1                      let the allowlisted actions run; anything else is a dry run
  FIX_MODE=off|propose|open          AI fixes: off, only describe them in the email, or open real pull requests
  MEMORY=1                           remember failures and say "this looks like run N, resolved by ..."
"""
import re
import logging
import os
import sys
import threading
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import config
import memory
import pool_alerts
import remediate
from ai_diagnose import ai_diagnose
from ai_fix import check_fix_verifications, open_fix_pr
from diagnose import apply_ai, diagnose, summarize_health
from flaky import assess as assess_flaky
from notify import recipient_for, render_email, render_pool_email, run_url
from pipemedic_agent import collect_failure, fetch_health
from pool_status import analyze_pool
from predict import analyze_health

log = logging.getLogger("pipemedic.watcher")
MAX_PER_RUN = 5  # keeps one invocation well inside the function timeout
MAX_PER_RUN_WITH_RUNNER_LOGS = 2
EMAIL_TIMEOUT_S = 45
RUNNER_LOGS_TIMEOUT_S = 75
AI_TIMEOUT_S = 150
STALE_AFTER_MIN = 15  # a claimed run that has not moved for this long was stopped before it could finish
STALL_AFTER_S = 90    # an investigation still running after this long gets a note saying where it is
HEARTBEAT_S = 10      # how often a running investigation records that its worker is alive
WATCHED_FILES = {"watcher.py", "runner_logs.py", "remediate.py", "store.py", "notify.py", "ai_diagnose.py", "ai_fix.py", "memory.py",
                 "llm.py", "pipemedic_agent.py", "diagnose.py"}


def run_with_timeout(fn, seconds: float):
    """Call fn, but stop waiting after `seconds`. A call that stalls (a network hang) must not hold the whole investigation
    until the function is killed, because the run would then never be marked done. The stuck call is left on its own thread."""
    box: dict = {}

    def target():
        try:
            box["value"] = fn()
        except BaseException as e:  # noqa: BLE001 - handed back to the caller
            box["error"] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        raise TimeoutError(f"no answer after {seconds:.0f}s")
    if "error" in box:
        raise box["error"]
    return box["value"]


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass
class Config:
    org: str = field(default_factory=lambda: config.settings().org)
    project: str = field(default_factory=lambda: config.settings().project)
    pool: str = field(default_factory=lambda: config.settings().pool)
    health_api: str = ""
    state_url: str = ""
    ai_on: bool = False
    notify_on: bool = False
    notify_endpoint: str = ""
    notify_from: str = ""
    notify_default_to: str = ""
    notify_override_to: str = ""
    act: bool = False
    fix_mode: str = "off"
    memory_on: bool = False
    pool_alerts: bool = False
    runner_logs: bool = False
    timer_failures: bool = True  # False = failures are investigated only when the hook reports them
    top: int = 30

    @classmethod
    def from_env(cls) -> "Config":
        flag = lambda n: os.getenv(n, "") in ("1", "true", "yes")  # noqa: E731
        base = config.settings()
        return cls(org=base.org, project=base.project, pool=base.pool, health_api=base.health_api,
                   state_url=os.getenv("STATE_TABLE_URL", ""), ai_on=flag("AI_IN_WATCHER"),
                   notify_on=flag("NOTIFY_ENABLED"), notify_endpoint=os.getenv("NOTIFY_ENDPOINT", ""),
                   notify_from=os.getenv("NOTIFY_FROM", ""), notify_default_to=os.getenv("NOTIFY_DEFAULT_TO", ""), notify_override_to=os.getenv("NOTIFY_OVERRIDE_TO", ""),
                   act=flag("ACT_ENABLED"), fix_mode=os.getenv("FIX_MODE", "off").lower(), memory_on=flag("MEMORY"),
                   pool_alerts=os.getenv("POOL_ALERTS", "1") in ("1", "true", "yes"),
                   runner_logs=os.getenv("RUNNER_LOGS", "1") in ("1", "true", "yes"),
                   timer_failures=os.getenv("WATCH_TIMER", "1") in ("1", "true", "yes"))


class Watcher:
    def __init__(self, cfg: Config, ado, store, health_fetch=fetch_health, llm=None, mailer=None,
                 collect=collect_failure, now=None, runner_log_fetch=None):
        self.cfg, self.ado, self.store = cfg, ado, store
        self._env_override = cfg.notify_override_to
        self.health_fetch, self.llm, self.mailer, self.collect = health_fetch, llm, mailer, collect
        self.runner_log_fetch = runner_log_fetch  # (hostname, start, finish) -> runner_logs.collect() result
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.hosts: dict = {}
        self.recent: list[dict] = []
        self._stage_now: dict = {}

    FIX_MODES = ("off", "propose", "open")
    _env_override = ""

    @property
    def _scope(self) -> str:
        return f"{self.cfg.org}/{self.cfg.project}"

    def _apply_owner_preferences(self) -> None:
        """The owner's choice in the dashboard (stored) overrides the app setting. A missing or invalid value changes nothing."""
        try:
            mode = (self.store.get_setting("fix_mode", "", scope=self._scope) or "").lower()
        except Exception:
            log.exception("could not read owner preferences")
            return
        if mode in self.FIX_MODES:
            self.cfg.fix_mode = mode
        try:  # which automatic actions may run for real: the owner's list, else all of them when ACT_ENABLED, else none
            stored = self.store.get_setting("actions", None, scope=self._scope)
        except Exception:
            stored = None
        if isinstance(stored, str):
            self.allowed_actions = {a.strip() for a in stored.split(",") if a.strip() in remediate.AUTO_ACTIONS}
        else:
            self.allowed_actions = set(remediate.AUTO_ACTIONS) if self.cfg.act else set()
        try:
            to = (self.store.get_setting("notify_to", None, scope=self._scope) or "").strip()
        except Exception:
            return
        # A stored address sends every alert there. An empty stored value means "back to the app setting".
        if to and EMAIL_RE.match(to):
            self.cfg.notify_override_to = to
        elif self.store.get_setting("notify_to", None, scope=self._scope) == "":
            self.cfg.notify_override_to = self._env_override

    # ------------------------------------------------------------------
    def run_once(self) -> dict:
        self._apply_owner_preferences()
        pool = None
        if self.cfg.pool_alerts:
            try:  # checked first: it is quick, and a slow failure investigation must not delay it
                pool = self._check_pool()
            except Exception:
                log.exception("pool check failed")
        out = self._run_failures() if self.cfg.timer_failures else {"baseline": 0, "handled": 0, "timer": "off: hook only"}
        if pool is not None:
            out["pool"] = pool
        return out

    def _check_pool(self) -> dict:
        """Look at the agent pool and runner health, and email each new problem once and each cleared problem once."""
        cfg = self.cfg
        now = self._now()
        previous = self.store.pool_alert_state(self._scope)
        pool = analyze_pool(self.ado.pool_agents(cfg.pool), self.ado.pool_requests(cfg.pool), now=now)
        start = (now - timedelta(minutes=60)).isoformat()
        records = []
        for host in sorted({h for h in self.ado.agent_hosts(cfg.pool).values() if h}):
            try:
                records += self.health_fetch(cfg.health_api, host, start)
            except Exception:  # one runner's missing history must not hide the pool's own findings
                log.exception("could not read health for %s", host)
        current = pool_alerts.collect(pool, analyze_health(records, now=now))
        state, raised, resolved = pool_alerts.update(previous or {}, current, now.isoformat())
        self.store.save_pool_alert_state(state, self._scope)  # before the email, so a failure cannot repeat it every minute

        status = "nothing to send"
        to = cfg.notify_override_to or cfg.notify_default_to
        if (raised or resolved) and cfg.notify_on and self.mailer is not None and to:
            subject, text, body = render_pool_email(raised, resolved, cfg.pool)
            try:
                status = f"{self.mailer(to, subject, text, body)} to {to}"
            except Exception as e:
                status = f"email failed: {str(e)[:150]}"
        elif raised or resolved:
            status = "not sent"
        return {"open": len(state), "raised": len(raised), "resolved": len(resolved), "email": status}

    def _run_failures(self) -> dict:
        builds = self.ado.recent_builds(self.cfg.top)
        failed = [b for b in builds if b.get("result") in ("failed", "canceled")]

        if not self.store.initialised():
            # First start: remember what already failed, but do not email about old history.
            for b in failed:
                self.store.mark_seen(b["id"], "baseline")
            self.store.set_initialised()
            return {"baseline": len(failed), "handled": 0}

        if self.cfg.memory_on:
            try:  # close failures whose pipeline has since passed
                memory.resolve(builds, self.store)
            except Exception:
                log.exception("memory update failed")
        if self.cfg.fix_mode == "open":
            try:  # post verification results on pull requests this watcher opened
                check_fix_verifications(self.ado, self.store)
            except Exception:
                log.exception("fix verification check failed")

        expire = getattr(self.store, "expire_stale", None)
        if expire is not None and self._now().minute % 10 == 0:  # now and then: close runs whose worker was stopped mid-way
            try:
                stale = expire(STALE_AFTER_MIN)
                if stale["released"] or stale["closed"]:
                    log.warning("runs whose worker was stopped: investigated again %s, closed with an error %s", stale["released"], stale["closed"])
            except Exception:
                log.exception("could not close stale runs")
        seen = self.store.seen_ids()
        # Reading a runner's logs takes up to a minute, so fewer runs per invocation keep it inside the function timeout.
        limit = MAX_PER_RUN_WITH_RUNNER_LOGS if self.cfg.runner_logs else MAX_PER_RUN
        new = sorted((b for b in failed if b["id"] not in seen), key=lambda b: b["id"])[:limit]
        if not new:
            return {"baseline": 0, "handled": 0}

        self.recent = builds
        self.hosts = self.ado.agent_hosts(self.cfg.pool)
        handled = [r for r in (self._handle(b) for b in new) if r is not None]
        return {"baseline": 0, "handled": len(handled), "runs": handled}

    def _handle(self, build: dict) -> dict | None:
        """Investigate one failed run, unless the hook or an earlier check already took it. A crash never repeats work."""
        if not self.store.claim(build["id"]):  # marked before any side effect, and only one caller can win
            return None
        finished = threading.Event()
        self._watch_for_stall(build["id"], finished, threading.get_ident())
        self._heartbeat(build["id"], finished)
        try:
            return self.process(build)
        except Exception as e:  # one bad run must not stop the others
            log.exception("failed to process run %s", build["id"])
            self.store.mark_seen(build["id"], f"error: {e}"[:400])
            return None
        finally:
            finished.set()

    def _trace(self, build_id: int, text: str) -> None:
        trace = getattr(self.store, "trace", None)
        if trace is not None:
            trace(build_id, f"[pid {os.getpid()} inst {(os.getenv('WEBSITE_INSTANCE_ID') or '-')[:8]}] {text}")

    def _heartbeat(self, build_id: int, finished: threading.Event, every_s: float | None = None) -> None:
        """While a run is being investigated, record that the worker is alive. If the records stop, the worker did."""
        wait = HEARTBEAT_S if every_s is None else every_s
        if getattr(self.store, "trace", None) is None:
            return

        def beat():
            while not finished.wait(wait):
                self._trace(build_id, f"alive, stage: {self._stage_now.get(build_id, '?')}")

        threading.Thread(target=beat, daemon=True).start()

    def _watch_for_stall(self, build_id: int, finished: threading.Event, worker: int, after_s: float | None = None) -> None:
        """If an investigation is still going after a while, write where it is stuck into the run's note. With no log
        access in the cloud, this is how a stall can be seen, and a later stage note simply replaces it."""
        wait = STALL_AFTER_S if after_s is None else after_s

        def watch():
            if finished.wait(wait):
                return
            try:
                parts = []
                for ident, frame in sys._current_frames().items():
                    stack = traceback.extract_stack(frame)
                    ours = [f for f in stack if os.path.basename(f.filename) in WATCHED_FILES]
                    if ident == worker or ours:
                        shown = stack[-5:] if ident == worker else ours[-3:]
                        parts.append(("worker" if ident == worker else "thread") + ": " + " > ".join(
                            f"{os.path.basename(f.filename)}:{f.lineno} {f.name}" for f in shown))
                self.store.mark_seen(build_id, ("processing: STALLED " + " || ".join(parts))[:480])
            except Exception:
                log.exception("could not record where run %s is stuck", build_id)

        threading.Thread(target=watch, daemon=True).start()

    def handle_event(self, build_id: int) -> dict:
        """A "build completed" hook arrived: investigate that run now instead of waiting for the next timer check."""
        self._trace(build_id, "hook event received")
        try:
            self._apply_owner_preferences()
            build = self.ado.build(build_id)
            if build.get("status") != "completed" or build.get("result") not in ("failed", "canceled"):
                self._trace(build_id, "hook event skipped: the run did not fail")
                return {"run": build_id, "skipped": "the run did not fail"}
            self.recent = self.ado.recent_builds(self.cfg.top)
            self.hosts = self.ado.agent_hosts(self.cfg.pool)
            out = self._handle(build) or {"run": build_id, "skipped": "already handled"}
            self._trace(build_id, f"hook event handled: {str(out)[:300]}")
            return out
        except BaseException as e:  # the trace is the only record of a worker that dies here
            self._trace(build_id, f"hook event crashed: {e.__class__.__name__}: {str(e)[:300]}")
            raise

    # ------------------------------------------------------------------
    def _stage(self, build_id: int, text: str) -> None:
        """Note how far an investigation has got, so one that stalls shows where."""
        self._stage_now[build_id] = text
        try:
            self.store.mark_seen(build_id, f"processing: {text}")
        except Exception:
            log.exception("could not record the stage of run %s", build_id)

    def process(self, build: dict) -> dict:
        cfg = self.cfg
        bid = build["id"]
        self._stage(bid, "reading the logs")
        failure = self.collect(self.ado, build, self.hosts)
        failure["flaky"] = assess_flaky(build, self.recent)
        try:
            health = self.health_fetch(cfg.health_api, failure["hostname"], failure["start"])
        except Exception:
            health = []
        logs = None
        if cfg.runner_logs and self.runner_log_fetch is not None and failure.get("hostname"):
            self._stage(bid, "reading the runner logs")
            try:  # the runner's own logs around the failure; without them the diagnosis simply has less to go on
                logs = run_with_timeout(lambda: self.runner_log_fetch(failure["hostname"], failure["start"], failure["finish"]),
                                        RUNNER_LOGS_TIMEOUT_S)
            except Exception:
                log.exception("could not read the runner logs for run %s", bid)
        d = diagnose(failure, health, logs)

        if cfg.ai_on and self.llm is not None:
            self._stage(bid, "asking the model")
            try:
                d["ai"] = run_with_timeout(lambda d=d: ai_diagnose(self.llm, d), AI_TIMEOUT_S)
            except Exception as e:  # the rules' verdict stands without it
                d["ai"] = {"error": str(e)[:200]}
            d = apply_ai(d, d.get("ai"))

        vec = None
        if cfg.memory_on and self.llm is not None:
            self._stage(bid, "checking similar failures")
            try:
                d["similar"], vec = run_with_timeout(lambda d=d: memory.recall(self.llm, self.store, d), AI_TIMEOUT_S)
            except Exception:
                d["similar"] = None  # memory is a bonus; the diagnosis stands without it
        self._stage(bid, "acting")
        notes = self._remediate(d)
        d["actions"] = notes
        link = run_url(cfg.org, cfg.project, d["build_id"])
        if cfg.fix_mode in ("propose", "open") and self.llm is not None:
            self._stage(bid, "drafting a fix")
            d["fix"] = self._fix(d, link)
        self._stage(bid, "saving")
        self.store.save_diagnosis(d)
        if vec is not None:
            try:
                memory.remember(self.store, d, vec)
            except Exception:
                log.exception("could not remember run %s", d["build_id"])

        status = "not sent"
        if cfg.notify_on and self.mailer is not None and cfg.notify_default_to:
            to = cfg.notify_override_to or recipient_for(d, cfg.notify_default_to)
            subject, text, body = render_email(d, link, notes)
            self._stage(bid, "sending the email")
            try:
                status = f"{run_with_timeout(lambda: self.mailer(to, subject, text, body), EMAIL_TIMEOUT_S)} to {to}"
            except Exception as e:
                status = f"email failed: {str(e)[:150] or e.__class__.__name__}"
        self.store.mark_seen(d["build_id"], f"done; email {status}"[:480])
        return {"run": d["build_id"], "verdict": d["verdict"], "email": status}

    def _fix(self, d: dict, link: str) -> dict:
        """Ask for an AI fix. Never raises: a failed attempt just means no fix is offered."""
        try:
            r = run_with_timeout(lambda: open_fix_pr(self.ado, self.llm, d, link, store=self.store, dry_run=self.cfg.fix_mode != "open",
                                                     now=self._now()), AI_TIMEOUT_S)
        except Exception as e:
            return {"status": "error", "detail": str(e)[:200]}
        p = r.get("proposal") or {}
        return {"status": r["status"], "detail": r.get("detail", ""), "pr_id": r.get("pr_id"), "verify_build": r.get("verify_build"),
                "summary": p.get("summary", ""), "confidence": p.get("confidence", ""),
                "files": [f"{c['action']} {c['path']}" for c in p.get("changes", [])],
                "pr_url": f"https://dev.azure.com/{self.cfg.org}/{self.cfg.project}/_git/{self.cfg.project}/pullrequest/{r['pr_id']}" if r.get("pr_id") else ""}

    def _remediate(self, d: dict) -> list[str]:
        cfg = self.cfg
        start = (self._now() - timedelta(minutes=3)).isoformat()
        try:
            current = summarize_health(self.health_fetch(cfg.health_api, d["hostname"], start), start, self._now().isoformat())
        except Exception:
            current = {"samples": 0, "flags": ["no-health-data"]}
        notes = []
        for a in remediate.plan_actions(d, current, remediate.load_history(), now=self._now()):
            if a["auto"]:
                allowed = getattr(self, "allowed_actions", set(remediate.AUTO_ACTIONS) if cfg.act else set())
                real = a["action"] in allowed
                entry = remediate.execute(a, d, self.ado, cfg.pool, dry_run=not real)
                if real:
                    remediate.log_entry(entry)
                notes.append(f"{a['action']}: {entry['status']} - {entry['detail'][:120]}")
            else:
                notes.append(f"{a['action']} needs a person: {a['reason'][:120]}")
        return notes


# ----------------------------------------------------------------------
def build_watcher() -> Watcher:
    """The real dependencies, built from the app settings. Used by the timer trigger and by the build-completed hook."""
    import runner_logs
    from azure_auth import get_credential
    from llm import FoundryLLM
    from pipemedic_agent import AzureDevOps
    from store import TableStore

    cfg = Config.from_env()
    cred = get_credential()
    store = TableStore(cfg.state_url, cred)
    remediate.configure_store(store)
    mailer = None
    if cfg.notify_on:
        from notify import send_email
        mailer = lambda to, subject, text, body: send_email(  # noqa: E731
            cfg.notify_endpoint, cfg.notify_from, to, subject, text, body, cred)
    return Watcher(cfg, AzureDevOps(cfg.org, cfg.project, credential=cred), store,
                   llm=FoundryLLM() if cfg.ai_on else None, mailer=mailer,
                   runner_log_fetch=lambda host, start, finish: runner_logs.collect(cred, host, start, finish))


def run_once() -> dict:
    """Entry point for the timer trigger: the safety net that catches any run the hook missed."""
    return build_watcher().run_once()


def handle_event(build_id: int) -> dict:
    """Entry point for the build-completed hook: investigate that run now."""
    return build_watcher().handle_event(build_id)
