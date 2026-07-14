"""Startup validation for the LLM configuration.

The chat model is a single ``provider:model`` string (``settings.MODEL``), so validation is simply:
build it once — if that fails (unknown provider, missing API key), fail fast with a clear message.
Long-term memory is an optional capability that needs an embeddings provider (OpenAI/Azure, since
Anthropic has no embedding model); when none resolves, memory degrades with a warning rather than
raising.

Call ``validate_llm_config()`` once at application startup (FastAPI lifespan).
"""

from src.app.core.common.config import settings
from src.app.core.common.logging import logger
from src.app.core.llm.factory import LLMConfigError, api_key_for, create_chat_model, provider_of

_OPENAI = "openai"
_AZURE = "azure"
_NONE = "none"


def resolve_embeddings_provider() -> str:
    """Resolve which provider powers embeddings/long-term memory: ``openai`` | ``azure`` | ``none``.

    Reads the provider from ``EMBEDDINGS_MODEL``'s prefix when set; otherwise auto-picks whichever of
    OpenAI/Azure has a key. Anthropic is never an embeddings provider (it has no embedding model), so
    an Anthropic-only deployment resolves to ``none`` and long-term memory degrades gracefully.
    """
    spec = settings.EMBEDDINGS_MODEL.strip().lower()
    if spec.startswith(("azure_openai:", "azure:")):
        return _AZURE
    if spec.startswith("openai:"):
        return _OPENAI
    if spec:
        logger.warning("embeddings_model_provider_unrecognized", value=spec)
    if settings.OPENAI_API_KEY:
        return _OPENAI
    if settings.AZURE_OPENAI_API_KEY and settings.AZURE_OPENAI_ENDPOINT:
        return _AZURE
    return _NONE


def embeddings_model_name(provider: str) -> str:
    """The embedder model id for the resolved provider.

    Uses the part after ``:`` in ``EMBEDDINGS_MODEL`` when set; else a sensible per-provider default
    (OpenAI: ``text-embedding-3-small``; Azure: the ``AZURE_OPENAI_EMBEDDING_DEPLOYMENT`` name).
    """
    spec = settings.EMBEDDINGS_MODEL.strip()
    if ":" in spec:
        return spec.split(":", 1)[1].strip()
    if provider == _AZURE:
        return settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT
    return "text-embedding-3-small"


def validate_llm_config() -> None:
    """Build ``settings.MODEL`` once and log the resolved config; raise ``LLMConfigError`` on failure.

    Fails fast (clear message naming ``MODEL``) when the chat model can't be constructed — a missing
    key or unknown provider. Warns (does not raise) when long-term memory will be disabled for lack of
    an embeddings provider.
    """
    provider = provider_of(settings.MODEL)
    if not provider:
        raise LLMConfigError(
            f"MODEL={settings.MODEL!r} has no provider prefix. Use 'provider:model', e.g. "
            "'anthropic:claude-sonnet-5', 'openai:gpt-4o', or 'azure_openai:<deployment>'."
        )
    if not api_key_for(settings.MODEL):
        raise LLMConfigError(
            f"MODEL={settings.MODEL!r} needs its provider's API key. Set the key for '{provider}' "
            "(ANTHROPIC_API_KEY / OPENAI_API_KEY / AZURE_OPENAI_API_KEY) in your .env."
        )
    try:
        create_chat_model()
    except Exception as exc:
        raise LLMConfigError(f"Could not build MODEL={settings.MODEL!r}: {exc}.") from exc

    embeddings = resolve_embeddings_provider()
    memory_enabled = settings.LONG_TERM_MEMORY_ENABLED and embeddings != _NONE
    if settings.LONG_TERM_MEMORY_ENABLED and embeddings == _NONE:
        logger.warning(
            "long_term_memory_disabled_no_embeddings",
            reason="no openai/azure embeddings key resolved",
            hint="set EMBEDDINGS_MODEL (or an OpenAI/Azure key), or LONG_TERM_MEMORY_ENABLED=false",
        )

    logger.info(
        "llm_config_validated",
        chat_model=settings.MODEL,
        utility_model=settings.UTILITY_MODEL or settings.MODEL,
        embeddings_provider=embeddings,
        long_term_memory_enabled=memory_enabled,
    )
