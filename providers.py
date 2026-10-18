"""Provider adapters. One function `call()` returns a normalised dict or raises a typed error."""
import datetime
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request

import config


class QuotaBlocked(Exception):
    """Provider is rate/quota limited until `until` (unix time)."""
    def __init__(self, until, reason):
        super().__init__(reason)
        self.until, self.reason = until, reason


class Transient(Exception):
    """Network / 5xx: retry later."""


class Fatal(Exception):
    """This request will never succeed (e.g. 400)."""


class Abort(Exception):
    """Config problem (missing key, unknown model, bad key): stop the run and tell the user."""


def _http(url, headers, body=None, method="POST", timeout=240):
    data = json.dumps(body).encode() if body is not None else None
    hdr = {"Content-Type": "application/json", "User-Agent": "closure-study/1.0", **headers}
    req = urllib.request.Request(url, data=data, headers=hdr, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        raise Transient(f"network: {e}")


def _pt_midnight():
    from zoneinfo import ZoneInfo
    now = datetime.datetime.now(ZoneInfo("America/Los_Angeles"))
    nxt = (now + datetime.timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
    return nxt.timestamp()


def _classify_429(provider, headers, body):
    low = body.lower()
    if "request too large" in low:
        # Groq answers 429 with this text when the per-minute bucket cannot take our requested tokens right now
        # (it cleared on retry in the dry run), so treat it as a short block, not a permanent failure.
        wait = min(float(headers.get("retry-after") or 60), 300)
        return QuotaBlocked(time.time() + max(wait, 60), "too large for current bucket: " + body[:400].replace("\n", " "))
    try:
        ra = float(headers.get("retry-after", "")) if headers.get("retry-after") else None
    except ValueError:
        ra = None
    daily = any(s in low for s in ("per day", "daily", "(tpd)", "(rpd)", "perday", "tokens per day", "requests per day"))
    if daily:
        until = _pt_midnight() if provider == "gemini" else time.time() + (ra if ra and ra > 0 else 3600)
        return QuotaBlocked(until, "daily quota: " + body[:160].replace("\n", " "))
    return QuotaBlocked(time.time() + min(ra if ra else 30, 300), "rate limit: " + body[:120].replace("\n", " "))


def _strip_think(text):
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def _key(prov):
    k = os.environ.get(prov["key_env"])
    if not k:
        raise Abort(f"environment variable {prov['key_env']} is not set")
    return k


def _ratelimit_headers(h):
    return {k: v for k, v in h.items() if k.startswith("x-ratelimit") or k == "retry-after"}


def _call_openai(name, prov, route, prompt, max_tokens, temperature):
    body = {"model": route["model_id"], "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature, prov.get("max_param", "max_tokens"): max_tokens}
    if route.get("reasoning_effort"):
        body["reasoning_effort"] = route["reasoning_effort"]
    status, h, txt = _http(prov["base_url"] + "/chat/completions", {"Authorization": "Bearer " + _key(prov)}, body)
    if status == 429:
        raise _classify_429(name, h, txt)
    if status in (401, 403):
        raise Abort(f"{name}: HTTP {status} (bad key or no access): {txt[:200]}")
    if status == 404:
        raise Abort(f"{name}: model '{route['model_id']}' not found: {txt[:200]}")
    if status >= 500:
        raise Transient(f"{name} HTTP {status}: {txt[:200]}")
    if status != 200:
        raise Fatal(f"{name} HTTP {status}: {txt[:300]}")
    j = json.loads(txt)
    ch = j["choices"][0]
    msg = ch.get("message", {})
    text = _strip_think(msg.get("content") or "")
    u = j.get("usage", {}) or {}
    det = u.get("completion_tokens_details") or {}
    return {"text": text, "finish_reason": ch.get("finish_reason"), "prompt_tokens": u.get("prompt_tokens"),
            "completion_tokens": u.get("completion_tokens"), "reasoning_tokens": det.get("reasoning_tokens"),
            "headers": _ratelimit_headers(h)}


def _call_gemini(name, prov, route, prompt, max_tokens, temperature):
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens}}
    url = f"{prov['base_url']}/models/{route['model_id']}:generateContent"
    status, h, txt = _http(url, {"x-goog-api-key": _key(prov)}, body, timeout=100)
    if status == 429:
        raise _classify_429(name, h, txt)
    if status in (401, 403):
        raise Abort(f"gemini: HTTP {status} (bad key or no access): {txt[:200]}")
    if status == 404:
        raise Abort(f"gemini: model '{route['model_id']}' not found: {txt[:200]}")
    if status >= 500:
        raise Transient(f"gemini HTTP {status}: {txt[:200]}")
    if status != 200:
        raise Fatal(f"gemini HTTP {status}: {txt[:300]}")
    j = json.loads(txt)
    cands = j.get("candidates") or []
    if not cands:  # blocked prompt etc.
        return {"text": "", "finish_reason": "blocked:" + str(j.get("promptFeedback", {}).get("blockReason")),
                "prompt_tokens": None, "completion_tokens": None, "reasoning_tokens": None, "headers": {}}
    c = cands[0]
    text = "".join(p.get("text", "") for p in (c.get("content", {}).get("parts") or []) if not p.get("thought"))
    fr = {"STOP": "stop", "MAX_TOKENS": "length"}.get(c.get("finishReason"), str(c.get("finishReason")).lower())
    u = j.get("usageMetadata", {})
    return {"text": _strip_think(text), "finish_reason": fr, "prompt_tokens": u.get("promptTokenCount"),
            "completion_tokens": u.get("candidatesTokenCount"), "reasoning_tokens": u.get("thoughtsTokenCount"),
            "headers": {}}


# ---- offline mock (tests only) -------------------------------------------------------------
_mock = {"n": 0}


def _h(*parts):
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest(), 16)


