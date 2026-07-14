"""Chat-model factory: one thin place that builds the agents' LLMs from a ``provider:model`` string.

Everything routes through LangChain's :func:`init_chat_model`, which infers the provider from the
``MODEL`` prefix (``anthropic:``/``openai:``/``azure_openai:``) and reads the provider's API key from
the environment. The factory only owns the few cross-provider quirks so the agents never have to:

- **Anthropic** requires an explicit ``max_tokens`` (and rejects ``temperature`` — Sonnet 400s on it),
  so the factory always forwards ``MODEL_MAX_TOKENS`` and never sends ``temperature`` to Anthropic.
- **Azure** needs ``azure_endpoint`` + ``api_version`` in addition to the key (our env-var names differ
  from what ``init_chat_model`` auto-reads), so the factory threads them from ``AZURE_OPENAI_*``.

Prompt caching for Anthropic is bundled automatically by ``create_deep_agent``
(``AnthropicPromptCachingMiddleware``), so there is no caching helper here. Long-term memory (mem0)
and the evals framework build their own models — this factory only builds the agents' chat models.
"""

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel

from src.app.core.common.config import settings

_ANTHROPIC_PREFIX = "anthropic"
_AZURE_PREFIXES = ("azure_openai", "azure")

# Provider prefix (the part before ":" in MODEL) → the settings attr holding its API key. The single
# source of truth for "which key does this model need", used by both the builders and startup validation.
_PROVIDER_KEY_ATTR = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "azure_openai": "AZURE_OPENAI_API_KEY",
    "azure": "AZURE_OPENAI_API_KEY",
}


class LLMConfigError(RuntimeError):
    """Raised when ``MODEL`` is unbuildable — unknown provider or a missing provider API key."""


def provider_of(spec: str) -> str:
    """The provider prefix of a ``provider:model`` spec (``""`` when there is no prefix)."""
    return spec.split(":", 1)[0].strip().lower() if ":" in spec else ""


def api_key_for(spec: str) -> str:
    """The configured API key for a spec's provider, read from ``settings`` (``""`` when unset)."""
    attr = _PROVIDER_KEY_ATTR.get(provider_of(spec))
    return getattr(settings, attr, "") if attr else ""


def _model_id(spec: str) -> str:
    """The bare model id / Azure deployment name (the part after ``provider:``)."""
    return spec.split(":", 1)[1].strip() if ":" in spec else spec.strip()


# OpenAI/Azure reasoning-model families (gpt-5.x, o1/o3/o4). Detected by name/deployment prefix.
_REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def _is_openai_reasoning_model(spec: str) -> bool:
    """True for an OpenAI/Azure reasoning model (gpt-5.x / o-series) — needs special tool handling."""
    if spec.startswith(_ANTHROPIC_PREFIX):
        return False
    return _model_id(spec).lower().startswith(_REASONING_PREFIXES)


def _build_kwargs(spec: str, max_tokens: int | None, temperature: float | None) -> dict:
    """Build the ``init_chat_model`` kwargs for a ``provider:model`` spec, applying provider quirks."""
    # Pass the key from settings; None (not "") lets init_chat_model fall back to its own env lookup.
    kwargs: dict = {"api_key": api_key_for(spec) or None}
    # Anthropic requires an explicit output cap (and init_chat_model would otherwise default it low,
    # truncating deliverables). Always forward one; harmless on OpenAI/Azure.
    kwargs["max_tokens"] = max_tokens or settings.MODEL_MAX_TOKENS
    # Sonnet rejects non-default sampling params — only forward temperature to non-Anthropic providers.
    if temperature is not None and not spec.startswith(_ANTHROPIC_PREFIX):
        kwargs["temperature"] = temperature
    # OpenAI/Azure reasoning models (gpt-5.x, o-series) reject function tools on /v1/chat/completions
    # unless reasoning is off ("Function tools with reasoning_effort are not supported ... set
    # reasoning_effort to 'none'"). The deep agent ALWAYS binds tools, so force it off — this is also
    # the right call for a tool-heavy agent (reasoning-in-the-loop bloats tokens and re-planning).
    if _is_openai_reasoning_model(spec):
        kwargs["reasoning_effort"] = "none"
    # Azure needs endpoint + version; our env-var names differ from what init_chat_model auto-reads.
    if spec.startswith(_AZURE_PREFIXES):
        kwargs["azure_endpoint"] = settings.AZURE_OPENAI_ENDPOINT
        kwargs["api_version"] = settings.AZURE_OPENAI_API_VERSION
    return kwargs


def create_chat_model(
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> BaseChatModel:
    """Build the agents' chat model from ``settings.MODEL`` (or an explicit ``provider:model`` override).

    ``temperature`` is honored on OpenAI/Azure only (dropped on Anthropic, which rejects it);
    ``max_tokens`` defaults to ``settings.MODEL_MAX_TOKENS``.
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
