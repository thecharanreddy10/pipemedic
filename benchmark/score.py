"""Score the PipeMedic classifier against labelled real pipeline failures.

Reads collected failures (one JSON per run) and the ground-truth file, runs the production classifier on each failure,
and reports accuracy. The classifier never sees the answers; this script is the only place they are joined.

Usage:
    python score.py --logs data/logs --answers data/answers.json [--split dev|holdout|all] [--misses] [--json out.json]
"""
import argparse
import collections
import json
import os
import sys

AGENT = os.environ.get("PIPEMEDIC_AGENT", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent"))
sys.path.insert(0, AGENT)

import diagnose  # noqa: E402  (the production classifier)

# What each truth label corresponds to in the classifier's category list. None means the classifier has no category
# for that kind of cause, so a correct answer is impossible; the interesting question is whether it admits "unknown"
# or confidently files it under something wrong.
TRUTH_TO_CATEGORY = {
    "test-failure": {"test-failure"},
    "dependency": {"dependency"},
    "network": {"network"},
    "permissions": {"permissions"},
    "timeout": {"timeout"},
    "config": {"script-failure", "configuration"},
    "other": {"unknown", "script-failure", "configuration"},
    "lint": None,
    "type-error": None,
    "code-error": None,
}

# After the taxonomy is extended, these names are scored directly (truth label == predicted category).
EXTENDED = {"lint": "static-analysis", "type-error": "static-analysis", "code-error": "code-error"}


def load(logs_dir):
    out = []
    for name in sorted(os.listdir(logs_dir)):
        if not name.startswith("run_") or not name.endswith(".json"):
            continue
        out.append(json.load(open(os.path.join(logs_dir, name), encoding="utf-8")))
    return out


def predict(rec):
    failure = {
        "pipeline": rec["pipeline"], "pipeline_id": rec.get("pipeline_id"), "build_id": rec["build_id"],
        "source_branch": rec.get("source_branch"), "failed_step": rec["failed_step"], "issues": rec.get("issues", []),
        "log_text": rec.get("log_text", ""), "agent_name": None, "hostname": None,
        "start": rec.get("start"), "finish": rec.get("finish"),
    }
    d = diagnose.diagnose(failure, [])
    return d["category"], d


def ai_category(rec, d, llm, cache, cache_path):
    """The model's own category for a failure, cached by the log excerpt it was shown."""
    import hashlib
    import ai_diagnose  # noqa: E402

    key = hashlib.sha256((d.get("log_excerpt", "") + "|" + d["failed_step"]).encode("utf-8")).hexdigest()[:24]
    if key not in cache:
        try:
            out = ai_diagnose.ai_diagnose(llm, d)
            cache[key] = {"category": out["category"], "confidence": out["confidence"], "cause": out["likely_cause"],
                          "likely_cause": out["likely_cause"], "evidence_verified": out["evidence_verified"],
                          "suggested_fixes": out["suggested_fixes"],
                          "verified": len(out["evidence_verified"]), "quoted": len(out["evidence_verified"]) + len(out["evidence_unverified"])}
        except Exception as e:  # keep scoring even if one call fails; do not cache the failure
            return {"error": str(e)[:200]}
        json.dump(cache, open(cache_path, "w", encoding="utf-8"), indent=1)
    return cache[key]


def is_correct(truth, predicted, extended):
    if truth == "other":
        return predicted in TRUTH_TO_CATEGORY["other"]
    if extended:
        return predicted == EXTENDED.get(truth, truth) or (truth == "config" and predicted in ("script-failure", "configuration"))
    ok = TRUTH_TO_CATEGORY.get(truth)
    return bool(ok) and predicted in ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", default="data/logs")
    ap.add_argument("--answers", default="data/answers.json")
    ap.add_argument("--split", default="all", choices=["all", "dev", "holdout", "round2", "round3", "round4"])
    ap.add_argument("--misses", action="store_true")
    ap.add_argument("--extended", action="store_true", help="score lint/type/code errors against the extended taxonomy")
    ap.add_argument("--json")
    ap.add_argument("--ai", action="store_true", help="also ask the model (Azure AI Foundry) and score its own category")
    ap.add_argument("--ai-cache", default="data/ai_cache.json")
    a = ap.parse_args()

    llm, ai_cache = None, {}
    if a.ai:
        import llm as llm_mod  # noqa: E402
        llm = llm_mod.get_llm()
        if llm is None:
            sys.exit("AI is not enabled in this environment")
        if os.path.exists(a.ai_cache):
            ai_cache = json.load(open(a.ai_cache, encoding="utf-8"))

    answers = json.load(open(a.answers, encoding="utf-8"))
    # later rounds keep their truth in sibling files (answers2.json ...); merge them so one command scores every round
    for extra in ("answers2.json", "answers3.json", "answers4.json"):
        p = os.path.join(os.path.dirname(os.path.abspath(a.answers)), extra)
        if os.path.exists(p) and os.path.basename(a.answers) == "answers.json":
            answers.update(json.load(open(p, encoding="utf-8")))
    recs = load(a.logs)
    rows, excluded = [], []
    for rec in recs:
        did = rec["defect_id"]
        ans = answers[did]
        if a.split != "all" and ans["split"] != a.split:
            continue
        if rec["result"] == "succeeded":
            excluded.append((did, ans["label"], ans["subtype"], "the pipeline passed, so this was not a failure"))
            continue
        cat, d = predict(rec)
        row = {"id": did, "truth": ans["label"], "subtype": ans["subtype"], "ambiguous": ans["ambiguous"], "split": ans["split"],
               "predicted": cat, "ok": is_correct(ans["label"], cat, a.extended), "step": rec["failed_step"],
               "evidence": d.get("evidence", []), "pipeline": rec["pipeline"]}
        if llm:
            ai = ai_category(rec, d, llm, ai_cache, a.ai_cache)
            row["ai"] = ai.get("category", "error")
            row["ai_ok"] = is_correct(ans["label"], row["ai"], a.extended)
            row["ai_note"] = ai
        rows.append(row)

    clear = [r for r in rows if not r["ambiguous"]]
    print(f"split={a.split}  failures scored={len(rows)}  (unambiguous={len(clear)})  excluded={len(excluded)}")
    for e in excluded:
        print("  excluded:", *e)

    def pct(n, d):
        return f"{(100 * n / d):.0f}%" if d else "n/a"

    scorable = [r for r in clear if a.extended or TRUTH_TO_CATEGORY.get(r["truth"]) is not None]
    right = sum(r["ok"] for r in scorable)
    print(f"\nAccuracy on causes the classifier has a category for: {right}/{len(scorable)} = {pct(right, len(scorable))}")

    print("\nPer truth label (unambiguous):")
    for label in sorted({r["truth"] for r in clear}):
        rs = [r for r in clear if r["truth"] == label]
        dist = collections.Counter(r["predicted"] for r in rs)
        okn = sum(r["ok"] for r in rs)
        print(f"  {label:13s} {okn}/{len(rs)} correct   predicted: {dict(dist)}")

    outside = [r for r in clear if not a.extended and TRUTH_TO_CATEGORY.get(r["truth"]) is None]
    if outside:
        d = collections.Counter(r["predicted"] for r in outside)
        wrong_specific = sum(v for k, v in d.items() if k not in ("unknown",))
        print(f"\nCauses with no category in the classifier ({len(outside)} failures): {dict(d)}")
        print(f"  said 'unknown' (honest): {d.get('unknown', 0)};  gave a specific category (not meaningful here): {wrong_specific}")

    labels = sorted({r["truth"] for r in clear})
    preds = sorted({r["predicted"] for r in clear})
    print("\nConfusion (rows = truth, columns = predicted):")
    print("  " + " " * 13 + "  ".join(f"{p[:9]:>9s}" for p in preds))
    for t in labels:
        c = collections.Counter(r["predicted"] for r in clear if r["truth"] == t)
        print(f"  {t:13s}" + "  ".join(f"{c.get(p, 0):9d}" for p in preds))

    if llm:
        sc = [r for r in scorable if "ai_ok" in r]
        ai_right = sum(r["ai_ok"] for r in sc)
        print(f"\nThe model on its own, same scope: {ai_right}/{len(sc)} = {pct(ai_right, len(sc))}  (rules: {right}/{len(scorable)})")
        for label in sorted({r["truth"] for r in clear}):
            rs = [r for r in clear if r["truth"] == label]
            print(f"  {label:13s} model {sum(r['ai_ok'] for r in rs)}/{len(rs)}   said: {dict(collections.Counter(r['ai'] for r in rs))}")
        qv = [(r["ai_note"].get("verified", 0), r["ai_note"].get("quoted", 0)) for r in rows if "verified" in r["ai_note"]]
        if qv:
            print(f"  model quotes found in the log: {sum(v for v, _ in qv)}/{sum(q for _, q in qv)}")

    amb = [r for r in rows if r["ambiguous"]]
    if amb:
        print(f"\nAmbiguous cases (not in the headline number): {[(r['id'], r['truth'], r['predicted']) for r in amb]}")

    if a.misses:
        print("\nMisses:")
        for r in rows:
            if not r["ok"]:
                print(f"  {r['id']} truth={r['truth']}/{r['subtype']} predicted={r['predicted']} step={r['step']!r}")
                for ev in r["evidence"][:2]:
                    print("      evidence:", ev[:140])

    if a.json:
        json.dump({"rows": rows, "excluded": excluded}, open(a.json, "w", encoding="utf-8"), indent=1)


if __name__ == "__main__":
    main()
