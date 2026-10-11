"""Central config. Edit model ids after running `python run.py probe`.

Everything the study depends on lives here so it can be hashed and preregistered.
"""
import os

# kind "openai" = OpenAI-compatible chat/completions; kind "gemini" = Google AI Studio REST.
# min_interval: seconds between calls (keeps under RPM). tpm: tokens/min budget (sliding window).
# tpd_budget: tokens per rolling 24h we allow ourselves (set ~10% under the real limit).
PROVIDERS = {
    "cerebras": {"kind": "openai", "base_url": "https://api.cerebras.ai/v1", "key_env": "CEREBRAS_API_KEY",
                 "max_param": "max_completion_tokens", "min_interval": 13.0, "tpm": 80000, "tpd_budget": 900000},
    "groq":     {"kind": "openai", "base_url": "https://api.groq.com/openai/v1", "key_env": "GROQ_API_KEY",
                 "max_param": "max_completion_tokens", "min_interval": 2.5, "tpm": 7000, "tpd_budget": 180000},
    "mistral":  {"kind": "openai", "base_url": "https://api.mistral.ai/v1", "key_env": "MISTRAL_API_KEY",
                 "max_param": "max_tokens", "min_interval": 1.2, "tpm": 100000, "tpd_budget": 10**9},
    # Gemini free-tier numbers: read them in AI Studio and edit here (placeholders are conservative).
    "gemini":   {"kind": "gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta", "key_env": "GEMINI_API_KEY",
                 "min_interval": 5.0, "tpm": 200000, "tpd_budget": 10**9},
    "mock":     {"kind": "mock", "min_interval": 0.0, "tpm": 10**9, "tpd_budget": 10**9},
}

# Evaluated models. `reasoning` only affects the max_tokens cap used.
# IMPORTANT: confirm the exact ids with `python run.py probe` (lists each provider's models).
MODELS = {
    "gemini":  {"provider": "gemini",   "model_id": "gemini-3.5-flash-lite", "family": "gemini", "reasoning": False},
    # Groq free tier lists limits per model (8K TPM, 200K tokens/day each), so each model is its own "lane".
    "gpt120":  {"provider": "groq",     "model_id": "openai/gpt-oss-120b",   "family": "openai", "reasoning": True, "reasoning_effort": "low", "tpd_budget": 180000},
    "qwen27":  {"provider": "groq",     "model_id": "qwen/qwen3.8-27b",      "family": "qwen",   "reasoning": True, "tpd_budget": 180000},
    "gpt20":   {"provider": "groq",     "model_id": "openai/gpt-oss-20b",    "family": "openai", "reasoning": True, "reasoning_effort": "low", "tpd_budget": 180000},
}

# Two judges, identical for every response. Neither should be an evaluated family if avoidable.
JUDGES = {
    "judgeA": {"provider": "mistral", "model_id": "mistral-small-latest", "family": "mistral", "reasoning": False},
    "judgeB": {"provider": "gemini",  "model_id": "gemini-3.8-flash",     "family": "gemini",  "reasoning": False},  # see sensitivity note in design
}

# Stimulus generator: must NOT be an evaluated model.
STIM_GENERATOR = {"provider": "mistral", "model_id": "mistral-small-latest", "family": "mistral", "reasoning": False}

# Stimulus generators tried in order (first that works). None of these is an evaluated model.
STIM_CHAIN = [
    {"provider": "mistral", "model_id": "mistral-small-latest", "family": "mistral", "reasoning": False},
    {"provider": "gemini",  "model_id": "gemma-4-31b-it",        "family": "gemma",   "reasoning": False},
    {"provider": "gemini",  "model_id": "gemini-3.8-flash",      "family": "gemini",  "reasoning": False},
]
# Extra candidate routes to test in `probe` (possible judges).
PROBE_EXTRA = [
    {"provider": "gemini", "model_id": "gemma-4-31b-it",      "family": "gemma"},
    {"provider": "gemini", "model_id": "gemma-4-26b-a4b-it",  "family": "gemma"},
]

DOMAINS = ["career", "interpersonal", "identity"]
FRAMINGS = ["neutral", "mild", "emphatic"]
TEMPERATURE = 0.0
PROBE_CLOSURE_THRESHOLD = 0.70


def max_tokens(kind, route):
    """Token caps per call kind. One retry at 2x on finish_reason=length."""
    r = route.get("reasoning", False)
    if kind == "free":
        return 3500 if r else 1500
    if kind == "probe":
        return 2500 if r else 800
    if kind == "judge":
        return 2048
    return 1500


DB_PATH = os.environ.get("HARNESS_DB", "jobs.db")
STIMULI_PATH = os.environ.get("HARNESS_STIMULI", "stimuli/stimuli_final.jsonl")

# Test mode: route everything to the offline mock provider.
if os.environ.get("HARNESS_MOCK"):
    for _d in (MODELS, JUDGES):
        for _v in _d.values():
            _v["provider"] = "mock"
    STIM_GENERATOR["provider"] = "mock"
    for _v in STIM_CHAIN + PROBE_EXTRA:
        _v["provider"] = "mock"
