#!/usr/bin/env python3
"""Stimulus pipeline.  python stimuli.py gen --n 150  ->  screen  ->  (human edits screening.csv)  ->  finalize

Candidates come from a NON-evaluated model (config.STIM_GENERATOR). A human screens every item.
"""
import argparse
import csv
import json
import os
import random
import re
import sys
import time

import config
import providers
from providers import QuotaBlocked, Transient, Fatal, Abort
from prompts import templates as T

HINTS = {
    "career": ["a promotion decision", "sudden change in meeting habits", "feedback that stopped", "a new colleague getting visible projects",
               "a vague message from a senior leader", "being left off an email thread", "a reorganisation rumour", "a mentor going quiet",
               "an unexpected invitation to a leadership meeting", "a project quietly reassigned"],
    "interpersonal": ["a friend who cancelled plans", "a partner's changed routine", "a sibling's short replies", "a roommate's new silence",
                      "a group chat that went quiet", "a parent's odd phone call", "a friend's sudden gift", "a neighbour avoiding eye contact",
                      "an invitation that never came", "a colleague-turned-friend pulling away"],
    "identity": ["doubting a career path", "feeling out of place in a community", "an old hobby losing its appeal", "a cultural tradition feeling hollow",
                 "a sudden urge to change cities", "feeling different around old friends", "a belief that quietly shifted", "pride and shame about a background",
                 "a sense of being behind peers", "unease about a long-held goal"],
}
CAND = "stimuli/candidates.jsonl"
SCREEN = "stimuli/screening.csv"


def call_chain(prompt, blocked):
    """Try each generator in config.STIM_CHAIN; remember quota blocks. Returns (route, result) or (None, None)."""
    for route in config.STIM_CHAIN:
        key = route["provider"] + "|" + route["model_id"]
        if blocked.get(key, 0) > time.time():
            continue
        for attempt in range(3):
            try:
                return route, providers.call(route["provider"], route, prompt, 4000, temperature=1.0)
            except QuotaBlocked as q:
                blocked[key] = min(q.until, time.time() + 600)
                print(f"  {key} blocked: {q.reason[:70]}", flush=True)
                break
            except Transient as e:
                print(f"  {key} temporary error ({str(e)[:80]}); retry {attempt + 1}/3")
                time.sleep(8 * (attempt + 1))
            except Fatal as e:
                print(f"  {key} error: {str(e)[:200]}")
                blocked[key] = time.time() + 3600
                break
            except Abort as e:
                print(f"  {key} unusable: {str(e)[:200]}")
                blocked[key] = time.time() + 86400
                break
    return None, None


