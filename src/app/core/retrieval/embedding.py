"""Default embedder for the agent's experience-memory index (#23).

The experience memory embeds a short ``summary`` per entry and searches by cosine similarity. It needs
an embeddings model — the SAME separate provider as long-term memory (OpenAI/Azure, since Anthropic has
no embedding model), resolved from ``EMBEDDINGS_MODEL`` via :mod:`src.app.core.llm.validation`.

Built lazily on first use (``get_default_embedder``) and cached, so importing this module needs no
network or keys. When no embeddings provider resolves, it raises ``EmbeddingsUnavailable`` — callers in
``agent_memory_service`` are best-effort and degrade (a memory failure never breaks a turn).
"""

from typing import Optional

from langchain_openai import AzureOpenAIEmbeddings, OpenAIEmbeddings

from src.app.core.common.config import settings
from src.app.core.llm.validation import embeddings_model_name, resolve_embeddings_provider


class EmbeddingsUnavailable(RuntimeError):
    """Raised when no embeddings provider is configured (Anthropic-only / no OpenAI-Azure key)."""


class Embedder:
    """Thin async wrapper over a LangChain embeddings client (``embed_query`` → vector)."""

    def __init__(self, client) -> None:
        """Wrap a LangChain embeddings client that exposes ``aembed_query``."""
        self._client = client

    async def embed_query(self, text: str) -> list[float]:
        """Return the embedding vector for ``text``."""
        return await self._client.aembed_query(text)


_default: Optional[Embedder] = None


def _build_client():
    """Build the LangChain embeddings client for the resolved provider (openai or azure)."""
    provider = resolve_embeddings_provider()
    if provider == "none":
        raise EmbeddingsUnavailable(
            "No embeddings provider configured — set EMBEDDINGS_MODEL (or an OpenAI/Azure key)."
        )
    model = embeddings_model_name(provider)
    if provider == "azure":
        return AzureOpenAIEmbeddings(
            model=model,
            azure_deployment=settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT or model,
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
        )
    return OpenAIEmbeddings(model=model, api_key=settings.OPENAI_API_KEY)


def get_default_embedder() -> Embedder:
    """Return the process-wide default embedder, building it on first use.

    Raises ``EmbeddingsUnavailable`` when no embeddings provider is configured.
    """
    global _default
    if _default is None:
        _default = Embedder(_build_client())
    return _default
