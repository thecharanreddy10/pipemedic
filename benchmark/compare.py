"""Compare classifier versions on the same collected failures, in aggregate only (no per-case output for the holdout split)."""
import collections
import importlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VERSIONS = os.path.join(HERE, "classifier_versions")
LIVE = os.path.join(HERE, "..", "agent")
VARIANTS = {
    "v1 original (as first measured)": os.path.join(VERSIONS, "v1_original"),
    "v1 + word-boundary fix": os.path.join(VERSIONS, "v1b_word_boundary_fix"),
    "v2 (frozen before round 2)": os.path.join(VERSIONS, "v2"),
    "v3 (frozen before round 3)": os.path.join(VERSIONS, "v3"),
    "v4 (frozen before round 4)": os.path.join(VERSIONS, "v4"),
    "current (rules only)": LIVE,
    "current + AI refinement": (LIVE, True),
}
EXTENDED = {"lint": "static-analysis", "type-error": "static-analysis", "code-error": "code-error"}
BASELINE_SUPPORTED = {"test-failure", "dependency", "network", "permissions", "timeout", "config"}


def correct(truth, predicted):
    if truth == "config":
        return predicted in ("script-failure", "configuration")
    if truth == "other":  # a custom message no rule could know: saying so, or a generic script failure, is right
        return predicted in ("unknown", "script-failure")
    return predicted == EXTENDED.get(truth, truth)


def run(agent_dir, recs, answers):
    use_ai = False
    if isinstance(agent_dir, tuple):
        agent_dir, use_ai = agent_dir
    ai_cache = {}
    cache_path = os.path.join(HERE, "data", "ai_cache.json")
    if use_ai and os.path.exists(cache_path):
        ai_cache = json.load(open(cache_path, encoding="utf-8"))
    for m in ("diagnose", "ai_diagnose", "config"):
        sys.modules.pop(m, None)
    sys.path.insert(0, agent_dir)
    try:
        diagnose = importlib.import_module("diagnose")
        out = {}
        for r in recs:
            f = {"pipeline": r["pipeline"], "build_id": r["build_id"], "failed_step": r["failed_step"], "issues": r.get("issues", []),
                 "log_text": r.get("log_text", ""), "agent_name": None, "hostname": None, "start": r.get("start"), "finish": r.get("finish")}
            d = diagnose.diagnose(f, [])
            if use_ai:
                import hashlib
                key = hashlib.sha256((d.get("log_excerpt", "") + "|" + d["failed_step"]).encode("utf-8")).hexdigest()[:24]
                ai = ai_cache.get(key)
                if ai:
                    d = diagnose.apply_ai(d, ai)
            out[r["defect_id"]] = d["category"]
        return out
    finally:
        sys.path.remove(agent_dir)


def main():
    answers = json.load(open(os.path.join(HERE, "data", "answers.json"), encoding="utf-8"))
    for extra in ("answers2.json", "answers3.json", "answers4.json"):
        p2 = os.path.join(HERE, "data", extra)
        if os.path.exists(p2):
            answers.update(json.load(open(p2, encoding="utf-8")))
    logs = os.path.join(HERE, "data", "logs")
    recs = [json.load(open(os.path.join(logs, n), encoding="utf-8")) for n in sorted(os.listdir(logs)) if n.startswith("run_")]
    recs = [r for r in recs if r["defect_id"] in answers]
    not_failed = [r["defect_id"] for r in recs if r["result"] == "succeeded"]
    ambiguous = [r["defect_id"] for r in recs if answers[r["defect_id"]]["ambiguous"]]
    print("did not fail:", not_failed, " ambiguous (left out):", ambiguous)
    recs = [r for r in recs if r["result"] != "succeeded" and not answers[r["defect_id"]]["ambiguous"]]
    preds = {name: run(path, recs, answers) for name, path in VARIANTS.items()}

    for split in ("dev", "holdout", "round2", "round3", "round4", "all"):
        rs = [r for r in recs if split == "all" or answers[r["defect_id"]]["split"] == split]
        print(f"\n=== {split}: {len(rs)} real failures (unambiguous, actually failed)")
        for name in VARIANTS:
            ok = sum(correct(answers[r["defect_id"]]["label"], preds[name][r["defect_id"]]) for r in rs)
            sup = [r for r in rs if answers[r["defect_id"]]["label"] in BASELINE_SUPPORTED]
            oks = sum(correct(answers[r["defect_id"]]["label"], preds[name][r["defect_id"]]) for r in sup)
            unk = sum(preds[name][r["defect_id"]] == "unknown" for r in rs)
            print(f"  {name:32s} all causes {ok:2d}/{len(rs):2d} = {100 * ok / max(1, len(rs)):3.0f}%   "
                  f"causes it already had a category for {oks:2d}/{len(sup):2d} = {100 * oks / max(1, len(sup)):3.0f}%   said unknown: {unk}")
        if split in ("holdout", "round2", "round3", "round4"):
            for name in VARIANTS:
                by = collections.defaultdict(lambda: [0, 0])
                for r in rs:
                    t = answers[r["defect_id"]]["label"]
                    by[t][1] += 1
                    by[t][0] += correct(t, preds[name][r["defect_id"]])
                print(f"    {name:32s} " + "  ".join(f"{t}:{a}/{b}" for t, (a, b) in sorted(by.items())))


if __name__ == "__main__":
    main()
