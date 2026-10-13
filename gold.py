#!/usr/bin/env python3
"""Human gold set.  python gold.py sample   -> results/gold_annotation.csv (blind: no model/framing shown)
                    python gold.py score    -> judge-vs-human agreement per judge and per generator (after you fill labels)
Stratified: equal responses per generator x framing, seeded. Dry-run items are never sampled.
"""
import argparse, csv, json, random, sys
import numpy as np, pandas as pd

DATA = "results/data_main.csv"; SHEET = "results/gold_annotation.csv"; KEY = "results/gold_key.csv"


def cmd_sample(a):
    d = pd.read_csv(DATA)
    d = d[(d.free_truncated == 0) & d.free_text.notna() & d.closure_both.notna()]
    rnd = np.random.RandomState(a.seed)
    cells = d.groupby(["model", "framing"])
    per = max(1, a.n // cells.ngroups)
    pick = pd.concat([g.sample(min(per, len(g)), random_state=rnd) for _, g in cells])
    pick = pick.sample(frac=1, random_state=rnd).reset_index(drop=True)
    stim = {json.loads(l)["id"]: json.loads(l) for l in open("stimuli/stimuli_final.jsonl")}
    pick["item"] = [f"G{i+1:03d}" for i in range(len(pick))]
    rows = []
    for _, r in pick.iterrows():
        s = stim[r.structure]
        rows.append({"item": r["item"], "scenario": s["scenario"], "I1": s["readings"][0], "I2": s["readings"][1], "I3": s["readings"][2],
                     "response": r.free_text, "label(CLOSURE/LEANING/PRESERVED)": "", "dominant(I1/I2/I3/none/other)": ""})
    pd.DataFrame(rows).to_csv(SHEET, index=False)
    pick[["item", "model", "structure", "framing", "lean_k", "judgeA_label", "judgeB_label", "judgeA_dominant", "judgeB_dominant", "closure_both"]].to_csv(KEY, index=False)
    print(f"wrote {SHEET} ({len(pick)} items, blind) and {KEY} (answer key: do NOT open before annotating)")


def kappa_ac1(y1, y2):
    y1, y2 = np.asarray(y1), np.asarray(y2); n = len(y1)
    po = (y1 == y2).mean(); p1, p2 = y1.mean(), y2.mean()
    pe = p1 * p2 + (1 - p1) * (1 - p2)
    k = (po - pe) / (1 - pe) if pe < 1 else float("nan")
    pi = (p1 + p2) / 2; ac1 = (po - 2 * pi * (1 - pi)) / (1 - 2 * pi * (1 - pi)) if 0 < pi < 1 else float("nan")
    return po, k, ac1


def boot(y1, y2, f, B=2000, seed=1):
    rnd = np.random.RandomState(seed); n = len(y1); y1, y2 = np.asarray(y1), np.asarray(y2); v = []
    for _ in range(B):
        i = rnd.randint(0, n, n); v.append(f(y1[i], y2[i]))
    return np.nanpercentile(v, [2.5, 97.5])


def cmd_score(a):
    h = pd.read_csv(SHEET); k = pd.read_csv(KEY)
    h = h.rename(columns={"label(CLOSURE/LEANING/PRESERVED)": "h_label", "dominant(I1/I2/I3/none/other)": "h_dom"})
    d = k.merge(h[["item", "h_label", "h_dom"]], on="item")
    d = d[d.h_label.notna() & (d.h_label.astype(str).str.strip() != "")]
    d["h_closure"] = (d.h_label.str.strip().str.upper() == "CLOSURE").astype(int)
    out = {"n": len(d)}
    for j in ("judgeA", "judgeB", "both"):
        pred = d.closure_both if j == "both" else (d[f"{j}_label"] == "CLOSURE").astype(int)
        po, kap, ac1 = kappa_ac1(d.h_closure, pred)
        out[j] = {"agree": round(po, 3), "kappa": round(kap, 3), "kappa_ci": boot(d.h_closure, pred, lambda a, b: kappa_ac1(a, b)[1]).round(3).tolist(),
                  "ac1": round(ac1, 3), "ac1_ci": boot(d.h_closure, pred, lambda a, b: kappa_ac1(a, b)[2]).round(3).tolist(),
                  "by_model_accuracy": {m: round(float((g.h_closure == (g.closure_both if j == 'both' else (g[f'{j}_label'] == 'CLOSURE').astype(int))).mean()), 3)
                                         for m, g in d.groupby("model")},
                  "bias_by_model(judge-human closure rate)": {m: round(float((g.closure_both if j == 'both' else (g[f'{j}_label'] == 'CLOSURE').astype(int)).mean() - g.h_closure.mean()), 3)
                                                              for m, g in d.groupby("model")}}
    print(json.dumps(out, indent=1)); json.dump(out, open("results/gold_scores.json", "w"), indent=1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="c", required=True)
    s = sp.add_parser("sample"); s.add_argument("--n", type=int, default=120); s.add_argument("--seed", type=int, default=2026)
    sp.add_parser("score")
    a = ap.parse_args(); {"sample": cmd_sample, "score": cmd_score}[a.c](a)
