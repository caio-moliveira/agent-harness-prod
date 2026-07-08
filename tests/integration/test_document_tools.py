"""Tests for the Phase 1 document tools: list_documents + read_document.

These operate on the ingested manifest/chunks (not the live disk): list_documents catalogs by
stable doc_id, read_document opens an explicit page range with a token budget, printed-folio
detection (with divergence vs the PDF index), and records the pages it read into the session.
"""

import pytest
from httpx import AsyncClient

from src.app.agents.data_agent.document_tools import make_document_tools
from src.app.core.ingestion.chunk_model import DocumentChunk
from src.app.core.ingestion.chunk_repository import DocumentChunkRepository
from src.app.core.ingestion.source_model import derive_doc_id
from src.app.core.ingestion.source_repository import IngestedFileRepository
from src.app.core.sandbox.registry import registry

pytestmark = pytest.mark.asyncio

USER, AGENT = 1, 7


async def _seed_document(source_path: str, content_hash: str, pages: list[str]) -> str:
    """Insert a manifest row + one chunk per page; return the derived doc_id."""
    await IngestedFileRepository().upsert(
        USER, AGENT, source_path, content_hash, len(pages), page_count=len(pages), text_layer="native"
    )
    chunks = [
        DocumentChunk(
            user_id=USER,
            agent_id=AGENT,
            source_path=source_path,
            doc_type="pdf",
            section=f"página {i}",
            chunk_index=i - 1,
            content=text,
            meta={"needs_ocr": False},
        )
        for i, text in enumerate(pages, start=1)
    ]
    await DocumentChunkRepository().add_chunks(chunks)
    return derive_doc_id(content_hash)


class TestListDocuments:
    async def test_lists_catalog_with_ids_and_pages(self, client: AsyncClient):
        doc_id = await _seed_document("/docs/lei.pdf", "hash_lei_0001", ["p1", "p2", "p3"])
        tools = {t.name: t for t in make_document_tools(USER, AGENT, "sess-list")}

        out = await tools["list_documents"].ainvoke({})
        assert doc_id in out
        assert '"lei.pdf"' in out  # title is display-only
        assert "3 pág" in out

    async def test_empty_corpus_message(self, client: AsyncClient):
        tools = {t.name: t for t in make_document_tools(USER, 999, "sess-empty")}
        out = await tools["list_documents"].ainvoke({})
        assert "Nenhum documento" in out


class TestReadDocument:
    async def test_reads_range_and_records_pages(self, client: AsyncClient):
        # Page 1 prints folio 270 while its PDF index is 1 → divergence must be flagged.
        doc_id = await _seed_document(
            "/docs/const.pdf",
            "hash_const_0001",
            ["Conteúdo da página um.\n270", "Página dois: contratos.\n271", "Página três.\n272"],
        )
        tools = {t.name: t for t in make_document_tools(USER, AGENT, "sess-read")}

        out = await tools["read_document"].ainvoke({"doc_id": doc_id, "start_page": 1, "end_page": 2})
        assert "página um" in out and "contratos" in out
        assert "PDF pág. 1/3" in out
        assert "fólio impresso 270" in out and "divergente" in out
        assert "Página três" not in out  # page 3 not requested

        res = await registry.get("sess-read")
        assert (doc_id, 1) in res.read_pages and (doc_id, 2) in res.read_pages
        assert (doc_id, 3) not in res.read_pages

    async def test_unknown_doc_id(self, client: AsyncClient):
        tools = {t.name: t for t in make_document_tools(USER, AGENT, "sess-x")}
        out = await tools["read_document"].ainvoke({"doc_id": "doc_missing", "start_page": 1, "end_page": 1})
        assert "não encontrado" in out

    async def test_out_of_range(self, client: AsyncClient):
        doc_id = await _seed_document("/docs/small.pdf", "hash_small_1", ["only page"])
        tools = {t.name: t for t in make_document_tools(USER, AGENT, "sess-oor")}
        out = await tools["read_document"].ainvoke({"doc_id": doc_id, "start_page": 5, "end_page": 6})
        assert "fora do documento" in out

    async def test_partial_read_reports_next_range(self, client: AsyncClient):
        # Two ~4k-char pages: page 1 fits the ~6k budget, page 2 would exceed → partial read.
        big = "palavra " * 550  # ~4400 chars
        doc_id = await _seed_document("/docs/long.pdf", "hash_long_1", [big, big])
        tools = {t.name: t for t in make_document_tools(USER, AGENT, "sess-partial")}

        out = await tools["read_document"].ainvoke({"doc_id": doc_id, "start_page": 1, "end_page": 2})
        assert "leitura parcial" in out
        assert f"read_document('{doc_id}', 2, 2)" in out

        res = await registry.get("sess-partial")
        assert (doc_id, 1) in res.read_pages
        assert (doc_id, 2) not in res.read_pages  # page 2 was NOT read (deferred to next call)
