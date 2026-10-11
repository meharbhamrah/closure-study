# Interpretive closure and lean-following: experiment harness

Design: see RESTART_DESIGN.md. Standard library only (Python 3.9+).

## Keys (environment variables, never commit them)
GROQ_API_KEY, CEREBRAS_API_KEY, MISTRAL_API_KEY, GEMINI_API_KEY

## Order of operations
1. `python run.py probe`            verify model ids, list models, save rate-limit headers to config/evidence/. Fix ids in config.py.
2. `python stimuli.py gen --n 150`  candidate dilemmas from a non-evaluated model (resumable).
3. `python stimuli.py screen`       writes stimuli/screening.csv. HUMAN: mark keep=y (>=32 per domain), edit text if needed.
4. `python stimuli.py finalize`     90 main + 6 dry items (fixed-seed split). Commit.
5. `python run.py hash`             record hashes of prompts/templates.py and config.py in the preregistration. FREEZE.
6. `python run.py init --split dry` then `python run.py run --split dry --once`; check `python run.py status` and export. Dry items are excluded from analysis.
7. `python run.py init` then `python run.py run` (loops through quota resets) or let the GitHub Actions workflow run it every 3 h.
8. `python run.py export` writes results/data_main.csv for analysis.

## Behaviour
- State is SQLite (jobs.db); kill and restart freely. Judge jobs are created as soon as a free response is done.
- 429s are classified: daily/token-per-day limits block that provider until the reset (Gemini: midnight Pacific; others: retry-after or 1 h then re-test); short limits wait. Other providers keep working meanwhile.
- Internal 24 h token budgets (config.py) stay under each provider's free cap; per-provider TPM pacing too.
- Truncation: one retry at 2x max_tokens; persistent truncation is flagged (truncated=1) and never scored as preserved.
- Unparseable probe/judge JSON: up to 3 attempts, then status=failed.
- `HARNESS_MOCK=1` runs everything offline (used for testing).

## Not built yet
Automated LLM stimulus raters; analysis scripts (R lme4 + Python GEE); power simulation; gold-set sampler and annotation sheet.
