#!/usr/bin/env python3
"""Quota-aware experiment runner.  Usage: python run.py {init,probe,run,status,export,hash} ...

All state lives in SQLite (config.DB_PATH). Safe to kill and restart at any time.
"""
import argparse
import collections
import csv
import hashlib
import json
import os
import random
import re
import sqlite3
import sys
import time

import config
import providers
from providers import Abort, Fatal, QuotaBlocked, Transient
from prompts import templates as T

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs(
  job_id TEXT PRIMARY KEY, kind TEXT, model TEXT, judge TEXT, structure TEXT, framing TEXT,
  replicate INTEGER DEFAULT 0, parent TEXT, split TEXT, ord INTEGER,
  status TEXT DEFAULT 'pending', attempts INTEGER DEFAULT 0,
  provider TEXT, model_id TEXT, response TEXT, finish_reason TEXT, truncated INTEGER DEFAULT 0,
  prompt_tokens INTEGER, completion_tokens INTEGER, reasoning_tokens INTEGER, latency REAL,
  error TEXT, updated REAL
);
CREATE INDEX IF NOT EXISTS ix_status ON jobs(status, kind);
CREATE TABLE IF NOT EXISTS provider_state(provider TEXT PRIMARY KEY, blocked_until REAL, note TEXT);
"""


def connect():
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


# ---------------------------------------------------------------- stimuli
def load_stimuli():
    rows = [json.loads(l) for l in open(config.STIMULI_PATH, encoding="utf-8") if l.strip()]
    ids = set()
    per_dom = collections.Counter()
    for i, s in enumerate(rows):
        assert s["id"] not in ids, f"duplicate id {s['id']}"
        ids.add(s["id"])
        assert s["domain"] in config.DOMAINS, s["id"]
        assert len(s["readings"]) == 3 and all(s["readings"]), s["id"]
        s.setdefault("split", "main")
        # deterministic lean rotation within domain+split so each reading is the lean equally often
        s["lean_k"] = s.get("lean_k") or (per_dom[(s["domain"], s["split"])] % 3) + 1
        per_dom[(s["domain"], s["split"])] += 1
        rnd = random.Random(int(hashlib.sha256(s["id"].encode()).hexdigest(), 16))
        s["probe_order"] = s.get("probe_order") or rnd.sample([0, 1, 2], 3)
        s["ord"] = i
    return rows


def stim_by_id():
    return {s["id"]: s for s in load_stimuli()}


def lean_sentence(s, framing):
    reading = s["readings"][s["lean_k"] - 1]
    if framing == "mild":
        return T.LEAN_MILD.format(reading=reading)
    if framing == "emphatic":
        return T.LEAN_EMPHATIC.format(reading=reading)
    return None


def build_prompt(conn, job, S):
    s = S[job["structure"]]
    ls = lean_sentence(s, job["framing"])
    if job["kind"] == "free":
        return (T.FREE_LEAN.format(scenario=s["scenario"], lean_sentence=ls) if ls
                else T.FREE_NEUTRAL.format(scenario=s["scenario"]))
    if job["kind"] == "probe":
        base = s["scenario"] + (("\n\n" + ls) if ls else "")
        listing = "\n".join(f"{'ABC'[j]}: {s['readings'][idx]}" for j, idx in enumerate(s["probe_order"]))
        return base + T.PROBE_SUFFIX.format(reading_list=listing)
    if job["kind"] == "judge":
        parent = conn.execute("SELECT response FROM jobs WHERE job_id=?", (job["parent"],)).fetchone()
        r = s["readings"]
        return T.JUDGE_PROMPT.format(scenario=s["scenario"], r1=r[0], r2=r[1], r3=r[2], response=parent["response"])
    raise ValueError(job["kind"])


def route_of(job):
    return config.JUDGES[job["judge"]] if job["kind"] == "judge" else config.MODELS[job["model"]]


# ---------------------------------------------------------------- init / status
def cmd_init(args):
    conn = connect()
    S = load_stimuli()
    r = conn.execute("UPDATE jobs SET status='pending', error=NULL WHERE status='failed' AND error LIKE 'request too large%'").rowcount
    conn.commit()
    if r:
        log(f"init: reset {r} jobs that failed on the mistaken 'request too large' = fatal rule")
    # D2: Gemma labels collected under the names judgeA/judgeB become gemmaA/gemmaB; unfinished Gemma jobs are dropped
    # so the new judgeA/judgeB (Gemini lite models) are created fresh for every response.
    for old_n, new_n in (("judgeA", "gemmaA"), ("judgeB", "gemmaB")):
        conn.execute("UPDATE jobs SET judge=?, job_id=replace(job_id, ?, ?) WHERE kind='judge' AND judge=? AND model_id LIKE 'gemma%'",
                     (new_n, "judge|" + old_n + "|", "judge|" + new_n + "|", old_n))
    conn.execute("DELETE FROM jobs WHERE kind='judge' AND judge IN ('judgeA','judgeB') AND status!='done'")
    conn.execute("DELETE FROM jobs WHERE kind='judge' AND judge='judgeA' AND model_id='gemini-3.6-flash'")  # D2b: 2 stray labels from the abandoned judgeA model
    conn.commit()
    n = 0
    for s in S:
        if args.split != "all" and s["split"] != args.split:
            continue
        for m in config.MODELS:
            for f in config.FRAMINGS:
                for kind in ("free", "probe"):
                    jid = f"{kind}|{m}|{s['id']}|{f}|0"
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO jobs(job_id,kind,model,structure,framing,replicate,split,ord) VALUES(?,?,?,?,?,?,?,?)",
                        (jid, kind, m, s["id"], f, 0, s["split"], s["ord"]))
                    n += cur.rowcount
    conn.commit()
    log(f"init: {n} new generation jobs ({len(S)} stimuli in file). Judge jobs are created as responses arrive.")


def spawn_judges(conn):
    rows = conn.execute("SELECT * FROM jobs WHERE kind='free' AND status='done' AND truncated=0").fetchall()
    n = 0
    for r in rows:
        for j in config.JUDGES:
            jid = f"judge|{j}|{r['job_id']}"
            n += conn.execute(
                "INSERT OR IGNORE INTO jobs(job_id,kind,model,judge,structure,framing,replicate,parent,split,ord) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (jid, "judge", r["model"], j, r["structure"], r["framing"], 0, r["job_id"], r["split"], r["ord"])).rowcount
    if n:
        conn.commit()
    return n


def cmd_status(args):
    conn = connect()
    spawn_judges(conn)
    rows = conn.execute("SELECT kind, COALESCE(judge, model) AS who, status, COUNT(*) c FROM jobs GROUP BY 1,2,3 ORDER BY 1,2,3").fetchall()
    tab = collections.defaultdict(dict)
    for r in rows:
        tab[(r["kind"], r["who"])][r["status"]] = r["c"]
    print(f"{'kind':7}{'who':10}{'pending':>9}{'done':>7}{'failed':>8}{'truncated':>10}")
    for (k, w), d in sorted(tab.items()):
        t = conn.execute("SELECT COUNT(*) FROM jobs WHERE kind=? AND COALESCE(judge,model)=? AND truncated=1", (k, w)).fetchone()[0]
        print(f"{k:7}{w:10}{d.get('pending', 0):9}{d.get('done', 0):7}{d.get('failed', 0):8}{t:10}")
    for r in conn.execute("SELECT * FROM provider_state").fetchall():
        if r["blocked_until"] and r["blocked_until"] > time.time():
            print(f"BLOCKED {r['provider']} until {time.strftime('%Y-%m-%d %H:%M', time.localtime(r['blocked_until']))}: {r['note']}")
    for lid, L in sorted(lanes().items()):
        used = tokens_24h(conn, L["provider"], L["route"]["model_id"])
        print(f"tokens last 24h {lid}: {used} / budget {L['route'].get('tpd_budget', config.PROVIDERS[L['provider']]['tpd_budget'])}")


def tokens_24h(conn, provider, model_id):
    r = conn.execute("SELECT COALESCE(SUM(COALESCE(prompt_tokens,0)+COALESCE(completion_tokens,0)),0) FROM jobs WHERE provider=? AND model_id=? AND updated>?",
                     (provider, model_id, time.time() - 86400)).fetchone()
    return r[0]


def lanes(args=None):
    """A lane = one (provider, model_id). Limits are tracked per lane (Groq lists per-model limits)."""
    L = {}
    for kind, d in (("model", config.MODELS), ("judge", config.JUDGES)):
        for name, v in d.items():
            lid = f"{v['provider']}|{v['model_id']}"
            L.setdefault(lid, {"id": lid, "provider": v["provider"], "route": v, "models": [], "judges": []})
            L[lid]["models" if kind == "model" else "judges"].append(name)
    return L


# ---------------------------------------------------------------- parsing
def extract_json(text):
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def validate(kind, obj):
    if obj is None:
        return False
    if kind == "probe":
        try:
            ps = [float(obj[k]) for k in ("p_A", "p_B", "p_C")]
        except (KeyError, TypeError, ValueError):
            return False
        return all(0 <= p <= 1 for p in ps) and abs(sum(ps) - 1) < 0.05
    if kind == "judge":
        return (obj.get("label") in ("CLOSURE", "LEANING", "PRESERVED") and
                obj.get("dominant") in ("I1", "I2", "I3", "none", "other") and isinstance(obj.get("coverage"), int))
    return True


# ---------------------------------------------------------------- execution
class PState:
    def __init__(self):
        self.next_allowed = 0.0
        self.blocked_until = 0.0
        self.fails = 0  # consecutive transient failures on this lane
        self.window = collections.deque()  # (time, tokens)


def tpm_wait(st, tpm, est):
    t = time.time()
    while st.window and st.window[0][0] < t - 60:
        st.window.popleft()
    used = sum(x[1] for x in st.window)
    if used + est <= tpm or not st.window:
        return 0.0
    return max(0.5, st.window[0][0] + 60 - t)


def set_blocked(conn, st, provider, until, note):
    st.blocked_until = until
    conn.execute("INSERT OR REPLACE INTO provider_state VALUES(?,?,?)", (provider, until, note))
    conn.commit()
    log(f"BLOCKED {provider} until {time.strftime('%H:%M:%S', time.localtime(until))} ({note[:400]})")


def finish(conn, job, **kw):
    kw["updated"] = time.time()
    cols = ", ".join(f"{k}=?" for k in kw)
    conn.execute(f"UPDATE jobs SET {cols} WHERE job_id=?", (*kw.values(), job["job_id"]))
    conn.commit()


def execute(conn, job, P, st, S, lid):
    route = route_of(job)
    prompt = build_prompt(conn, job, S)
    cap = config.max_tokens(job["kind"], route)
    t0 = time.time()
    res = None
    for attempt in (1, 2):
        try:
            res = providers.call(P, route, prompt, cap)
        except QuotaBlocked as q:
            set_blocked(conn, st, lid, q.until, q.reason)
            return
        except Transient as e:
            n = job["attempts"] + 1
            st.fails += 1
            # Gemma/Gemini 5xx errors are frequent but mostly pass on an immediate retry, so retry quickly and only
            # back off when the lane has failed many times in a row.
            wait = 20 if st.fails < 8 else 60 if st.fails < 20 else 240
            conn.execute("UPDATE jobs SET attempts=?, error=? WHERE job_id=?", (n, str(e)[:300], job["job_id"]))
            conn.commit()
            set_blocked(conn, st, lid, time.time() + wait, f"transient ({n}): {str(e)[:80]}")
            return
        except Fatal as e:
            finish(conn, job, status="failed", error=str(e)[:300], provider=P)
            log(f"FATAL job {job['job_id']}: {e}")
            return
        st.fails = 0
        st.window.append((time.time(), (res["prompt_tokens"] or len(prompt) // 4) + (res["completion_tokens"] or cap // 2)))
        if res["finish_reason"] == "length" or (not res["text"].strip() and str(res["finish_reason"]).startswith(("length", "stop"))):
            if attempt == 1:
                lim = config.PROVIDERS[P]["tpm"]
                headroom = (lim - 700 - len(prompt) // 3) if lim <= 10000 else 10**9  # Groq rejects prompt+max_tokens above its 8K window
                cap = max(cap, min(cap * 2, headroom))  # prereg: one retry at doubled max_tokens (as far as the provider window allows)
                st.next_allowed = time.time() + config.PROVIDERS[P]["min_interval"]
                time.sleep(config.PROVIDERS[P]["min_interval"])
                continue
        break
    st.next_allowed = time.time() + config.PROVIDERS[P]["min_interval"]
    truncated = int(res["finish_reason"] == "length" or not res["text"].strip())
    common = dict(provider=P, model_id=route["model_id"], finish_reason=str(res["finish_reason"]), truncated=truncated,
                  prompt_tokens=res["prompt_tokens"], completion_tokens=res["completion_tokens"],
                  reasoning_tokens=res["reasoning_tokens"], latency=round(time.time() - t0, 2), error=None)
    if job["kind"] in ("probe", "judge"):
        obj = extract_json(res["text"])
        if not validate(job["kind"], obj):
            n = job["attempts"] + 1
            if n >= 3:
                finish(conn, job, status="failed", attempts=n, response=res["text"], **{**common, "error": "unparseable"})
            else:
                conn.execute("UPDATE jobs SET attempts=?, error='unparseable', updated=? WHERE job_id=?", (n, time.time(), job["job_id"]))
                conn.commit()
            return
        finish(conn, job, status="done", response=json.dumps(obj), **common)
        return
    finish(conn, job, status="done", response=res["text"], **common)
    if truncated:
        log(f"truncated: {job['job_id']}")


def next_job(conn, lane, args):
    ms = [m for m in lane["models"] if (not args.models or m in args.models)]
    js = list(lane["judges"])
    parts, params = [], []
    if ms and ("free" in args.kinds or "probe" in args.kinds):
        ks = [k for k in ("free", "probe") if k in args.kinds]
        parts.append(f"(kind IN ({','.join('?' * len(ks))}) AND model IN ({','.join('?' * len(ms))}))")
        params += ks + ms
    if js and "judge" in args.kinds:
        parts.append(f"(kind='judge' AND judge IN ({','.join('?' * len(js))}))")
        params += js
    if not parts:
        return None
    where = "status='pending' AND (" + " OR ".join(parts) + ")"
    if args.split != "all":
        where += " AND split=?"
        params.append(args.split)
    return conn.execute(f"SELECT * FROM jobs WHERE {where} ORDER BY ord, framing, kind LIMIT 1", params).fetchone()


def active_lanes(args):
    out = {}
    for lid, L in lanes().items():
        ms = [m for m in L["models"] if (not args.models or m in args.models)]
        if ({"free", "probe"} & set(args.kinds) and ms) or ("judge" in args.kinds and L["judges"]):
            out[lid] = L
    return out


def cmd_run(args):
    conn = connect()
    ls = {}
    for lid, L in active_lanes(args).items():
        if providers.has_key(L["provider"]):
            ls[lid] = L
        else:
            log(f"skipping {lid}: {config.PROVIDERS[L['provider']]['key_env']} not set")
    state = {lid: PState() for lid in ls}
    for r in conn.execute("SELECT * FROM provider_state").fetchall():
        if r["provider"] in state and r["blocked_until"] > time.time():
            state[r["provider"]].blocked_until = r["blocked_until"]
    done_calls, last_report = 0, time.time()
    while True:
        spawn_judges(conn)
        progressed, earliest, pending_any = False, None, False
        for lid, L in ls.items():
            P, st = L["provider"], state[lid]
            job = next_job(conn, L, args)
            if not job:
                continue
            pending_any = True
            t = time.time()
            budget = L["route"].get("tpd_budget", config.PROVIDERS[P]["tpd_budget"])
            if tokens_24h(conn, P, L["route"]["model_id"]) >= budget:
                set_blocked(conn, st, lid, t + 1800, "internal 24h token budget reached")
            wake = max(st.blocked_until, st.next_allowed)
            if wake > t:
                earliest = wake if earliest is None else min(earliest, wake)
                continue
            est = len(build_prompt(conn, job, STIM)) // 4 + config.max_tokens(job["kind"], route_of(job)) // 2
            w = tpm_wait(st, config.PROVIDERS[P]["tpm"], est)
            if w > 0:
                st.next_allowed = t + w
                earliest = st.next_allowed if earliest is None else min(earliest, st.next_allowed)
                continue
            try:
                execute(conn, job, P, st, STIM, lid)
            except Abort as e:
                # One provider refusing us (bad key, project denied) must not stop the other providers' lanes.
                log(f"LANE DISABLED for 3h ({lid}): {str(e)[:300]}")
                set_blocked(conn, st, lid, time.time() + 3 * 3600, f"access refused: {str(e)[:150]}")
                continue
            progressed = True
            done_calls += 1
            if args.limit and done_calls >= args.limit:
                log("call limit reached")
                return 0
        if time.time() - last_report > 300:
            last_report = time.time()
            log("progress: " + "; ".join(f"{r['kind']}/{r['status']}={r['c']}" for r in conn.execute(
                "SELECT kind,status,COUNT(*) c FROM jobs GROUP BY 1,2").fetchall()))
        if not progressed:
            if not pending_any:
                if spawn_judges(conn) == 0:
                    log("nothing pending for selected work: finished")
                    return 0
                continue
            if earliest is None:
                return 0
            wait = earliest - time.time()
            if args.once and wait > 120:
                log(f"all lanes blocked/pending for {wait/60:.0f} more minutes; --once so exiting")
                return 0
            time.sleep(max(0.2, min(wait, 60)))


# ---------------------------------------------------------------- probe
def cmd_probe(args):
    os.makedirs("config/evidence", exist_ok=True)
    out = {"date": time.strftime("%Y-%m-%d %H:%M:%S"), "providers": {}, "routes": {}}
    routes = {}
    for name, v in list(config.MODELS.items()) + list(config.JUDGES.items()):
        routes[f"{v['provider']}|{v['model_id']}"] = (name, v)
    for v in config.STIM_CHAIN + config.PROBE_EXTRA:
        routes.setdefault(f"{v['provider']}|{v['model_id']}", ("candidate", v))
    for P in sorted({v["provider"] for _, v in routes.values()}):
        if config.PROVIDERS[P]["kind"] == "mock" or not providers.has_key(P):
            print(f"{P}: no key set, skipped")
            continue
        try:
            ids = providers.list_models(P)
            out["providers"][P] = {"n_models": len(ids), "model_ids": sorted(ids)}
        except Exception as e:
            ids = []
            out["providers"][P] = {"list_error": str(e)[:200]}
        for rid, (name, v) in routes.items():
            if v["provider"] != P:
                continue
            res = {"role": name, "listed": v["model_id"] in ids}
            for attempt in range(4):
                time.sleep(3)  # free tiers allow ~1 request/second
                try:
                    c = providers.call(P, {**v, "reasoning_effort": v.get("reasoning_effort")}, "Reply with the single word OK.", 256)
                    res.update(ok=True, text=c["text"][:40], finish=c["finish_reason"], usage=[c["prompt_tokens"], c["completion_tokens"]],
                               ratelimit_headers=c["headers"])
                    break
                except QuotaBlocked as e:
                    res.update(ok=False, error="rate limited: " + e.reason[:150])
                    time.sleep(10)
                except Exception as e:
                    res.update(ok=False, error=f"{type(e).__name__}: {str(e)[:200]}")
                    if isinstance(e, Transient):
                        time.sleep(30 * (attempt + 1))
                    else:
                        break
            out["routes"][rid] = res
            print(rid, json.dumps({k: res[k] for k in res if k != "ratelimit_headers"}))
    path = f"config/evidence/probe_{time.strftime('%Y%m%d_%H%M')}.json"
    json.dump(out, open(path, "w"), indent=1)
    print("saved", path)


# ---------------------------------------------------------------- export / hash
def cmd_export(args):
    conn = connect()
    S = stim_by_id()
    rows = conn.execute("SELECT * FROM jobs").fetchall()
    free = {(r["model"], r["structure"], r["framing"]): r for r in rows if r["kind"] == "free"}
    probe = {(r["model"], r["structure"], r["framing"]): r for r in rows if r["kind"] == "probe"}
    judge = collections.defaultdict(dict)
    for r in rows:
        if r["kind"] == "judge" and r["status"] == "done":
            judge[(r["model"], r["structure"], r["framing"])][r["judge"]] = json.loads(r["response"])
    os.makedirs("results", exist_ok=True)
    out = []
    for (m, sid, f), fr in sorted(free.items()):
        s = S[sid]
        if args.split != "all" and s["split"] != args.split:
            continue
        d = dict(model=m, structure=sid, domain=s["domain"], split=s["split"], framing=f, lean_k=s["lean_k"],
                 free_status=fr["status"], free_truncated=fr["truncated"], free_finish=fr["finish_reason"],
                 free_completion_tokens=fr["completion_tokens"], free_reasoning_tokens=fr["reasoning_tokens"],
                 free_text=fr["response"], provider=fr["provider"], model_id=fr["model_id"])
        pr = probe.get((m, sid, f))
        if pr and pr["status"] == "done":
            p = json.loads(pr["response"])
            probs = [0.0, 0.0, 0.0]
            for j, idx in enumerate(s["probe_order"]):  # map displayed A/B/C back to canonical I1..I3
                probs[idx] = float(p["p_" + "ABC"[j]])
            tot = sum(probs) or 1
            probs = [x / tot for x in probs]
            d.update(p_I1=probs[0], p_I2=probs[1], p_I3=probs[2], probe_max=max(probs),
                     probe_closure=int(max(probs) >= config.PROBE_CLOSURE_THRESHOLD), p_lean=probs[s["lean_k"] - 1],
                     probe_truncated=pr["truncated"])
        jd = judge.get((m, sid, f), {})
        for j in list(config.JUDGES) + config.CROSSCHECK_JUDGES:
            o = jd.get(j)
            d[f"{j}_label"] = o["label"] if o else None
            d[f"{j}_dominant"] = o["dominant"] if o else None
            d[f"{j}_coverage"] = o["coverage"] if o else None
            d[f"{j}_refusal"] = o.get("refusal_flag") if o else None
            d[f"{j}_dom_is_lean"] = (int(o["dominant"] == f"I{s['lean_k']}") if o else None)
        labs = [d[f"{j}_label"] for j in config.JUDGES]
        d["closure_both"] = int(all(l == "CLOSURE" for l in labs)) if all(labs) else None
        d["closure_either"] = int(any(l == "CLOSURE" for l in labs)) if all(labs) else None
        out.append(d)
    if not out:
        print("nothing to export")
        return
    path = f"results/data_{args.split}.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=sorted({k for r in out for k in r}, key=lambda k: list(out[0]).index(k) if k in out[0] else 999))
        w.writeheader()
        w.writerows(out)
    print(f"wrote {path}: {len(out)} rows")


def cmd_hash(args):
    for f in ("prompts/templates.py", "config.py"):
        print(hashlib.sha256(open(f, "rb").read()).hexdigest(), f)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("init"); sp.add_argument("--split", default="all", choices=["all", "dry", "main"])
    sub.add_parser("probe")
    sp = sub.add_parser("run")
    sp.add_argument("--kinds", nargs="+", default=["free", "probe", "judge"], choices=["free", "probe", "judge"])
    sp.add_argument("--models", nargs="*", default=None)
    sp.add_argument("--split", default="all", choices=["all", "dry", "main"])
    sp.add_argument("--once", action="store_true", help="exit instead of sleeping through long (daily) blocks")
    sp.add_argument("--limit", type=int, default=0)
    sub.add_parser("status")
    sp = sub.add_parser("export"); sp.add_argument("--split", default="main", choices=["all", "dry", "main"])
    sub.add_parser("hash")
    a = ap.parse_args()
    if a.cmd in ("run", "export", "init"):
        STIM = stim_by_id() if os.path.exists(config.STIMULI_PATH) else {}
    sys.exit({"init": cmd_init, "probe": cmd_probe, "run": cmd_run, "status": cmd_status, "export": cmd_export, "hash": cmd_hash}[a.cmd](a) or 0)
