import os
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import watcher  # noqa: E402
from test_event_trigger import Ado, Store, b, collect, healthy, make  # noqa: E402


class RecordingStore(Store):
    """Remembers every note written for a run, in order."""

    def __init__(self):
        super().__init__()
        self.notes = []

    def mark_seen(self, i, note=""):
        self.notes.append(note)
        super().mark_seen(i, note)


def test_a_call_that_answers_returns_and_one_that_fails_raises_its_own_error():
    assert watcher.run_with_timeout(lambda: 42, 1) == 42
    with pytest.raises(ValueError):
        watcher.run_with_timeout(lambda: (_ for _ in ()).throw(ValueError("boom")), 1)


def test_a_call_that_hangs_is_given_up_on_after_the_time_limit():
    start = time.time()
    with pytest.raises(TimeoutError):
        watcher.run_with_timeout(lambda: time.sleep(5), 0.2)
    assert time.time() - start < 2


def test_an_email_that_never_answers_does_not_stop_the_run_from_being_marked_done(monkeypatch):
    monkeypatch.setattr(watcher, "EMAIL_TIMEOUT_S", 0.2)
    w, store, sent, _ = make([b(5)])
    w.mailer = lambda *a: time.sleep(5)
    out = w.handle_event(5)
    assert out["run"] == 5 and "email failed: no answer after" in out["email"]
    assert store.seen[5].startswith("done")  # not left as "processing" for ever


def test_a_runner_log_read_that_hangs_leaves_the_diagnosis_without_runner_logs(monkeypatch):
    monkeypatch.setattr(watcher, "RUNNER_LOGS_TIMEOUT_S", 0.2)
    w, store, sent, _ = make([b(5)])
    w.runner_log_fetch = lambda *a: time.sleep(5)
    assert w.handle_event(5)["run"] == 5
    assert store.diags[5]["runner_logs"] is None and store.seen[5].startswith("done")


def test_each_stage_is_noted_so_a_stall_shows_where():
    w, store, sent, _ = make([b(5)])
    store.__class__ = RecordingStore
    store.notes = []
    w.handle_event(5)
    stages = [n for n in store.notes if n.startswith("processing:")]
    for expected in ("reading the logs", "reading the runner logs", "acting", "saving", "sending the email"):
        assert f"processing: {expected}" in stages, stages
    assert stages.index("processing: reading the logs") < stages.index("processing: saving") < stages.index("processing: sending the email")
    assert store.notes[-1].startswith("done")


def test_an_investigation_that_stalls_writes_where_it_is_stuck_into_the_runs_note(monkeypatch):
    monkeypatch.setattr(watcher, "STALL_AFTER_S", 0.2)
    w, store, sent, _ = make([b(5)])

    def stuck_collect(ado, build, hosts):
        time.sleep(1.0)  # the stall
        return collect(ado, build, hosts)

    w.collect = stuck_collect
    notes = []
    real = store.mark_seen
    store.mark_seen = lambda i, note="": (notes.append(note), real(i, note))[1]
    w.handle_event(5)
    stalled = [n for n in notes if "STALLED" in n]
    assert stalled and "stuck_collect" in stalled[0] and "worker:" in stalled[0]
    assert store.seen[5].startswith("done")  # and the run still finishes and replaces the note


def test_a_quick_investigation_leaves_no_stall_note(monkeypatch):
    monkeypatch.setattr(watcher, "STALL_AFTER_S", 0.3)
    w, store, sent, _ = make([b(5)])
    notes = []
    real = store.mark_seen
    store.mark_seen = lambda i, note="": (notes.append(note), real(i, note))[1]
    w.handle_event(5)
    time.sleep(0.5)
    assert not [n for n in notes if "STALLED" in n]


# ---------------- closing runs whose worker was stopped ----------------
class FakeTable:
    def __init__(self, rows):
        self.rows = {(r["PartitionKey"], r["RowKey"]): dict(r) for r in rows}

    def query_entities(self, flt):
        return [dict(r) for (pk, _), r in self.rows.items() if pk == "seen"]

    def upsert_entity(self, e):
        self.rows[(e["PartitionKey"], e["RowKey"])] = dict(e)

    def get_entity(self, pk, rk):
        return self.rows[(pk, rk)]

    def delete_entity(self, pk, rk):
        del self.rows[(pk, rk)]


def table_store(rows):
    from store import TableStore
    s = TableStore.__new__(TableStore)
    t = FakeTable(rows)
    s._t = lambda name: t
    return s, t


def row(build, note, minutes_ago):
    when = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    return {"PartitionKey": "seen", "RowKey": str(build), "note": note, "time": when}


def test_only_runs_stuck_in_processing_for_a_long_time_are_dealt_with():
    s, t = table_store([row(1, "processing: sending the email", 30), row(2, "processing: saving", 2),
                        row(3, "done; email Succeeded", 90), row(4, "baseline", 90)])
    assert s.expire_stale(15) == {"released": [], "closed": [1]}
    assert t.rows[("seen", "1")]["note"].startswith("error: the investigation did not finish")  # it had already acted: not repeated
    assert t.rows[("seen", "2")]["note"] == "processing: saving" and t.rows[("seen", "3")]["note"].startswith("done")


def test_a_run_stopped_before_it_did_anything_is_released_once_so_it_is_investigated_again():
    s, t = table_store([row(7, "processing: reading the runner logs", 30), row(8, "processing", 30)])
    assert s.expire_stale(15) == {"released": [7, 8], "closed": []}
    assert ("seen", "7") not in t.rows and ("retried", "7") in t.rows
    t.rows[("seen", "7")] = row(7, "processing: asking the model", 30)  # it was stopped again after the retry
    assert s.expire_stale(15) == {"released": [], "closed": [7]}
    assert t.rows[("seen", "7")]["note"].startswith("error: the investigation did not finish")


def test_the_check_for_stuck_runs_happens_every_ten_minutes_and_a_failure_in_it_is_harmless():
    w, store, sent, _ = make([b(1)])
    calls = []
    store.expire_stale = lambda minutes: calls.append(minutes) or {"released": [], "closed": []}
    w._now = lambda: datetime(2026, 10, 20, 12, 7, tzinfo=timezone.utc)
    w.run_once()
    assert calls == []
    w._now = lambda: datetime(2026, 10, 20, 12, 10, tzinfo=timezone.utc)
    w.run_once()
    assert calls == [watcher.STALE_AFTER_MIN]
    store.expire_stale = lambda minutes: (_ for _ in ()).throw(RuntimeError("table down"))
    w._now = lambda: datetime(2026, 10, 20, 12, 20, tzinfo=timezone.utc)
    w.run_once()  # no exception


# ---------------- hook only ----------------
def test_with_the_timer_off_failures_are_investigated_only_when_the_hook_reports_them():
    w, store, sent, _ = make([b(5)], timer_failures=False)
    assert w.run_once()["timer"] == "off: hook only" and sent == [] and store.seen == {}
    assert w.handle_event(5)["run"] == 5 and len(sent) == 1
