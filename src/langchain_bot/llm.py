"""Pick the chat model and embeddings from whatever the user has configured.

.env settings (see .env.example):
  LLM_PROVIDER=auto        auto | openai | groq | gemini | ollama
  LLM_MODEL=               optional model override
  OPENAI_API_KEY / GROQ_API_KEY / GOOGLE_API_KEY / OLLAMA_BASE_URL
  EMBEDDINGS_PROVIDER=auto auto | openai | gemini | local

LLM_PROVIDER=auto uses the FIRST available, in this order:
  OPENAI_API_KEY -> GROQ_API_KEY -> GOOGLE_API_KEY -> Ollama (if OLLAMA_BASE_URL responds).
EMBEDDINGS_PROVIDER=auto uses OpenAI if OPENAI_API_KEY is set, else local FastEmbed (no key).
Gemini embeddings are only used when chosen explicitly: the free tier may use data for training.
"""

import os
import urllib.request

from dotenv import load_dotenv
from langchain.agents.middleware import ModelRetryMiddleware

load_dotenv()

AUTO_ORDER = ("openai", "groq", "gemini", "ollama")

DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    # Groq's free plan serves gpt-oss / qwen models; llama-3.3-70b-versatile is not available on it
    "groq": "openai/gpt-oss-120b",
    "gemini": "gemini-flash-latest",  # Google's alias for the newest Flash model
    "ollama": "qwen2.5:7b",
}

# init_chat_model provider names
CHAT_PROVIDERS = {"openai": "openai", "groq": "groq", "gemini": "google_genai", "ollama": "ollama"}

KEY_ENV = {"openai": "OPENAI_API_KEY", "groq": "GROQ_API_KEY", "gemini": "GOOGLE_API_KEY"}

EMBEDDING_MODELS = {
    "openai": "text-embedding-3-small",
    "gemini": "gemini-embedding-001",
    "local": "BAAI/bge-small-en-v1.5",
}

DEFAULT_OLLAMA_URL = "http://localhost:11434"
TEMPERATURE = 0.2

# 429 handling: Groq's and Gemini's free tiers are tight. Delays: 2 s, 4 s, 8 s, 16 s (+ jitter).
RETRY_MAX = 4
RETRY_INITIAL_DELAY = 2.0

SETUP_HELP = """No LLM provider is configured. Put ANY ONE of these in your .env file:

  Free:
    GROQ_API_KEY=...     free key from https://console.groq.com/keys
    GOOGLE_API_KEY=...   free key from https://aistudio.google.com/apikey
                         (the free tier may use your data for training)
    Ollama, no key:      install from https://ollama.com/download, run `ollama pull qwen2.5:7b`
                         (OLLAMA_BASE_URL defaults to http://localhost:11434)
  Paid:
    OPENAI_API_KEY=...   from https://platform.openai.com/api-keys

Optionally set LLM_PROVIDER=openai|groq|gemini|ollama to pick one explicitly."""


