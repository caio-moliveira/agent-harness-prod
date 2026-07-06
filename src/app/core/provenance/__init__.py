"""Provenance: reusable source-attribution for every claim the agent produces.

A ``Source`` links an answer back to where it came from — a SQL query (table(s) + statement +
extraction time) or, later, a document chunk (#14). Kept framework-neutral so the data layer,
retrieval layer, and artifact renderer (#18) all attach the same shape.
"""

from src.app.core.provenance.source import Source

__all__ = ["Source"]
