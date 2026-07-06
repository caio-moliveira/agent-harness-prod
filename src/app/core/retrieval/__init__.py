"""Semantic retrieval over ingested document chunks.

Public surface: ``index_chunks`` (embed a corpus), ``retrieve`` (search it), ``make_retrieval_tools``
(the agent-facing tool), and the ``Embedder`` abstraction with its OpenAI default.
"""

from src.app.core.retrieval.embedding import Embedder, OpenAIEmbedder, get_default_embedder
from src.app.core.retrieval.indexing import index_chunks
from src.app.core.retrieval.retriever import RetrievedChunk, retrieve
from src.app.core.retrieval.tool import make_retrieval_tools

__all__ = [
    "Embedder",
    "OpenAIEmbedder",
    "get_default_embedder",
    "index_chunks",
    "retrieve",
    "RetrievedChunk",
    "make_retrieval_tools",
]
