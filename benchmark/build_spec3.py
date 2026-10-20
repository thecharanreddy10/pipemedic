"""Round 3: write baseline4.json (files added to main), spec3.json (defects, no labels) and answers3.json (truth)."""
import json
import os
import random

import catalog3

HERE = os.path.dirname(os.path.abspath(__file__))
R3 = os.path.join(HERE, "baseline_r3")

files = dict(catalog3.BASELINE3_FILES)
for rel in catalog3.PROJECT_FILES:
    files["/" + rel] = open(os.path.join(R3, *rel.split("/")), encoding="utf-8", newline="").read().replace("\r\n", "\n")

rng = random.Random(20261018 + 3)
order = list(range(len(catalog3.D)))
rng.shuffle(order)
spec, answers = [], {}
for rank, idx in enumerate(order, start=1):
    d = catalog3.D[idx]
    rid = f"d{rank:03d}"
    spec.append({"id": rid, "branch": f"bench/{rid}", "pipeline": d["pipeline"], "message": d["message"], "edits": d["edits"]})
    answers[rid] = {"label": d["label"], "subtype": d["subtype"], "ambiguous": d["ambiguous"], "note": d["note"],
                    "pipeline": d["pipeline"], "split": "round3"}

out = os.path.join(HERE, "data")
json.dump(files, open(os.path.join(out, "baseline4.json"), "w", encoding="utf-8"))
json.dump(spec, open(os.path.join(out, "spec3.json"), "w", encoding="utf-8"))
json.dump(answers, open(os.path.join(out, "answers3.json"), "w", encoding="utf-8"), indent=1)
print(len(files), "baseline files;", len(spec), "defects;", len(json.dumps(files)), "+", len(json.dumps(spec)), "bytes")
