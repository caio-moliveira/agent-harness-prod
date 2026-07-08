"""Tests for the Phase 1 document tools: list_documents + read_document.

These operate on the ingested manifest/chunks (not the live disk): list_documents catalogs by
stable doc_id, read_document opens an explicit page range with a token budget, printed-folio
detection (with divergence vs the PDF index), and records the pages it read into the session.
"""

import pymupdf
import pytest
from httpx import AsyncClient

from src.app.agents.data_agent.document_tools import make_document_tools
from src.app.core.common.config import settings
from src.app.core.ingestion.chunk_model import DocumentChunk
from src.app.core.ingestion.chunk_repository import DocumentChunkRepository
from src.app.core.ingestion.normalize import normalize_text
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


class TestNormalize:
    def test_folds_accent_case_and_ordinal(self):
        # The transcript case: the doc says "nº", the user types "no" — they must match.
        assert normalize_text("Emenda Constitucional nº 100") == "emenda constitucional no 100"
        assert normalize_text("SÃO  PAULO") == "sao paulo"


class TestSearchDocuments:
    async def test_literal_hit_across_accent_and_case(self, client: AsyncClient):
        doc_id = await _seed_document(
            "/docs/cf.pdf",
            "hash_cf_0001",
            ["Texto irrelevante.", "Ver a Emenda Constitucional nº 100, de 2019, sobre orçamento.\n270"],
        )
        tools = {t.name: t for t in make_document_tools(USER, AGENT, "sess-search")}

        # Query without the º and without accents still matches the accented source text.
        out = await tools["search_documents"].ainvoke({"query": "Emenda Constitucional no 100"})
        assert doc_id in out
        assert "PDF pág. 2" in out and "fólio 270" in out
        assert "Busca literal" in out and "emenda constitucional no 100" in out  # normalized query echoed

    async def test_empty_result_is_distinguishable(self, client: AsyncClient):
        await _seed_document("/docs/x.pdf", "hash_x_0001", ["conteúdo qualquer"])
        tools = {t.name: t for t in make_document_tools(USER, AGENT, "sess-search2")}

        out = await tools["search_documents"].ainvoke({"query": "termo-que-nao-existe-42"})
        assert "Nenhuma ocorrência literal" in out
        assert "termo-que-nao-existe-42" in out  # the normalized query, so it's not a malformed-query mystery
        assert "buscar_documentos" in out  # steer to semantic search for concepts


def _make_pdf(path, pages: int = 1) -> None:
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Página {i + 1}")
    doc.save(str(path))
    doc.close()


def _img_call(doc_id: str, page: int) -> dict:
    return {"name": "read_page_image", "args": {"doc_id": doc_id, "page": page}, "id": "call_x", "type": "tool_call"}


class TestReadPageImage:
    async def test_renders_page_as_image_block(self, client: AsyncClient, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "SANDBOX_ALLOWED_ROOTS", [str(tmp_path)])
        pdf = tmp_path / "laudo.pdf"
        _make_pdf(pdf, pages=2)
        doc_id = await _seed_document(str(pdf), "hash_img_0001", ["p1", "p2"])
        tools = {t.name: t for t in make_document_tools(USER, AGENT, "sess-img")}

        result = await tools["read_page_image"].ainvoke(_img_call(doc_id, 1))
        # The model receives an actual image content block, not text.
        assert any(b.get("type") == "image" for b in result.content_blocks)

        res = await registry.get("sess-img")
        assert (doc_id, 1) in res.read_pages

        # Second call hits the on-disk cache and still returns an image.
        again = await tools["read_page_image"].ainvoke(_img_call(doc_id, 1))
        assert any(b.get("type") == "image" for b in again.content_blocks)

    async def test_non_pdf_is_rejected(self, client: AsyncClient, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "SANDBOX_ALLOWED_ROOTS", [str(tmp_path)])
        doc_id = await _seed_document(str(tmp_path / "planilha.xlsx"), "hash_xlsx_1", ["a"])
        tools = {t.name: t for t in make_document_tools(USER, AGENT, "sess-img2")}
        result = await tools["read_page_image"].ainvoke(_img_call(doc_id, 1))
        assert "só rasteriza PDFs" in result.content

    async def test_out_of_range(self, client: AsyncClient, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "SANDBOX_ALLOWED_ROOTS", [str(tmp_path)])
        pdf = tmp_path / "doc.pdf"
        _make_pdf(pdf, pages=1)
        doc_id = await _seed_document(str(pdf), "hash_img_oor", ["p1"])
        tools = {t.name: t for t in make_document_tools(USER, AGENT, "sess-img3")}
        result = await tools["read_page_image"].ainvoke(_img_call(doc_id, 9))
        assert "fora do documento" in result.content

    async def test_outside_allowed_roots_is_blocked(self, client: AsyncClient, tmp_path, monkeypatch):
        # A path not under any allowed root must be refused even if the file exists on disk.
        monkeypatch.setattr(settings, "SANDBOX_ALLOWED_ROOTS", ["/some/other/root"])
        pdf = tmp_path / "secret.pdf"
        _make_pdf(pdf, pages=1)
        doc_id = await _seed_document(str(pdf), "hash_img_sec", ["p1"])
        tools = {t.name: t for t in make_document_tools(USER, AGENT, "sess-img4")}
        result = await tools["read_page_image"].ainvoke(_img_call(doc_id, 1))
        assert "indisponível" in result.content
