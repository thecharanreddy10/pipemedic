"""Pool and runner alerts that are sent once, then closed when the problem clears.

collect() merges the pool findings (agents, queue) with the runner-health alerts (trends, silent runners) into one
set keyed by problem. update() compares that set with what was seen on earlier checks and says what is new and what
has cleared. Pure functions, no network, so they can be tested with fixtures.

A problem must be seen on RAISE_AFTER checks in a row before anyone is told, and must be gone for CLEAR_AFTER
checks in a row before it is called resolved, so a one-minute blip does not send two emails.
"""

RAISE_AFTER = 2
CLEAR_AFTER = 2
LEVELS = ("warning", "critical")


def collect(pool: dict, health_alerts: list[dict]) -> dict[str, dict]:
    """Warning and critical problems only, keyed by what they are about (informational findings never alert)."""
    out = {}
    for f in pool.get("findings", []):
        if f["level"] in LEVELS:
            key = f"pool:{f['code']}" + (f":{f['agent']}" if f.get("agent") else "")
            out[key] = {"level": f["level"], "message": f["message"], "suggestion": f["suggestion"]}
    for a in health_alerts:
        if a["level"] in LEVELS:
            out[f"health:{a['metric']}:{a['host']}"] = {"level": a["level"], "message": a["message"], "suggestion": a["suggestion"]}
    return out


def update(state: dict, current: dict, now: str) -> tuple[dict, list[dict], list[dict]]:
    """Return (new state, problems to announce, problems that cleared). State maps key -> record."""
    new, raised, resolved = {}, [], []
    for key, f in current.items():
        prev = state.get(key)
        rec = {**f, "since": now, "seen": 1, "missing": 0, "sent": False, "sent_level": None} if prev is None else \
            {**prev, **f, "seen": prev["seen"] + 1, "missing": 0}
        escalated = rec["sent"] and f["level"] == "critical" and rec["sent_level"] != "critical"
        if rec["seen"] >= RAISE_AFTER and (not rec["sent"] or escalated):
            raised.append({"key": key, "level": f["level"], "message": f["message"], "suggestion": f["suggestion"],
                           "since": rec["since"], "escalated": escalated})
            rec["sent"], rec["sent_level"] = True, f["level"]
        new[key] = rec
    for key, prev in state.items():
        if key in current:
            continue
        if not prev["sent"]:
            continue  # it was never announced, so there is nothing to close
        rec = {**prev, "missing": prev["missing"] + 1}
        if rec["missing"] >= CLEAR_AFTER:
            resolved.append({"key": key, "message": prev["message"], "since": prev["since"]})
        else:
            new[key] = rec
    return new, raised, resolved
