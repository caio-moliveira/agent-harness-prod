"""Build a per-file document structure tree from a ``ParsedDocument`` — the local, vectorless index.

Dispatch by file extension: prose (pdf/docx/markdown) becomes a hierarchy of located sections;
tabular (xlsx/csv/tsv) becomes a schema (sheets/columns) that pairs with the DuckDB tools; plain
text is a single node. Only the PDF path spends an LLM call (the heading refiner); everything else
is free. The ``refiner`` is injected so tests can exercise the PDF path without an LLM.
"""

from pathlib import Path
from typing import Awaitable, Callable, List

from src.app.core.ingestion.parsers import ParsedDocument
from src.app.core.structure.headings import (
    assemble_tree,
    docx_headings,
    markdown_headings,
    pdf_candidates,
    tabular_schema,
    text_headings,
    xlsx_schema,
)
from src.app.core.structure.models import Candidate, DocumentTree, RawHeading
from src.app.core.structure.refine import refine_headings

Refiner = Callable[[List[Candidate], str], Awaitable[List[RawHeading]]]


async def build_document_tree(
    parsed: ParsedDocument,
    *,
    doc_id: str = "",
    refiner: Refiner = refine_headings,
) -> DocumentTree:
    """Build the structure tree for one parsed document, dispatching by file extension."""
    ext = Path(parsed.path).suffix.lower()
    name = Path(parsed.path).name

    if ext == ".pdf":
        candidates, total = pdf_candidates(parsed)
        structure = assemble_tree(await refiner(candidates, name), total)
    elif ext == ".md":
        structure = assemble_tree(*markdown_headings(parsed))
    elif ext == ".docx":
        structure = assemble_tree(*docx_headings(parsed))
    elif ext == ".xlsx":
        structure = xlsx_schema(parsed)
    elif ext in (".csv", ".tsv"):
        structure = tabular_schema(parsed, delimiter="\t" if ext == ".tsv" else ",")
    else:
        structure = assemble_tree(*text_headings(parsed))

    return DocumentTree(doc_id=doc_id, doc_name=name, doc_type=parsed.doc_type, structure=structure)
