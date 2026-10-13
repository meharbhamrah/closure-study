#!/usr/bin/env python3
"""Confirmatory + sensitivity analysis. Reads results/data_main.csv, writes results/numbers.json (every paper number comes from here).

Models: logistic regression with cluster-robust (by structure) sandwich covariance, equivalent in point estimates to a GEE with
independence working correlation. Wald chi-square tests; Holm correction over the confirmatory family H1-H3.
Cluster bootstrap (resampling structures) for per-model effects. numpy/scipy/pandas only.
"""
import json, sys
import numpy as np, pandas as pd
from scipy import stats

DATA = "results/data_main.csv"; OUT = "results/numbers.json"; RIDGE = 1e-4; B = 2000


def design(d, interaction):
    X = pd.DataFrame({"const": 1.0}, index=d.index)
    ms = sorted(d.model.unique())
    for m in ms[1:]: X[f"m_{m}"] = (d.model == m).astype(float)
    for f in ("mild", "emphatic"): X[f"f_{f}"] = (d.framing == f).astype(float)
    for dm in sorted(d.domain.unique())[1:]: X[f"d_{dm}"] = (d.domain == dm).astype(float)
    if interaction:
        for m in ms[1:]:
            for f in ("mild", "emphatic"): X[f"m_{m}:f_{f}"] = X[f"m_{m}"] * X[f"f_{f}"]
    return X


def logit_cluster(y, X, g):
    Xv, yv = X.values, np.asarray(y, float); n, k = Xv.shape; b = np.zeros(k)
    for _ in range(100):
        p = 1 / (1 + np.exp(-np.clip(Xv @ b, -30, 30))); W = p * (1 - p)
        H = Xv.T @ (Xv * W[:, None]) + RIDGE * np.eye(k)
        step = np.linalg.solve(H, Xv.T @ (yv - p) - RIDGE * b); b += step
        if np.abs(step).max() < 1e-8: break
    p = 1 / (1 + np.exp(-np.clip(Xv @ b, -30, 30))); W = p * (1 - p)
    Hinv = np.linalg.inv(Xv.T @ (Xv * W[:, None]) + RIDGE * np.eye(k)); S = np.zeros((k, k)); G = np.unique(g)
    for c in G:
        i = g == c; s = Xv[i].T @ (yv[i] - p[i]); S += np.outer(s, s)
    c_adj = len(G) / (len(G) - 1) * (n - 1) / max(n - k, 1)
    V = c_adj * Hinv @ S @ Hinv
    return pd.Series(b, X.columns), pd.DataFrame(V, X.columns, X.columns)


def wald(b, V, cols=None, R=None, G=None):
    if R is None:
        R = np.zeros((len(cols), len(b))); [R.__setitem__((i, list(b.index).index(c)), 1.0) for i, c in enumerate(cols)]
    r = R @ b.values; M = R @ V.values @ R.T
    w = float(r @ np.linalg.pinv(M) @ r); df = int(np.linalg.matrix_rank(M))
    # small-sample correction: F reference with (df, G-1) when the number of clusters G is known (cluster-robust Wald is liberal at G~90)
    p = float(stats.f.sf(w / max(df, 1), df, G - 1)) if G else float(stats.chi2.sf(w, df))
    return {"chi2": round(w, 3), "df": df, "p": p}


def holm(ps):
    o = np.argsort(ps); adj = np.empty(len(ps)); run = 0
    for r, i in enumerate(o): run = max(run, (len(ps) - r) * ps[i]); adj[i] = min(1, run)
    return adj.tolist()


