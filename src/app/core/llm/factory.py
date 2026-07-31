"""Chat-model factory: one thin place that builds the agents' LLMs from a ``provider:model`` string.

Everything routes through LangChain's :func:`init_chat_model`, which infers the provider from the
``MODEL`` prefix (``anthropic:``/``openai:``/``azure_openai:``/``ollama:``/…) and reads the provider's
API key from the environment. The factory only owns the few cross-provider quirks so the agents never
have to:

- **Anthropic** requires an explicit ``max_tokens`` (and rejects ``temperature`` — Sonnet 400s on it),
  so the factory always forwards ``MODEL_MAX_TOKENS`` and never sends ``temperature`` to Anthropic.
- **Azure** needs ``azure_endpoint`` + ``api_version`` in addition to the key (our env-var names differ
  from what ``init_chat_model`` auto-reads), so the factory threads them from ``AZURE_OPENAI_*``.
- **Ollama** (open-weight local models) needs no API key; its server address comes from
  ``OLLAMA_BASE_URL`` and its output cap is ``num_predict`` (``ChatOllama`` silently ignores
  ``max_tokens``), so the factory maps both.
- **OpenAI-compatible servers** (vLLM, LM Studio, a LiteLLM proxy, OpenRouter, …) are reached with the
  stock ``openai:`` provider by setting ``OPENAI_BASE_URL`` — the key becomes optional because most
  local servers accept any value.

Providers outside the key map below (``groq:``, ``google_genai:``, ``mistralai:``, …) still build:
``init_chat_model`` dispatches to the provider's integration package and reads its standard env key
itself. Startup validation lets those through with a hint instead of failing on the unknown prefix.

Prompt caching for Anthropic is bundled automatically by ``create_deep_agent``
(``AnthropicPromptCachingMiddleware``), so there is no caching helper here. Long-term memory (mem0)
and the evals framework build their own models — this factory only builds the agents' chat models.
"""

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel

from src.app.core.common.config import settings

_ANTHROPIC_PREFIX = "anthropic"
_AZURE_PREFIXES = ("azure_openai", "azure")
_OPENAI_PREFIX = "openai"
_OLLAMA_PREFIX = "ollama"

# Provider prefix (the part before ":" in MODEL) → the settings attr holding its API key. The single
# source of truth for "which key does this model need", used by both the builders and startup validation.
_PROVIDER_KEY_ATTR = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "azure_openai": "AZURE_OPENAI_API_KEY",
    "azure": "AZURE_OPENAI_API_KEY",
}

# Providers that authenticate without an API key (local / self-hosted runtimes).
_KEYLESS_PROVIDERS = frozenset({_OLLAMA_PREFIX})


class LLMConfigError(RuntimeError):
    """Raised when ``MODEL`` is unbuildable — unknown provider or a missing provider API key."""


def provider_of(spec: str) -> str:
    """The provider prefix of a ``provider:model`` spec (``""`` when there is no prefix)."""
    return spec.split(":", 1)[0].strip().lower() if ":" in spec else ""


def api_key_for(spec: str) -> str:
    """The configured API key for a spec's provider, read from ``settings`` (``""`` when unset)."""
    attr = _PROVIDER_KEY_ATTR.get(provider_of(spec))
    return getattr(settings, attr, "") if attr else ""


def is_keyless_provider(spec: str) -> bool:
    """True when the spec's provider authenticates without an API key (e.g. a local Ollama server)."""
    return provider_of(spec) in _KEYLESS_PROVIDERS


def is_known_provider(spec: str) -> bool:
    """True when the factory manages this provider's credentials/quirks itself.

    Unknown providers are still buildable — ``init_chat_model`` dispatches them to their LangChain
    integration package, which reads its own standard env key — but validation can only *hint* rather
    than pre-check their key.
    """
    provider = provider_of(spec)
    return provider in _PROVIDER_KEY_ATTR or provider in _KEYLESS_PROVIDERS


def _model_id(spec: str) -> str:
    """The bare model id / Azure deployment name (the part after ``provider:``)."""
    return spec.split(":", 1)[1].strip() if ":" in spec else spec.strip()


