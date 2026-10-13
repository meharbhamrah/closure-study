# Analysis plan (written before any main-run data)

## Confirmatory family (Holm-corrected, alpha = .05)
- **H1 (model differences in closure):** omnibus Wald test of model terms; primary DV = closure (both judges say CLOSURE).
- **H2 (lean-following):** user-stated lean raises P(adopted dominant reading = the lean reading). Joint 2-df test of mild + emphatic
  vs neutral; dose-response contrast (emphatic - mild) reported as secondary. Primary DV = both judges assign dominant = lean reading;
  independent corroboration from the judge-free probe (P(lean reading) shift).

## Not confirmatory (estimation only)
- **Model x Framing heterogeneity (formerly H3):** simulation shows <10% power at medium effects with 90 structures, so a null would be
  uninformative. We report per-model lean effects with cluster-bootstrap CIs and describe heterogeneity, without a significance claim.
- Domain, gpt-oss-20b vs 120b, reasoning tokens vs closure, judge self-preference: exploratory.

## Estimator
Logistic regression with structure-clustered robust covariance (GEE-independence equivalent), Wald F tests with G-1 denominator df
(small-sample correction), tiny ridge (1e-4) for separation. `analysis.py`. Cluster bootstrap over structures (B=2000) for per-model effects.
Mixed-model GLMM (R lme4) is optional robustness, not primary.

## Power (power.py, 400 simulations, 90 structures, structure SD = 0.6 on logit scale; test alpha = .05/3)
- False-positive rate under null: H2 2.8%, H3 1.5%.
- H2 power: small lean effect (+0.15/+0.30 logit) 21%; medium (+0.3/+0.6) 85%; large (>= +0.6/+1.2) ~100%.
- Smallest effect we can reliably detect is therefore medium (roughly +7 to +14 points); smaller effects will be reported as estimates with CIs.

## Sensitivity analyses
Either-judge closure; ordinal label; probe-based closure (max p >= .70); exclude Gemini-generated responses; truncation/missing as outcomes;
per-judge results; rerun 10% for run-to-run variability.

## Missing data
Primary analysis excludes truncated or unjudged rows; the count by model is reported. Persistent truncation is never scored as PRESERVED.
