"""Startup validation for the LLM configuration.

The chat model is a single ``provider:model`` string (``settings.MODEL``), so validation is simply:
build it once — if that fails (unknown provider, missing API key), fail fast with a clear message.
Key-less providers (Ollama) and OpenAI-compatible servers (``OPENAI_BASE_URL``) skip the API-key
check; providers outside the factory's key map get a hint and are validated by the build itself.
Long-term memory is an optional capability that needs an embeddings provider (OpenAI/Azure/Ollama,
since Anthropic has no embedding model); when none resolves, memory degrades with a warning rather
than raising.

Call ``validate_llm_config()`` once at application startup (FastAPI lifespan).
"""

from src.app.core.common.config import settings
from src.app.core.common.logging import logger
from src.app.core.llm.factory import (
    LLMConfigError,
    api_key_for,
    create_chat_model,
    is_keyless_provider,
    is_known_provider,
    provider_of,
)

_OPENAI = "openai"
_AZURE = "azure"
_OLLAMA = "ollama"
_NONE = "none"

# Default embedding vector size per provider when EMBEDDINGS_DIMS is unset. OpenAI/Azure default to
# text-embedding-3-small's 1536; Ollama's default embedder (nomic-embed-text) produces 768.
_DEFAULT_EMBEDDINGS_DIMS = {_OLLAMA: 768}
_FALLBACK_EMBEDDINGS_DIMS = 1536


def _api_key_required(spec: str) -> bool:
    """Whether startup must insist on a configured API key before building ``spec``.

    Key-less providers (Ollama) never need one; the openai provider pointed at an OpenAI-compatible
    server via ``OPENAI_BASE_URL`` treats the key as optional (local servers accept any value); and
    unknown providers can't be pre-checked here — ``init_chat_model`` reads their standard env key
    itself and the build below fails fast when it's missing.
    """
    if is_keyless_provider(spec):
        return False
    if provider_of(spec) == _OPENAI and settings.OPENAI_BASE_URL:
        return False
    return is_known_provider(spec)


def resolve_embeddings_provider() -> str:
    """Resolve which provider powers embeddings/long-term memory: ``openai`` | ``azure`` | ``ollama`` | ``none``.

    Reads the provider from ``EMBEDDINGS_MODEL``'s prefix when set; otherwise auto-picks whichever of
    OpenAI/Azure has a key (Ollama is never auto-picked — a local server can't be assumed, so it must
    be requested explicitly, e.g. ``EMBEDDINGS_MODEL=ollama:nomic-embed-text``). Anthropic is never an
    embeddings provider (it has no embedding model), so an Anthropic-only deployment resolves to
    ``none`` and long-term memory degrades gracefully.
    """
    spec = settings.EMBEDDINGS_MODEL.strip().lower()
    if spec.startswith("ollama:"):
        return _OLLAMA
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
    (OpenAI: ``text-embedding-3-small``; Ollama: ``nomic-embed-text``; Azure: the
    ``AZURE_OPENAI_EMBEDDING_DEPLOYMENT`` name).
    """
    spec = settings.EMBEDDINGS_MODEL.strip()
    if ":" in spec and spec.split(":", 1)[1].strip():
        return spec.split(":", 1)[1].strip()
    if provider == _AZURE:
        return settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT
    if provider == _OLLAMA:
        return "nomic-embed-text"
    return "text-embedding-3-small"


def embeddings_dims(provider: str) -> int:
    """The embedding vector size for the resolved provider (pgvector's collection dimension).

    ``EMBEDDINGS_DIMS`` wins when set; else the provider default (1536 for OpenAI/Azure, 768 for
    Ollama's nomic-embed-text). Must match the embedder model actually configured.
    """
    return settings.EMBEDDINGS_DIMS or _DEFAULT_EMBEDDINGS_DIMS.get(provider, _FALLBACK_EMBEDDINGS_DIMS)


def validate_llm_config() -> None:
    """Build ``settings.MODEL`` once and log the resolved config; raise ``LLMConfigError`` on failure.

    Fails fast (clear message naming ``MODEL``) when the chat model can't be constructed — a missing
    key, unknown provider, or missing integration package. Warns (does not raise) when long-term
    memory will be disabled for lack of an embeddings provider.
    """
    provider = provider_of(settings.MODEL)
    if not provider:
        raise LLMConfigError(
            f"MODEL={settings.MODEL!r} has no provider prefix. Use 'provider:model', e.g. "
            "'anthropic:claude-sonnet-5', 'openai:gpt-4o', 'azure_openai:<deployment>', or "
            "'ollama:<model>' for open-weight models on a local Ollama server."
        )
    if _api_key_required(settings.MODEL) and not api_key_for(settings.MODEL):
        raise LLMConfigError(
            f"MODEL={settings.MODEL!r} needs its provider's API key. Set the key for '{provider}' "
            "(ANTHROPIC_API_KEY / OPENAI_API_KEY / AZURE_OPENAI_API_KEY) in your .env."
        )
    if not is_known_provider(settings.MODEL):
        # Not an error: init_chat_model dispatches to the provider's integration package (which must
        # be installed) and reads its standard env key (e.g. GROQ_API_KEY). The build below fails
        # fast with the provider's own message when either is missing.
        logger.info(
            "llm_provider_not_in_key_map",
            provider=provider,
            hint="ensure the provider's langchain integration package is installed and its env API key is set",
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
            hint=(
                "set EMBEDDINGS_MODEL (or an OpenAI/Azure key) — e.g. ollama:nomic-embed-text for a "
                "fully local setup — or LONG_TERM_MEMORY_ENABLED=false"
            ),
        )

    logger.info(
        "llm_config_validated",
        chat_model=settings.MODEL,
        utility_model=settings.UTILITY_MODEL or settings.MODEL,
        embeddings_provider=embeddings,
        long_term_memory_enabled=memory_enabled,
    )
