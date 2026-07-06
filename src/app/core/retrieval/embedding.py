"""Embedding provider for document retrieval.

Wraps OpenAI embeddings behind a small ``Embedder`` protocol so the retrieval/indexing logic can
be tested with a deterministic fake (no network). Calls retry with exponential backoff (tenacity),
per the repo's conventions.
"""

from typing import List, Optional, Protocol, runtime_checkable

from langchain_openai import OpenAIEmbeddings
from tenacity import retry, stop_after_attempt, wait_exponential

from src.app.core.common.config import settings


@runtime_checkable
class Embedder(Protocol):
    """Anything that can turn texts into dense vectors."""

    async def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Embed a batch of documents."""
        ...

    async def embed_query(self, text: str) -> List[float]:
        """Embed a single query."""
        ...


class OpenAIEmbedder:
    """Default embedder backed by OpenAI (model from settings)."""

    def __init__(self, model: Optional[str] = None):
        """Build the embedder for the configured (or given) embedding model."""
        self._emb = OpenAIEmbeddings(
            model=model or settings.LONG_TERM_MEMORY_EMBEDDER_MODEL,
            api_key=settings.OPENAI_API_KEY,
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    async def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Embed a batch of documents, retrying transient failures."""
        return await self._emb.aembed_documents(texts)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    async def embed_query(self, text: str) -> List[float]:
        """Embed a single query, retrying transient failures."""
        return await self._emb.aembed_query(text)


def get_default_embedder() -> Embedder:
    """Return the default (OpenAI) embedder."""
    return OpenAIEmbedder()
