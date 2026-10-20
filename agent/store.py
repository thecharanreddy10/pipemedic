"""State for the always-on watcher, kept in Azure Table Storage.

Tables (created on first use):
  State        which failed runs were already handled, and a one-time "initialised" marker
  Remediation  the audit log of automatic actions (the cloud copy of remediation_log.jsonl)
  Diagnoses    every diagnosis the watcher produced, as JSON, for the dashboard and failure memory
"""
import json
from datetime import datetime, timedelta, timezone

import config

DEFAULT_STATE_URL = config.settings().state_table_url
MAX_JSON = 30000  # Table Storage allows 32K characters per string property


def _fit(obj: dict) -> str:
    text = json.dumps(obj, default=str)
    if len(text) > MAX_JSON:
        slim = {k: v for k, v in obj.items() if k not in ("log_excerpt", "ai")}
        text = json.dumps(slim, default=str)[:MAX_JSON]
    return text


class TableStore:
    def __init__(self, table_url: str, credential):
        from azure.data.tables import TableServiceClient

        self._svc = TableServiceClient(endpoint=table_url, credential=credential)
        self._tables = {}

    def _t(self, name: str):
        if name not in self._tables:
            self._tables[name] = self._svc.create_table_if_not_exists(name)
        return self._tables[name]

    # ---- which failures were handled ----
    def initialised(self) -> bool:
        try:
            self._t("State").get_entity("meta", "initialised")
            return True
        except Exception:
            return False

    def set_initialised(self) -> None:
        self._t("State").upsert_entity({"PartitionKey": "meta", "RowKey": "initialised",
                                        "time": datetime.now(timezone.utc).isoformat()})

    def seen_ids(self) -> set[int]:
        return {int(e["RowKey"]) for e in self._t("State").query_entities("PartitionKey eq 'seen'")}

    def claim(self, build_id: int, note: str = "processing") -> bool:
        """Take a run for investigation. True for exactly one caller: the hook and the timer can race, and only one may win."""
        from azure.core.exceptions import ResourceExistsError

        try:
            self._t("State").create_entity({"PartitionKey": "seen", "RowKey": str(build_id), "note": note[:500],
                                            "time": datetime.now(timezone.utc).isoformat()})
            return True
        except ResourceExistsError:
            return False

    def trace(self, build_id: int, text: str) -> None:
        """A timestamped line about what the agent is doing for a run. Best effort: it must never get in the way."""
        try:
            now = datetime.now(timezone.utc)
            self._t("Trace").upsert_entity({"PartitionKey": str(build_id), "RowKey": now.strftime("%Y%m%dT%H%M%S%f"),
                                            "time": now.isoformat(), "text": text[:900]})
        except Exception:
            pass

    # Stages before the agent has done anything outside its own storage. A run stopped here can safely be investigated again.
    EARLY_STAGES = ("processing", "processing: reading the logs", "processing: reading the runner logs",
                    "processing: asking the model", "processing: checking similar failures")

    def _retried(self, build_id: int) -> bool:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            self._t("State").get_entity("retried", str(build_id))
            return True
        except (ResourceNotFoundError, KeyError):
            return False

    def expire_stale(self, minutes: int = 15) -> dict:
        """Deal with runs that were claimed but never finished because the worker was stopped (a restart or a deployment).
        A run stopped before it did anything outside its own storage is released once, so the next check investigates it
        again. Any other stopped run is closed with an error, because repeating it could send a second email or open a
        second work item. A run is stale when its note has not changed for `minutes`."""
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
        out = {"released": [], "closed": []}
        for e in self._t("State").query_entities("PartitionKey eq 'seen'"):
            note = str(e.get("note", ""))
            if not note.startswith("processing") or str(e.get("time", "")) >= cutoff:
                continue
            build_id = int(e["RowKey"])
            if note in self.EARLY_STAGES and not self._retried(build_id):
                self._t("State").upsert_entity({"PartitionKey": "retried", "RowKey": str(build_id),
                                                "time": datetime.now(timezone.utc).isoformat()})
                self._t("State").delete_entity("seen", str(build_id))
                out["released"].append(build_id)
            else:
                self.mark_seen(build_id, "error: the investigation did not finish (the worker was stopped before it could). Check the run and its email.")
                out["closed"].append(build_id)
        return out

    def mark_seen(self, build_id: int, note: str = "") -> None:
        self._t("State").upsert_entity({"PartitionKey": "seen", "RowKey": str(build_id), "note": note[:500],
                                        "time": datetime.now(timezone.utc).isoformat()})

    # ---- remediation audit log ----
    def history(self) -> list[dict]:
        rows = [json.loads(e["json"]) for e in self._t("Remediation").query_entities("PartitionKey eq 'log'")]
        return sorted(rows, key=lambda r: r.get("time", ""))

    def append_history(self, entry: dict) -> None:
        entry = {"time": datetime.now(timezone.utc).isoformat(), **entry}
        self._t("Remediation").create_entity({"PartitionKey": "log", "RowKey": entry["time"].replace(":", "") + f"-{entry.get('build_id', 0)}-{entry.get('action', '')}",
                                              "json": _fit(entry)})

    # ---- diagnoses ----
    def save_diagnosis(self, d: dict) -> None:
        self._t("Diagnoses").upsert_entity({"PartitionKey": "run", "RowKey": f"{int(d['build_id']):012d}",
                                            "pipeline": d.get("pipeline", ""), "verdict": d.get("verdict", ""),
                                            "category": d.get("category", ""), "json": _fit(d)})

    def diagnoses(self, limit: int = 50) -> list[dict]:
        rows = [json.loads(e["json"]) for e in self._t("Diagnoses").query_entities("PartitionKey eq 'run'")]
        return sorted(rows, key=lambda r: -r["build_id"])[:limit]

    # ---- fixes opened by PipeMedic ----
    def fixes(self) -> list[dict]:
        return [json.loads(e["json"]) for e in self._t("Fixes").query_entities("PartitionKey eq 'fix'")]

    def record_fix(self, rec: dict) -> None:
        self._t("Fixes").upsert_entity({"PartitionKey": "fix", "RowKey": str(rec["build_id"]), "json": json.dumps(rec)})

    def update_fix(self, build_id: int, **changes) -> None:
        t = self._t("Fixes")
        e = t.get_entity("fix", str(build_id))
        rec = {**json.loads(e["json"]), **changes}
        t.upsert_entity({"PartitionKey": "fix", "RowKey": str(build_id), "json": json.dumps(rec)})

    # ---- failure memory ----
    def memory_entries(self) -> list[dict]:
        out = []
        for e in self._t("Memory").query_entities("PartitionKey eq 'failure'"):
            rec = json.loads(e["json"])
            rec["embedding"] = [float(x) for x in e["vec"].split(",")] if e.get("vec") else []
            out.append(rec)
        return out

    def save_memory(self, entry: dict) -> None:
        rec = {k: v for k, v in entry.items() if k != "embedding"}
        self._t("Memory").upsert_entity({"PartitionKey": "failure", "RowKey": str(entry["build_id"]), "json": json.dumps(rec),
                                         "vec": ",".join(f"{x:.5f}" for x in entry.get("embedding", []))})

    def update_memory(self, build_id: int, **changes) -> None:
        t = self._t("Memory")
        e = t.get_entity("failure", str(build_id))
        rec = {**json.loads(e["json"]), **changes}
        t.upsert_entity({"PartitionKey": "failure", "RowKey": str(build_id), "json": json.dumps(rec), "vec": e.get("vec", "")})

    # ---- open pool and runner alerts (so each problem is announced once) ----
    def pool_alert_state(self, scope: str | None = None) -> dict | None:
        """The problems currently open, or None if none were ever saved. Any other failure raises, so a table
        outage is never mistaken for "nothing was open" (which would announce every problem again)."""
        from azure.core.exceptions import ResourceNotFoundError

        try:
            return json.loads(self._t("State").get_entity("poolalerts", self._key("active", scope))["json"])
        except (ResourceNotFoundError, KeyError):
            return None

    def save_pool_alert_state(self, state: dict, scope: str | None = None) -> None:
        self._t("State").upsert_entity({"PartitionKey": "poolalerts", "RowKey": self._key("active", scope),
                                        "json": json.dumps(state, default=str)[:MAX_JSON],
                                        "time": datetime.now(timezone.utc).isoformat()})

    # ---- owner preferences (set from the dashboard, read by the watcher every cycle) ----
    # A setting can be kept for one project (scope "org/project") or for all projects. A project's own value wins.
    @staticmethod
    def _key(name: str, scope: str | None) -> str:
        # Table Storage row keys may not contain / \ # ? so the project's slash is replaced.
        return f"{scope.lower().replace('/', '~')}::{name}" if scope else name

    def get_setting(self, name: str, default: str = "", scope: str | None = None) -> str:
        keys = ([self._key(name, scope)] if scope else []) + [name]
        for key in keys:
            try:
                return str(self._t("State").get_entity("settings", key).get("value", default))
            except Exception:
                continue
        return default

    def setting_source(self, name: str, scope: str | None = None) -> str:
        """Where the value in force comes from: "project", "all" or "none"."""
        for key, label in ([(self._key(name, scope), "project")] if scope else []) + [(name, "all")]:
            try:
                self._t("State").get_entity("settings", key)
                return label
            except Exception:
                continue
        return "none"

    def set_setting(self, name: str, value: str, who: str = "", scope: str | None = None) -> None:
        self._t("State").upsert_entity({"PartitionKey": "settings", "RowKey": self._key(name, scope), "value": value, "who": who[:200],
                                        "time": datetime.now(timezone.utc).isoformat()})

    def clear_setting(self, name: str, scope: str | None = None) -> None:
        try:
            self._t("State").delete_entity("settings", self._key(name, scope))
        except Exception:
            pass
