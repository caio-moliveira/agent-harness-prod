"""Local, vectorless document structure: per-file trees the agent navigates instead of vector RAG.

``build_document_tree`` turns a ``ParsedDocument`` into a hierarchy of located sections (the
PageIndex idea, run in-process with our own model). Built at ingest, stored on ``IngestedFile`` and
navigated by the document tools.
"""

from src.app.core.structure.builder import build_document_tree
from src.app.core.structure.models import DocumentTree, TreeNode

__all__ = ["build_document_tree", "DocumentTree", "TreeNode"]