def _call_mock(route, prompt, max_tokens):
    _mock["n"] += 1
    lim = int(os.environ.get("MOCK_QUOTA_AFTER", "0"))
    if lim and _mock["n"] == lim + 1:
        raise QuotaBlocked(time.time() + 1.5, "mock daily quota")
    mid = route["model_id"]
    if prompt.startswith("You are scoring"):
        resp = prompt.split('"""')[1]
        label = "CLOSURE" if "It is clearly" in resp else ("PRESERVED" if "On the other hand" in resp else "LEANING")
        dom = "none"
        for k in (1, 2, 3):
            m = re.search(rf"^I{k}: (.*)$", prompt, flags=re.M)
            if m and m.group(1)[:25] in resp:
                dom = f"I{k}"
                break
        text = json.dumps({"coverage": 1 if label == "CLOSURE" else 3, "dominant": dom, "label": label,
                           "refusal_flag": False, "rationale": "mock"})
    elif "p_A" in prompt:
        a = (_h(mid, prompt) % 100) / 100.0
        lean = re.search(r"I'm (?:fairly sure|completely certain) (.*?)(?:\.|, so)", prompt)
        probs = [0.2, 0.3, 0.5] if not lean else [0.5, 0.3, 0.2]
        probs = [round(p * 0.9 + 0.1 * a / 3, 3) for p in probs]
        s = sum(probs)
        probs = [round(p / s, 3) for p in probs]
        text = json.dumps({"p_A": probs[0], "p_B": probs[1], "p_C": probs[2]})
    elif "Write one short first-person" in prompt:
        n = _h(prompt, _mock["n"])
        text = json.dumps({"scenario": f"Mock scenario {n}. A friend cancelled plans twice and posted about a party. I am unsure why.",
                           "readings": [f"reading one is true {n}", f"reading two is true {n}", f"reading three is true {n}"]})
    else:
        lean = re.search(r"I'm (?:fairly sure|completely certain) (.*?)(?:\.|, so)", prompt)
        if lean:
            text = f"It is clearly the case that {lean.group(1)}. You should act on that."
        elif _h(mid, prompt) % 2:
            text = "On the other hand it could be several things. It could be A, or B, or C. Ask them directly."
        else:
            text = "It is clearly a case of the first explanation. Act on that."
    return {"text": text, "finish_reason": "stop", "prompt_tokens": len(prompt) // 4, "completion_tokens": len(text) // 4,
            "reasoning_tokens": None, "headers": {}}


def call(provider, route, prompt, max_tokens, temperature=None):
    temperature = config.TEMPERATURE if temperature is None else temperature
    prov = config.PROVIDERS[provider]
    if prov["kind"] == "mock":
        return _call_mock(route, prompt, max_tokens)
    if prov["kind"] == "gemini":
        return _call_gemini(provider, prov, route, prompt, max_tokens, temperature)
    return _call_openai(provider, prov, route, prompt, max_tokens, temperature)


def list_models(provider):
    prov = config.PROVIDERS[provider]
    if prov["kind"] == "gemini":
        status, h, txt = _http(prov["base_url"] + "/models?pageSize=200", {"x-goog-api-key": _key(prov)}, method="GET")
        if status != 200:
            raise Fatal(f"{provider} list HTTP {status}: {txt[:200]}")
        return [m["name"].replace("models/", "") for m in json.loads(txt).get("models", [])]
    status, h, txt = _http(prov["base_url"] + "/models", {"Authorization": "Bearer " + _key(prov)}, method="GET")
    if status != 200:
        raise Fatal(f"{provider} list HTTP {status}: {txt[:200]}")
    return [m["id"] for m in json.loads(txt).get("data", [])]


def has_key(provider):
    prov = config.PROVIDERS[provider]
    return prov["kind"] == "mock" or bool(os.environ.get(prov.get("key_env", "")))
