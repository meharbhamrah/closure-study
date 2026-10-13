#!/usr/bin/env python3
"""Power + calibration simulation for the lean-following DV (H2, H3). Uses the same estimator as analysis.py.
Data-generating model: logit P = base_model + lean_effect(model, framing) + u_structure, u ~ N(0, sd^2).
Usage: python power.py [--sims 300]
"""
import argparse, json
import numpy as np, pandas as pd
import analysis as A


def sim(rnd, n_struct, sd, eff_mild, eff_emph, het, base=-0.7):
    models = ["gemini", "gpt120", "gpt20", "qwen27"]; rows = []
    u = rnd.normal(0, sd, n_struct); dom = np.array(["career", "interpersonal", "identity"])[np.arange(n_struct) % 3]
    mshift = {"gemini": 0.0, "gpt120": 0.2, "gpt20": -0.2, "qwen27": 0.1}
    hmult = {"gemini": 1 + het, "gpt120": 1 - het, "gpt20": 1.0, "qwen27": 1.0}
    for s in range(n_struct):
        for m in models:
            for f, e in (("neutral", 0), ("mild", eff_mild), ("emphatic", eff_emph)):
                lg = base + mshift[m] + e * hmult[m] + u[s]
                rows.append((f"s{s}", dom[s], m, f, int(rnd.rand() < 1 / (1 + np.exp(-lg)))))
    return pd.DataFrame(rows, columns=["structure", "domain", "model", "framing", "y"])


def run(n_struct, sd, eff_mild, eff_emph, het, sims, seed=11):
    rnd = np.random.RandomState(seed); r2 = r3 = 0
    for _ in range(sims):
        d = sim(rnd, n_struct, sd, eff_mild, eff_emph, het); g = d.structure.values
        Xa = A.design(d, False); ba, Va = A.logit_cluster(d.y, Xa, g)
        r2 += A.wald(ba, Va, ["f_mild", "f_emphatic"], G=n_struct)["p"] < 0.05 / 3
        Xf = A.design(d, True); bf, Vf = A.logit_cluster(d.y, Xf, g)
        r3 += A.wald(bf, Vf, [c for c in Xf if ":" in c], G=n_struct)["p"] < 0.05 / 3
    return round(r2 / sims, 3), round(r3 / sims, 3)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--sims", type=int, default=300); a = ap.parse_args(); out = {}
    print("alpha for each test = .05/3 (conservative Holm bound)")
    out["null_false_positive(H2,H3)"] = run(90, 0.6, 0, 0, 0, a.sims)
    for name, (em, ee, het) in {"small(+0.15/+0.30 logit)": (0.15, 0.3, 0.3), "medium(+0.3/+0.6)": (0.3, 0.6, 0.5), "large(+0.6/+1.2)": (0.6, 1.2, 0.5)}.items():
        for n in (60, 90):
            out[f"{name} n_struct={n} power(H2,H3)"] = run(n, 0.6, em, ee, het, a.sims)
    print(json.dumps(out, indent=1)); json.dump(out, open("results/power.json", "w"), indent=1)