class LLMConfigError(RuntimeError):
    """The requested model provider is not configured (missing key, unknown name, Ollama not running)."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _env(name):
    value = (os.getenv(name) or "").strip()
    # The placeholders in .env.example ("your_openai_api_key_here") do not count as keys
    if value.lower().startswith("your_") and value.lower().endswith("_here"):
        return ""
    return value


def _has_key(provider):
    return bool(_env(KEY_ENV[provider]))


def ollama_base_url():
    return _env("OLLAMA_BASE_URL") or DEFAULT_OLLAMA_URL


def _ollama_available(timeout=1.5):
    """True if an Ollama server answers at OLLAMA_BASE_URL."""
    try:
        with urllib.request.urlopen(f"{ollama_base_url().rstrip('/')}/api/tags", timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def _provider_available(provider):
    return _ollama_available() if provider == "ollama" else _has_key(provider)


def resolve_provider():
    """The chat provider to use: LLM_PROVIDER, or the first available one for 'auto'."""
    requested = (_env("LLM_PROVIDER") or "auto").lower()

    if requested == "auto":
        for provider in AUTO_ORDER:
            if _provider_available(provider):
                return provider
        raise LLMConfigError(SETUP_HELP)

    if requested not in CHAT_PROVIDERS:
        raise LLMConfigError(f"Unknown LLM_PROVIDER '{requested}'. Use auto, openai, groq, gemini or ollama.")

    if requested == "ollama":
        if not _ollama_available():
            raise LLMConfigError(
                f"LLM_PROVIDER=ollama, but no Ollama server answers at {ollama_base_url()}. "
                "Start Ollama (https://ollama.com/download) and run `ollama pull qwen2.5:7b`, "
                "or set OLLAMA_BASE_URL."
            )
    elif not _has_key(requested):
        env_name = KEY_ENV[requested]
        where = {
            "openai": "https://platform.openai.com/api-keys",
            "groq": "https://console.groq.com/keys (free)",
            "gemini": "https://aistudio.google.com/apikey (free tier)",
        }[requested]
        raise LLMConfigError(f"LLM_PROVIDER={requested}, but {env_name} is not set. Get a key at {where} "
                             f"and add {env_name}=... to .env, or set LLM_PROVIDER=auto.")
    return requested


def resolve_model(provider):
    return _env("LLM_MODEL") or DEFAULT_MODELS[provider]


def describe_provider():
    """'groq / llama-3.3-70b-versatile' for the UI and logs ('not configured' if none is available)."""
    try:
        provider = resolve_provider()
    except LLMConfigError:
        return "not configured"
    return f"{provider} / {resolve_model(provider)}"


# ---------------------------------------------------------------------------
# Chat model
# ---------------------------------------------------------------------------

def get_chat_model():
    """The chat model for the configured provider (raises LLMConfigError if none is usable)."""
    from langchain.chat_models import init_chat_model

    provider = resolve_provider()
    kwargs = {"temperature": TEMPERATURE}
    if provider == "ollama":
        kwargs["base_url"] = ollama_base_url()
    else:
        kwargs["max_retries"] = 1  # the agent's retry middleware does the backoff on 429
    return init_chat_model(resolve_model(provider), model_provider=CHAT_PROVIDERS[provider], **kwargs)


def is_rate_limit_error(error):
    """429 / quota errors from OpenAI, Groq, Gemini (and anything else that says so)."""
    status = getattr(error, "status_code", None) or getattr(error, "code", None)
    if status == 429:
        return True
    response = getattr(error, "response", None)
    if getattr(response, "status_code", None) == 429:
        return True
    name = type(error).__name__.lower()
    text = str(error).lower()
    return ("ratelimit" in name or "resourceexhausted" in name or "429" in text
            or "rate limit" in text or "rate_limit" in text or "resource_exhausted" in text)


def get_retry_middleware():
    """Retry model calls that hit a rate limit, with exponential backoff. Other errors are raised at once."""
    return ModelRetryMiddleware(
        max_retries=RETRY_MAX,
        retry_on=is_rate_limit_error,
        on_failure="error",
        backoff_factor=2.0,
        initial_delay=RETRY_INITIAL_DELAY,
        max_delay=30.0,
        jitter=True,
    )


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------

def resolve_embeddings_provider():
    requested = (_env("EMBEDDINGS_PROVIDER") or "auto").lower()
    if requested == "auto":
        return "openai" if _has_key("openai") else "local"
    if requested not in EMBEDDING_MODELS:
        raise LLMConfigError(f"Unknown EMBEDDINGS_PROVIDER '{requested}'. Use auto, openai, gemini or local.")
    if requested in KEY_ENV and not _has_key(requested):
        raise LLMConfigError(f"EMBEDDINGS_PROVIDER={requested}, but {KEY_ENV[requested]} is not set. "
                             "Use EMBEDDINGS_PROVIDER=local for free embeddings without a key.")
    return requested


def describe_embeddings():
    """{'provider': ..., 'model': ...}: saved next to the vector index so a change triggers a rebuild."""
    provider = resolve_embeddings_provider()
    return {"provider": provider, "model": EMBEDDING_MODELS[provider]}


def get_embeddings():
    provider = resolve_embeddings_provider()
    model = EMBEDDING_MODELS[provider]
    if provider == "openai":
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(model=model)
    if provider == "gemini":
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        return GoogleGenerativeAIEmbeddings(model=model)
    from langchain_community.embeddings import FastEmbedEmbeddings  # local, no key; downloads ~70 MB once

    return FastEmbedEmbeddings(model_name=model)
