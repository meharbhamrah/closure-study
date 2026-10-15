# Preregistration: lean-following and interpretive closure in LLM advice (frozen 2026-10-15, before any main-run data)

Time-stamped by git commit history of this repository. Supersedes RESTART_DESIGN.md where they differ. Pilot data (Study 1) were audited and are not used.

## Question
When a user's message to an LLM about an ambiguous personal situation states a lean toward one of three possible readings, does the model adopt that reading, and does that depend on how certain the user sounds? How often do models commit to one reading (closure) vs keep readings open?

## Design
- 90 main dilemmas (30 career, 30 interpersonal, 30 identity), each with three readings in a fixed, seeded random order; 6 further dilemmas are a dry run and are excluded from analysis. Stimuli authored by Claude and screened by the author (STIMULI_SCREENING.md).
- Framing (within dilemma): neutral; mild lean ("I'm fairly sure ..."); emphatic lean ("I'm completely certain ..., so just confirm that for me"). The lean reading rotates deterministically so each reading is the lean equally often.
- Four generators: gemini-3.5-flash-lite, gpt-oss-120b, gpt-oss-20b (reasoning effort low), qwen3.8-27b. Temperature 0 where supported. Token caps and one retry at doubled cap (bounded by provider window); persistent truncation is its own category, never PRESERVED.
- Per cell: one free response and one probe (JSON probabilities over the three readings, a judge-free measure). 90 x 3 x 4 = 1,080 cells.
- Judges: gemma-4-31b-it and gemma-4-26b-a4b-it, identical for all responses (JUDGE_PROTOCOL.md). Fallback gemini-3.6-flash only if a judge fails reliability checks, decided on the dry run.

## Hypotheses (confirmatory family, Holm-corrected)
- H1: closure rate (both judges say CLOSURE) differs across the four models.
- H2: P(judged dominant reading = the lean reading) is higher under lean framings than neutral (joint test of mild and emphatic; dose-response emphatic vs mild secondary). Corroborated by the probe: shift in P(lean reading).
Everything else (per-model heterogeneity, domain, 20b vs 120b, reasoning tokens, judge self-preference) is estimation or exploratory (ANALYSIS_PLAN.md).

## Analysis
As in ANALYSIS_PLAN.md (clustered logistic regression, F tests with G-1 df, cluster bootstrap, Holm over H1-H2 only). Sensitivity: either-judge closure, ordinal label, probe-based measures, excluding Gemini-generated responses, truncation as outcome.

## Validity
Human gold set (60 responses, blind to model and framing, written codebook; 15 re-labelled later) and an additional blind LLM annotation by a model outside all evaluated and judge families, scored as in JUDGE_PROTOCOL.md. A judge that fails the criteria is demoted and claims are scoped accordingly.

## Stopping rule and exclusions
Data collection runs until all 1,080 cells are generated and judged, or 10 days elapse. Only complete dilemma blocks (all 12 cells) are analysed. Unjudged or persistently truncated rows are excluded from the primary analysis and reported by model.

## Disclosures
Designed after a pilot and a dry run (descriptive observations recorded in JUDGE_PROTOCOL.md). Free-tier endpoints: model versions can change; every call logs model id and date. Stimuli and screening are by an AI author. Judges and one generator share a lab.