# OpenAI/Azure reasoning-model families (gpt-5.x, o1/o3/o4). Detected by name/deployment prefix.
_REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def _is_openai_reasoning_model(spec: str) -> bool:
    """True for an OpenAI/Azure reasoning model (gpt-5.x / o-series) — needs special tool handling."""
    if provider_of(spec) not in (_OPENAI_PREFIX, *_AZURE_PREFIXES):
        return False
    return _model_id(spec).lower().startswith(_REASONING_PREFIXES)


def _build_kwargs(spec: str, max_tokens: int | None, temperature: float | None) -> dict:
    """Build the ``init_chat_model`` kwargs for a ``provider:model`` spec, applying provider quirks."""
    tokens = max_tokens or settings.MODEL_MAX_TOKENS
    provider = provider_of(spec)
    # Ollama runs locally: no API key, the server address comes from OLLAMA_BASE_URL, and the output
    # cap is ``num_predict`` — ChatOllama accepts ``max_tokens`` but silently drops it, so map it.
    if provider == _OLLAMA_PREFIX:
        kwargs = {"base_url": settings.OLLAMA_BASE_URL, "num_predict": tokens}
        if temperature is not None:
            kwargs["temperature"] = temperature
        return kwargs
    # Pass the key from settings; None (not "") lets init_chat_model fall back to its own env lookup.
    kwargs = {"api_key": api_key_for(spec) or None}
    # Anthropic requires an explicit output cap (and init_chat_model would otherwise default it low,
    # truncating deliverables). Always forward one; harmless on OpenAI/Azure.
    kwargs["max_tokens"] = tokens
    # Sonnet rejects non-default sampling params — only forward temperature to non-Anthropic providers.
    if temperature is not None and provider != _ANTHROPIC_PREFIX:
        kwargs["temperature"] = temperature
    # OpenAI/Azure reasoning models (gpt-5.x, o-series) reject function tools on /v1/chat/completions
    # unless reasoning is off ("Function tools with reasoning_effort are not supported ... set
    # reasoning_effort to 'none'"). The deep agent ALWAYS binds tools, so force it off — this is also
    # the right call for a tool-heavy agent (reasoning-in-the-loop bloats tokens and re-planning).
    if _is_openai_reasoning_model(spec):
        kwargs["reasoning_effort"] = "none"
    # Azure needs endpoint + version; our env-var names differ from what init_chat_model auto-reads.
    if provider in _AZURE_PREFIXES:
        kwargs["azure_endpoint"] = settings.AZURE_OPENAI_ENDPOINT
        kwargs["api_version"] = settings.AZURE_OPENAI_API_VERSION
    # OpenAI-compatible servers (vLLM, LM Studio, LiteLLM proxy, OpenRouter, …): route the stock
    # openai provider at OPENAI_BASE_URL. Local servers accept any key, so default one when unset.
    if provider == _OPENAI_PREFIX and settings.OPENAI_BASE_URL:
        kwargs["base_url"] = settings.OPENAI_BASE_URL
        kwargs["api_key"] = kwargs["api_key"] or "not-needed"
    return kwargs


def create_chat_model(
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> BaseChatModel:
    """Build the agents' chat model from ``settings.MODEL`` (or an explicit ``provider:model`` override).

    ``temperature`` is honored on OpenAI/Azure/Ollama only (dropped on Anthropic, which rejects it);
    ``max_tokens`` defaults to ``settings.MODEL_MAX_TOKENS`` (mapped to ``num_predict`` on Ollama).
    """
    spec = model or settings.MODEL
    return init_chat_model(spec, **_build_kwargs(spec, max_tokens, temperature))


def create_utility_chat_model(
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> BaseChatModel:
    """Build the cheap model for low-stakes sub-flows (descriptions, safety check, research, mem0).

    Uses ``settings.UTILITY_MODEL`` when set, else falls back to ``MODEL``.
    """
    spec = settings.UTILITY_MODEL or settings.MODEL
    return init_chat_model(spec, **_build_kwargs(spec, max_tokens, temperature))


def active_model_name() -> str:
    """Return the configured ``provider:model`` string — for metrics, labels and traces."""
    return settings.MODEL
