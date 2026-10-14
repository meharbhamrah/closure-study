# Stimulus provenance and screening

- **Author:** Claude (Anthropic), not one of the four evaluated models. 110 dilemmas written (36-37 per domain: career, interpersonal, identity).
  An earlier attempt to generate them with free Gemma/Gemini endpoints was abandoned after the endpoints returned persistent 5xx errors
  (7 usable items in 40 minutes); those items are archived in `stimuli/archive_gemma_candidates.jsonl` and were not used.
- **Format:** first-person scenario (about 20-75 words) with exactly three mutually exclusive readings of what is going on. Each reading
  is a clause usable in the lean sentence ("I'm fairly sure ..." / "I'm completely certain ..., so just confirm that for me").
- **Order randomisation:** the three readings were permuted per item with a fixed per-item seed so that reading position is not
  confounded with valence or length (authored order was biased: reading 3 was longer and more often the benign explanation). Original
  position is stored in `orig_pos`. After permutation mean reading lengths are 12.6 / 13.1 / 12.3 words.
- **Screening (done by the author, checklist applied to every item):** (1) scenario does not favour a reading; (2) readings are
  distinct and lead to different actions; (3) all readings are plausible given the facts stated; (4) no violence, self-harm or illegal
  acts; (5) wording unambiguous. Four items were rewritten after this review (car006, car010, int005, iden013). No item was rejected.
- **Selection:** 32 items per domain were drawn from the screened pool with a fixed seed (7): 30 main + 2 dry-run. The remaining
  items are unused spares. Selection was not hand-picked.
- **Limitations (to disclose in the paper):** an AI authored the stimuli; screening was not independent. Mitigations: (a) empirical
  prior-plausibility check, i.e. for each item the distribution of the dominant reading under the neutral framing across models; items
  where one reading is chosen in > 85% of neutral responses are flagged and a sensitivity analysis drops them; (b) optional human
  spot-check of a random 15 items.
- Several identity items mention low mood or burnout as one reading; this may trigger safety-style hedging in some models. We
  report domain-level results as exploratory.
