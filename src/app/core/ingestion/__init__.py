"""Document ingestion: parse a folder's files into the per-(user, agent) manifest.

Public surface: ``sync_folder`` (incremental orchestrator), ``ingest_file`` (parse one file into its
manifest fields), ``extract_document`` (parsers), ``IngestedFileRepository`` (the manifest). The
corpus is vectorless — each file's structure tree + located text live on the manifest; there are no
chunks or embeddings.
"""

from src.app.core.ingestion.ingest import IngestFileResult, ingest_file
from src.app.core.ingestion.parsers import ParsedDocument, extract_document, is_supported
from src.app.core.ingestion.source_repository import IngestedFileRepository
from src.app.core.ingestion.sync import SyncResult, sync_folder

__all__ = [
    "IngestFileResult",
    "ingest_file",
    "ParsedDocument",
    "extract_document",
    "is_supported",
    "IngestedFileRepository",
    "SyncResult",
    "sync_folder",
]
