"""Single-file ingestion: parse → build the structure tree + located content for the manifest.

Parsing runs in a worker thread (``asyncio.to_thread``) so a large corpus never blocks the event
loop. The incremental ``sync_folder`` decides which files to (re)ingest; this module turns one file
into the manifest fields it persists — the structure tree, the located text (what the reading tools
slice), and page/text-layer metadata. No chunks, no embeddings: the corpus is vectorless.
"""

import asyncio
import json
import os
from typing import List

from pydantic import BaseModel

from src.app.core.ingestion.parsers import ParsedDocument, extract_document, is_supported
from src.app.core.structure.builder import build_document_tree


class IngestFileResult(BaseModel):
    """Per-file ingestion outcome: manifest metadata + the structure tree and located text."""

    page_count: int = 0
    text_layer: str = "native"  # native | ocr | mixed
    ocr_confidence: float = 1.0
    # Head of the extracted text, so sync can generate the map description (#23) without re-parsing.
    text_preview: str = ""
    # The document's structure tree (PageIndex-style) as a JSON string.
    structure: str = ""
    # The document's located text as a JSON string (the parsed sections) — what the reading tools slice.
    content: str = ""


_TEXT_PREVIEW_CHARS = 4000


def derive_manifest_meta(parsed: ParsedDocument) -> tuple[int, str, float]:
    """Derive ``(page_count, text_layer, ocr_confidence)`` from a parsed document.

    ``ocr_confidence`` is a heuristic — the fraction of sections (pages) that yielded extractable
    text — not a true OCR score; a real OCR pass can replace it later. ``text_layer`` summarizes it:
    all-text → ``native``, no-text → ``ocr``, some-text → ``mixed``.
    """
    sections = parsed.sections
    page_count = len(sections)
    if page_count == 0:
        return 0, "native", 1.0
    with_text = sum(1 for s in sections if s.text.strip())
    confidence = with_text / page_count
    if with_text == page_count:
        text_layer = "native"
    elif with_text == 0:
        text_layer = "ocr"
    else:
        text_layer = "mixed"
    return page_count, text_layer, confidence


def _list_supported_files(folder: str) -> List[str]:
    r"""Walk ``folder`` and return absolute paths of files with a registered parser.

    Paths are normalized (``os.path.normpath``) so the manifest key is stable regardless of how the
    folder was passed (mixed ``/`` and ``\`` separators would otherwise create duplicate rows —
    the same file tracked twice — and spurious add/remove churn on every sync).
    """
    found: List[str] = []
    for root, _dirs, files in os.walk(folder):
        for name in files:
            path = os.path.normpath(os.path.join(root, name))
            if is_supported(path):
                found.append(path)
    return sorted(found)


async def ingest_file(path: str) -> IngestFileResult:
    """Parse one file and build its manifest fields: structure tree, located text, and metadata.

    Parsing runs in a worker thread so it never blocks the event loop. Raises on a parse error so the
    caller (sync) can skip the file. Building the tree spends one LLM refine call for PDFs.
    """
    parsed = await asyncio.to_thread(extract_document, path)
    page_count, text_layer, confidence = derive_manifest_meta(parsed)
    preview = "\n".join(s.text for s in parsed.sections if s.text.strip())[:_TEXT_PREVIEW_CHARS]
    structure = (await build_document_tree(parsed)).model_dump_json()
    content = json.dumps(
        [{"location": s.location, "text": s.text, "needs_ocr": s.needs_ocr} for s in parsed.sections],
        ensure_ascii=False,
    )
    return IngestFileResult(
        page_count=page_count,
        text_layer=text_layer,
        ocr_confidence=confidence,
        text_preview=preview,
        structure=structure,
        content=content,
    )