def cmd_gen(a):
    os.makedirs("stimuli", exist_ok=True)
    have = [json.loads(l) for l in open(CAND)] if os.path.exists(CAND) else []
    counts = {d: sum(1 for h in have if h["domain"] == d) for d in config.DOMAINS}
    per = a.n // len(config.DOMAINS)
    rnd = random.Random(42 + len(have))
    blocked, tries, misses, bad = {}, 0, 0, 0
    t_end = time.time() + a.max_minutes * 60
    while any(counts[d] < per for d in config.DOMAINS) and tries < a.n * 3 and time.time() < t_end:
        d = min(config.DOMAINS, key=lambda x: counts[x])
        tries += 1
        hint = rnd.choice(HINTS[d])
        prompt = T.STIM_PROMPT.format(domain=d) + f"\nTopic seed for variety: {hint}. Variation id: {rnd.randint(0, 10**6)}"
        used, r = call_chain(prompt, blocked)
        if used is None:
            misses += 1
            if misses >= 3:
                print("All generators unavailable right now; progress is saved, rerun later.")
                break
            time.sleep(20)
            continue
        txt = r["text"]
        m = re.search(r"\{.*\}", txt, flags=re.S)
        try:
            o = json.loads(m.group(0))
            assert isinstance(o["scenario"], str) and len(o["readings"]) == 3 and all(isinstance(x, str) and x for x in o["readings"])
            bad = 0
        except Exception as e:
            bad += 1
            print(f"  unusable output from {used['model_id']}: finish={r['finish_reason']} tokens={r['completion_tokens']} "
                  f"thinking={r['reasoning_tokens']} chars={len(txt)} start={txt[:80]!r}", flush=True)
            if bad >= 8:
                print("8 unusable outputs in a row: stopping so this can be diagnosed.", flush=True)
                break
            continue
        cid = f"{d[:3]}{counts[d] + 1:03d}"
        row = {"id": cid, "domain": d, "scenario": o["scenario"].strip(), "readings": [x.strip() for x in o["readings"]],
               "hint": hint, "generator": used["model_id"]}
        with open(CAND, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        counts[d] += 1
        time.sleep(config.PROVIDERS[used["provider"]]["min_interval"] + 0.5)
    print("candidates per domain:", counts)


def tok(s):
    return set(re.findall(r"[a-z']+", s.lower()))


def cmd_screen(a):
    if not os.path.exists(CAND):
        sys.exit("No candidates yet (stimuli/candidates.jsonl missing): run `stimuli.py gen` first.")
    rows = [json.loads(l) for l in open(CAND)]
    flags = {}
    for i, r in enumerate(rows):
        f = []
        n = len(r["scenario"].split())
        if n < 25 or n > 110:
            f.append(f"length {n} words")
        for j in range(i):
            A, B = tok(r["scenario"]), tok(rows[j]["scenario"])
            if len(A & B) / max(1, len(A | B)) > 0.5:
                f.append(f"near-duplicate of {rows[j]['id']}")
        rs = [tok(x) for x in r["readings"]]
        if any(len(rs[x] & rs[y]) / max(1, len(rs[x] | rs[y])) > 0.6 for x in range(3) for y in range(x + 1, 3)):
            f.append("readings overlap")
        flags[r["id"]] = "; ".join(f)
    with open(SCREEN, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "domain", "scenario", "reading1", "reading2", "reading3", "auto_flags", "keep(y/n)", "notes"])
        for r in rows:
            w.writerow([r["id"], r["domain"], r["scenario"], *r["readings"], flags[r["id"]], "", ""])
    print(f"wrote {SCREEN}. Open it, edit text if needed, put y in the keep column for good items (need >=32 per domain).")


def cmd_finalize(a):
    rows = list(csv.DictReader(open(SCREEN, encoding="utf-8")))
    keep = [r for r in rows if r["keep(y/n)"].strip().lower() == "y"]
    out = []
    for d in config.DOMAINS:
        dk = [r for r in keep if r["domain"] == d]
        need = a.main + a.dry
        if len(dk) < need:
            sys.exit(f"{d}: only {len(dk)} kept, need {need}")
        random.Random(7).shuffle(dk)  # fixed seed: dry/main assignment is not chosen by hand
        for k, r in enumerate(dk[:need]):
            out.append({"id": r["id"], "domain": d, "scenario": r["scenario"], "readings": [r["reading1"], r["reading2"], r["reading3"]],
                        "split": "dry" if k < a.dry else "main"})
    out.sort(key=lambda r: (r["split"] != "dry", r["domain"], r["id"]))
    with open(config.STIMULI_PATH, "w", encoding="utf-8") as fh:
        for r in out:
            fh.write(json.dumps(r) + "\n")
    print(f"wrote {config.STIMULI_PATH}: {len(out)} items ({a.dry * 3} dry, {a.main * 3} main)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("gen"); s.add_argument("--n", type=int, default=150); s.add_argument("--max-minutes", type=float, default=40)
    sub.add_parser("screen")
    s = sub.add_parser("finalize"); s.add_argument("--main", type=int, default=30); s.add_argument("--dry", type=int, default=2)
    a = ap.parse_args()
    {"gen": cmd_gen, "screen": cmd_screen, "finalize": cmd_finalize}[a.cmd](a)
