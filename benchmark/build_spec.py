"""Turn catalog.py into spec.json (what is pushed: no labels) and answers.json (ground truth, kept apart)."""
import json
import os
import random

import catalog

HERE = os.path.dirname(os.path.abspath(__file__))
rng = random.Random(20261018)

defects = list(catalog.D)
order = list(range(len(defects)))
rng.shuffle(order)

# Hold out every third defect within each label before any result has been looked at.
seen = {}
split = {}
for idx, d in enumerate(defects):
    n = seen.get(d["label"], 0)
    split[idx] = "holdout" if n % 3 == 2 else "dev"
    seen[d["label"]] = n + 1

spec, answers = [], {}
for rank, idx in enumerate(order, start=1):
    d = defects[idx]
    bid = f"b{rank:03d}"
    spec.append({"id": bid, "branch": f"bench/{bid}", "pipeline": d["pipeline"], "message": d["message"], "edits": d["edits"]})
    answers[bid] = {"label": d["label"], "subtype": d["subtype"], "ambiguous": d["ambiguous"], "note": d["note"],
                    "pipeline": d["pipeline"], "split": split[idx]}

os.makedirs(os.path.join(HERE, "data"), exist_ok=True)
json.dump(spec, open(os.path.join(HERE, "data", "spec.json"), "w", encoding="utf-8"))
json.dump(answers, open(os.path.join(HERE, "data", "answers.json"), "w", encoding="utf-8"), indent=1)
print(len(spec), "defects;", sum(1 for a in answers.values() if a["split"] == "holdout"), "held out")
