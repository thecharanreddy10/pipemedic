"""Failure memory: "this looks like run 6, which was fixed by ...".

Each diagnosed failure is turned into a short text signature and embedded with a model in Azure AI Foundry. A new
failure is compared with past ones by cosine similarity. When the same pipeline later passes, the old failure is
marked resolved, and if PipeMedic opened a pull request for it the resolution says so.
"""
import math
import re
from datetime import datetime

SIMILARITY_THRESHOLD = 0.80
_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z\s*")


def signature(d: dict) -> str:
    """The text that represents a failure: what failed, how, and the key log lines (no timestamps)."""
    evidence = [_TS.sub("", e).strip() for e in d.get("evidence", [])[:3]]
    return " | ".join([d.get("category", ""), d.get("failed_step", ""), d.get("root_cause", "")[:300]] + evidence)


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def find_similar(vec: list[float], entries: list[dict], exclude_build: int | None = None,
                 threshold: float = SIMILARITY_THRESHOLD, top: int = 3) -> list[dict]:
    scored = [{**e, "similarity": cosine(vec, e["embedding"])} for e in entries
              if e["build_id"] != exclude_build and e.get("embedding")]
    return sorted((s for s in scored if s["similarity"] >= threshold), key=lambda s: -s["similarity"])[:top]


def describe(match: dict) -> str:
    """One sentence a person can act on."""
    pct = round(match["similarity"] * 100)
    what = match.get("evidence") or match["root_cause"][:160]
    text = f"run {match['build_id']} ({match['pipeline']}) looked similar ({pct}%): {what}."
    if match.get("status") == "resolved":
        text += f" It was resolved: {match['resolution']}."
    elif match.get("status") == "fix-ready":
        text += f" A fix is ready: {match['resolution']}."
    else:
        text += " It has not been resolved yet."
    return text


def recall(llm, store, d: dict) -> tuple[dict | None, list[float]]:
    """Return (best match summary or None, this failure's embedding)."""
    vec = llm.embed([signature(d)])[0]
    matches = find_similar(vec, store.memory_entries(), exclude_build=d["build_id"])
    if not matches:
        return None, vec
    best = matches[0]
    return {"build_id": best["build_id"], "pipeline": best["pipeline"], "similarity": round(best["similarity"], 3),
            "status": best.get("status", "open"), "resolution": best.get("resolution", ""), "summary": describe(best)}, vec


def remember(store, d: dict, vec: list[float]) -> None:
    first = next((_TS.sub("", e).strip() for e in d.get("evidence", []) if e.strip()), "")
    store.save_memory({"build_id": d["build_id"], "pipeline": d["pipeline"], "category": d.get("category", ""),
                       "verdict": d.get("verdict", ""), "root_cause": d.get("root_cause", ""), "evidence": first[:200],
                       "finish": (d.get("finish") or ""), "source_branch": d.get("source_branch") or "refs/heads/main",
                       "status": "open", "resolution": "", "embedding": vec})


def _t(v: str | None):
    return datetime.fromisoformat(v.replace("Z", "+00:00")) if v else None


def resolve(builds: list[dict], store) -> list[str]:
    """Update open failures. A failure is resolved when the same pipeline passes later on the same branch; an AI fix
    that passes on its own branch but is not merged yet shows as "fix ready". Returns notes about what changed."""
    notes = []
    fixes = {f["build_id"]: f for f in store.fixes()}
    for e in store.memory_entries():
        if e.get("status") not in ("open", "fix-ready"):
            continue
        failed_at = _t(e.get("finish"))
        branch = e.get("source_branch") or "refs/heads/main"
        later = [b for b in builds if b["definition"]["name"] == e["pipeline"] and b.get("result") == "succeeded"
                 and b.get("sourceBranch") == branch and failed_at and _t(b.get("finishTime")) and _t(b["finishTime"]) > failed_at]
        fx = fixes.get(e["build_id"])
        if later:
            first = min(later, key=lambda b: _t(b["finishTime"]))
            minutes = round((_t(first["finishTime"]) - failed_at).total_seconds() / 60)
            how = f"the pipeline passed again in run {first['id']}, {minutes} minutes later"
            if fx and fx.get("status") == "completed":
                how = f"fixed by pull request {fx['pr_id']} ({fx.get('title', 'AI fix')}); " + how
            store.update_memory(e["build_id"], status="resolved", resolution=how)
            notes.append(f"run {e['build_id']}: {how}")
        elif fx and fx.get("verified") == "passed" and fx.get("status") == "open" and e.get("status") != "fix-ready":
            how = f"a PipeMedic pull request ({fx['pr_id']}) passes the pipeline and is waiting for review"
            store.update_memory(e["build_id"], status="fix-ready", resolution=how)
            notes.append(f"run {e['build_id']}: {how}")
    return notes


def seed(ado, store, llm, hosts: dict, builds: list[dict], health_api: str, collect, fetch_health, diagnose_fn, assess_flaky) -> int:
    """Diagnose every failed run not yet in memory (rules only) and remember it. Returns how many were added."""
    known = {e["build_id"] for e in store.memory_entries()}
    added = 0
    for b in sorted((b for b in builds if b.get("result") in ("failed", "canceled") and b["id"] not in known), key=lambda b: b["id"]):
        f = collect(ado, b, hosts)
        f["flaky"] = assess_flaky(b, builds)
        try:
            health = fetch_health(health_api, f["hostname"], f["start"])
        except Exception:
            health = []
        d = diagnose_fn(f, health)
        remember(store, d, llm.embed([signature(d)])[0])
        added += 1
    resolve(builds, store)
    return added
