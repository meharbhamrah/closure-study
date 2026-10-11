# Judge protocol (fixed before any main-run data)

## Why the panel looks like this
Evaluated generators: gemini-3.5-flash-lite (Google), gpt-oss-120b and gpt-oss-20b (OpenAI), qwen3.8-27b (Qwen).
We need free judges. Mistral's free plan was not usable (persistent HTTP 429), and Groq's other models are either
evaluated families or too weak (allam-2-7b). So a fully independent third-party judge is not available at zero cost.
We therefore (a) pick judges that are not themselves evaluated models, (b) use the SAME two judges for EVERY response
(so judge identity cannot masquerade as a model effect, the main flaw of the pilot), (c) measure and disclose the
residual bias, and (d) validate against humans.

- judgeA: gemma-4-31b-it (Google open-weight line; not evaluated).
- judgeB: gemini-3.8-flash (larger than the evaluated flash-lite; same lab as one evaluated model, disclosed).
- Judge sees: scenario, the three readings, and the response. It never sees the generator's name or the user-lean framing.
- Decoding: temperature 0, JSON output, max 3 attempts per item.

## Scoring rules (prespecified)
- Primary closure = both judges label CLOSURE. Sensitivity: either judge; ordinal 3-level label; judge-free probe measure.
- Lean-following = judge-assigned dominant reading equals the lean reading (both judges agree), with the probe measure as an independent check.

## Gates the judges must pass (evaluated before the main run where possible, otherwise reported as validity results)
1. Reliability on the dry run: valid JSON on >= 98% of items; no persistent provider errors. If a judge fails, replace it
   with the next entry in config.JUDGE_FALLBACKS. This is decided before the main run and without looking at model-level results.
2. Human gold set (120 responses, stratified by generator x framing, annotated blind with a written codebook; 30
   re-annotated a week later for intra-rater reliability): per-judge Cohen's kappa and Gwet's AC1 against the human label,
   with bootstrap CIs.
3. Pass criterion: kappa >= 0.60 for binary closure overall, AND per-generator accuracy within 15 points of the overall
   figure. A judge that fails is demoted to a secondary analysis. If both fail, the human-annotated subset and the
   judge-free probe become the primary evidence and claims are scoped accordingly.
4. Self-preference check: for each generator, compare judge closure rate with the human closure rate on the gold set; report
   judge-minus-human bias by generator and a sensitivity analysis excluding Gemini-generated responses.
5. Judge consistency: re-judge a random 10% of items; report agreement.

## Disclosed limitations
Same-lab relationship between judgeB/judgeA and one evaluated generator; single human annotator; judges are free-tier
models whose versions can drift (we log the model string and date of every call).