def analyse(d, dv, label, full=True):
    d = d.dropna(subset=[dv]).copy(); g = d.structure.values; res = {"n": int(len(d)), "dv": dv}
    Xa = design(d, False); ba, Va = logit_cluster(d[dv], Xa, g)
    Gn = len(np.unique(g)); res["H1_model"] = wald(ba, Va, [c for c in Xa if c.startswith("m_")], G=Gn)
    res["H2_framing"] = wald(ba, Va, ["f_mild", "f_emphatic"], G=Gn)
    R = np.zeros((1, len(ba))); R[0, list(ba.index).index("f_emphatic")] = 1; R[0, list(ba.index).index("f_mild")] = -1
    res["H2_dose_emphatic_vs_mild"] = wald(ba, Va, R=R, G=Gn) | {"coef": round(float((R @ ba.values)[0]), 3)}
    Xf = design(d, True); bf, Vf = logit_cluster(d[dv], Xf, g)
    res["H3_model_x_framing"] = wald(bf, Vf, [c for c in Xf if ":" in c], G=Gn)
    fam = [res[k]["p"] for k in ("H1_model", "H2_framing", "H3_model_x_framing")]
    for k, a in zip(("H1_model", "H2_framing", "H3_model_x_framing"), holm(fam)): res[k]["p_holm"] = a
    res["odds_ratios_additive"] = {c: round(float(np.exp(ba[c])), 3) for c in ba.index if c != "const"}
    # descriptive + cluster bootstrap per model: lean(any) minus neutral, and by framing
    rnd = np.random.RandomState(7); structs = d.structure.unique(); per = {}
    piv = d.pivot_table(index=["structure", "model"], columns="framing", values=dv).reset_index()
    for m, gm in piv.groupby("model"):
        gm = gm.dropna(subset=["neutral"]); leanv = gm[["mild", "emphatic"]].mean(axis=1)
        diff = (leanv - gm["neutral"]).values; ids = np.arange(len(diff))
        bs = [diff[rnd.choice(ids, len(ids))].mean() for _ in range(B)]
        per[m] = {"neutral": round(float(gm.neutral.mean()), 3), "mild": round(float(gm.mild.mean()), 3), "emphatic": round(float(gm.emphatic.mean()), 3),
                  "lean_minus_neutral": round(float(diff.mean()), 3), "ci95": np.percentile(bs, [2.5, 97.5]).round(3).tolist(), "n_structures": int(len(gm))}
    res["per_model"] = per
    return res


def main():
    d = pd.read_csv(DATA); d = d[d.split == "main"] if "split" in d else d
    out = {"n_rows": int(len(d)), "n_structures": int(d.structure.nunique()), "models": sorted(d.model.unique())}
    out["truncated_free"] = d.groupby("model").free_truncated.mean().round(4).to_dict()
    out["missing_judgement"] = int(d.closure_both.isna().sum())
    ok = d[(d.free_truncated == 0) & d.closure_both.notna()].copy()
    ok["lean_follow_both"] = ((ok.judgeA_dom_is_lean == 1) & (ok.judgeB_dom_is_lean == 1)).astype(int)
    ok["lean_follow_either"] = ((ok.judgeA_dom_is_lean == 1) | (ok.judgeB_dom_is_lean == 1)).astype(int)
    out["primary_closure"] = analyse(ok, "closure_both", "closure")
    out["lean_following"] = analyse(ok, "lean_follow_both", "lean")
    out["closure_either_rule"] = analyse(ok, "closure_either", "either")
    if "probe_closure" in ok:
        pr = ok.dropna(subset=["probe_closure"]); out["probe_closure"] = analyse(pr, "probe_closure", "probe")
        pr = pr.assign(probe_lean_dom=(pr[["p_I1", "p_I2", "p_I3"]].values.argmax(1) + 1 == pr.lean_k).astype(int))
        out["probe_lean_following"] = analyse(pr, "probe_lean_dom", "probe_lean")
        pm = pr.pivot_table(index=["structure", "model"], columns="framing", values="p_lean").reset_index()
        out["probe_mean_p_lean_shift"] = {m: round(float((g[["mild", "emphatic"]].mean(axis=1) - g.neutral).mean()), 3) for m, g in pm.groupby("model")}
    out["judge_agreement"] = {"closure_A_vs_B_raw": round(float(((ok.judgeA_label == "CLOSURE") == (ok.judgeB_label == "CLOSURE")).mean()), 3)}
    if "probe_closure" in ok:
        o2 = ok.dropna(subset=["probe_closure"]); out["judge_vs_probe_closure_agreement"] = round(float((o2.closure_both == o2.probe_closure).mean()), 3)
    if (ok.model == "gemini").any():
        out["sensitivity_excluding_gemini_generated"] = analyse(ok[ok.model != "gemini"], "closure_both", "no_gemini")
    json.dump(out, open(OUT, "w"), indent=1, default=float); print(json.dumps(out, indent=1, default=float)[:3500])


if __name__ == "__main__":
    main()
