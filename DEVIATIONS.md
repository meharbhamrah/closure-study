# Deviations from the preregistration

Logged before any Claude-produced judge label exists. PREREG.md and the FROZEN hashes are unchanged.

## D1. Judge panel replaced after the Gemma endpoints failed (decided 2026-10-18)

**Preregistered:** judgeA = gemma-4-31b-it, judgeB = gemma-4-26b-a4b-it (Gemini API), identical for every response; primary closure = both judges say CLOSURE.

**What happened:** from 2026-10-16 the two Gemma endpoints on the free Gemini API returned HTTP 500/503 or timed out on most calls. In the last 40-minute test run (2026-10-18, after retries had been made fast) about 50 attempts produced about 2 valid judgements. By 2026-10-18 only 251 (judgeA) and 386 (judgeB) of about 1,000 completed responses had been judged, so judging could not finish in any reasonable time. The failure is on the provider side and does not depend on the content of any response. No outcome of the main run was inspected to make this decision (only completion counts and error logs).

**Change:** judgeA := Claude Sonnet, judgeB := Claude Opus, both applied to every response with the unchanged frozen JUDGE_PROMPT (prompts/templates.py), blind to evaluated model, framing and probe result. The panel is two different Claude models rather than two different labs. analysis.py is unchanged: the merge step writes the Claude labels into the judgeA/judgeB columns it reads. The partial Gemma labels already collected are kept as columns gemmaA/gemmaB and used only as an external cross-check of agreement.

**Consequences, stated for the paper:**
- The two judges share a model family and may share biases, so their agreement overstates independence. Mitigations: (i) judge-vs-human agreement on the human gold set is the validity gate; (ii) agreement with the independent Gemma labels on the overlapping subset is reported; (iii) the judge-free probe outcome is reported alongside.
- Claude also authored the dilemma stimuli. No evaluated model is a Claude model, so there is no self-preference towards a generator, but authorship is shared between stimuli and judges and is disclosed as a limitation.
- Claude judging was done by parallel sub-agents, one batch each, with items shuffled across models/framings/dilemmas so that no batch is dominated by one condition.
