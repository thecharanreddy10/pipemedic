"""Round 4: write spec4.json (defects, no labels) and answers4.json (truth). The baseline is round 3's; no new pipelines."""
import json
import os
import random

import catalog4

HERE = os.path.dirname(os.path.abspath(__file__))
rng = random.Random(20261018 + 4)
order = list(range(len(catalog4.D)))
rng.shuffle(order)
spec, answers = [], {}
for rank, idx in enumerate(order, start=1):
    d = catalog4.D[idx]
    rid = f"e{rank:03d}"
    spec.append({"id": rid, "branch": f"bench/{rid}", "pipeline": d["pipeline"], "message": d["message"], "edits": d["edits"]})
    answers[rid] = {"label": d["label"], "subtype": d["subtype"], "ambiguous": d["ambiguous"], "note": d["note"],
                    "pipeline": d["pipeline"], "split": "round4"}
out = os.path.join(HERE, "data")
json.dump(spec, open(os.path.join(out, "spec4.json"), "w", encoding="utf-8"))
json.dump(answers, open(os.path.join(out, "answers4.json"), "w", encoding="utf-8"), indent=1)
print(len(spec), "defects;", len(json.dumps(spec)), "bytes")
