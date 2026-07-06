"""Document ingestion: parse folder documents into metadata-rich, per-(user, agent) chunks.

Public surface: ``ingest_folder`` (orchestrator), ``DocumentChunkRepository`` (persistence),
``extract_document`` (parsers), ``chunk_document`` (chunking). Semantic indexing of the persisted
chunks is #14.
"""

from src.app.core.ingestion.chunk_repository import DocumentChunkRepository
from src.app.core.ingestion.chunking import ChunkData, chunk_document
from src.app.core.ingestion.ingest import IngestionResult, ingest_folder
from src.app.core.ingestion.parsers import ParsedDocument, extract_document, is_supported

__all__ = [
    "DocumentChunkRepository",
    "ChunkData",
    "chunk_document",
    "IngestionResult",
    "ingest_folder",
    "ParsedDocument",
    "extract_document",
    "is_supported",
]
