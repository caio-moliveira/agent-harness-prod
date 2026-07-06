"""The ``Source`` provenance model — where a claim came from.

Two kinds today/soon:
  - ``query``: a SQL result — the table(s) touched, the exact statement, and when it ran.
  - ``doc_chunk``: a retrieved document excerpt — document, section, excerpt (wired in #14).

Provenance is metadata attached to answers/artifacts, not a persisted table of its own, so this
is a plain Pydantic model reused across the data, retrieval, and rendering layers.
"""

from datetime import UTC, datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class Source(BaseModel):
    """Attribution for one piece of produced content."""

    kind: Literal["query", "doc_chunk"]

    # Fields for the query kind.
    tables: List[str] = Field(default_factory=list)
    query: Optional[str] = None
    extracted_at: Optional[datetime] = None

    # Fields for the doc_chunk kind (populated by #14).
    document: Optional[str] = None
    section: Optional[str] = None
    excerpt: Optional[str] = None

    @classmethod
    def from_query(cls, sql: str, tables: List[str], extracted_at: Optional[datetime] = None) -> "Source":
        """Build query provenance, stamping the extraction time (now, UTC) if not given."""
        return cls(
            kind="query",
            tables=list(tables),
            query=sql,
            extracted_at=extracted_at or datetime.now(UTC),
        )

    def render(self) -> str:
        """Render a compact, single-line attribution suitable for an LLM/tool output footer."""
        if self.kind == "query":
            tables = ", ".join(self.tables) or "(desconhecida)"
            stamp = self.extracted_at.isoformat() if self.extracted_at else "(sem data)"
            return f"tabela(s): {tables} | consulta: {self.query} | extraído em: {stamp}"
        # doc_chunk
        loc = f" ({self.section})" if self.section else ""
        return f"documento: {self.document}{loc}"
