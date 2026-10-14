#!/usr/bin/env python3
"""Build stimuli/candidates.jsonl from the authored source files. Reading order is randomised per item with a fixed seed
(so position is not confounded with valence/length); the original authoring position is stored in orig_pos."""
import hashlib, json, random, sys
sys.path.insert(0, "stimuli/src")
import career, interpersonal, identity
rows = []
for d, mod in (("career", career), ("interpersonal", interpersonal), ("identity", identity)):
    for i, (sc, rd) in enumerate(mod.ITEMS, 1):
        cid = f"{d[:3]}{i:03d}"
        rnd = random.Random(int(hashlib.sha256(("order|" + cid).encode()).hexdigest(), 16))
        perm = rnd.sample([0, 1, 2], 3)
        rows.append({"id": cid, "domain": d, "scenario": sc, "readings": [rd[p] for p in perm], "orig_pos": perm, "generator": "claude-authored"})
with open("stimuli/candidates.jsonl", "w", encoding="utf-8") as fh:
    for r in rows: fh.write(json.dumps(r) + "\n")
print(len(rows), "candidates written")
