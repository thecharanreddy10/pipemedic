"""Round 2: write baseline3.json (files added to main), spec2.json (defects, no labels) and answers2.json (truth)."""
import json
import os
import random

import catalog2

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "baseline_web")

files = dict(catalog2.BASELINE2_FILES)
for rel in catalog2.WEB_FILES:
    files["/" + rel] = open(os.path.join(WEB, rel), encoding="utf-8", newline="").read().replace("\r\n", "\n")

rng = random.Random(20261018 + 2)
order = list(range(len(catalog2.D)))
rng.shuffle(order)
spec, answers = [], {}
for rank, idx in enumerate(order, start=1):
    d = catalog2.D[idx]
    cid = f"c{rank:03d}"
    spec.append({"id": cid, "branch": f"bench/{cid}", "pipeline": d["pipeline"], "message": d["message"], "edits": d["edits"]})
    answers[cid] = {"label": d["label"], "subtype": d["subtype"], "ambiguous": d["ambiguous"], "note": d["note"],
                    "pipeline": d["pipeline"], "split": "round2"}

out = os.path.join(HERE, "data")
json.dump(files, open(os.path.join(out, "baseline3.json"), "w", encoding="utf-8"))
json.dump(spec, open(os.path.join(out, "spec2.json"), "w", encoding="utf-8"))
json.dump(answers, open(os.path.join(out, "answers2.json"), "w", encoding="utf-8"), indent=1)
print(len(files), "baseline files;", len(spec), "defects;", len(json.dumps(files)), "+", len(json.dumps(spec)), "bytes")
