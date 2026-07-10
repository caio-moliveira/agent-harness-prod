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
from src.app.core.ingestion.normalize import normalize_text
from src.app.core.ingestion.source_model import derive_doc_id
from src.app.core.ingestion.source_repository import IngestedFileRepository
from src.app.core.sandbox.registry import registry

pytestmark = pytest.mark.asyncio

USER, AGENT = 1, 7


async def _seed_document(source_path: str, content_hash: str, pages: list[str]) -> str:
    """Insert a manifest row with the located text (content) for the given pages; return the doc_id."""
    import json

    content = json.dumps(
        [{"location": f"página {i}", "text": text, "needs_ocr": False} for i, text in enumerate(pages, start=1)],
        ensure_ascii=False,
    )
    await IngestedFileRepository().upsert(
        USER, AGENT, source_path, content_hash, page_count=len(pages), text_layer="native", content=content
    )
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

    async def test_unknown_doc_id_empty_corpus(self, client: AsyncClient):
        # No documents indexed at all → guide the agent to connect a folder, not a bare "not found".
        tools = {t.name: t for t in make_document_tools(USER, AGENT, "sess-x")}
        out = await tools["read_document"].ainvoke({"doc_id": "doc_missing", "start_page": 1, "end_page": 1})
        assert "Nenhum documento indexado" in out

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
        assert "get_document_structure" in out  # steer to structure navigation for concepts


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


class TestFuzzyDocResolution:
    """The real transcript failure: the model dropped the doc_ prefix and used the filename, and
    read_document failed 5x. Resolution must tolerate those and self-correct in one step."""

    async def test_resolves_by_filename(self, client: AsyncClient):
        await _seed_document("/docs/representacao_986639.pdf", "hashrep000001", ["III – DECISÃO texto aqui", "b"])
        tools = {t.name: t for t in make_document_tools(USER, AGENT, "sess-fuzzy1")}
        # Model uses the filename (no extension) instead of the opaque doc_id.
        out = await tools["read_document"].ainvoke({"doc_id": "representacao_986639", "start_page": 1, "end_page": 1})
        assert "DECISÃO texto aqui" in out

    async def test_resolves_bare_id_without_doc_prefix(self, client: AsyncClient):
        doc_id = await _seed_document("/docs/x.pdf", "hashbare00001", ["conteudo unico"])
        bare = doc_id[len("doc_") :]  # the model dropped the "doc_" prefix
        tools = {t.name: t for t in make_document_tools(USER, AGENT, "sess-fuzzy2")}
        out = await tools["read_document"].ainvoke({"doc_id": bare, "start_page": 1, "end_page": 1})
        assert "conteudo unico" in out

    async def test_not_found_echoes_catalog(self, client: AsyncClient):
        await _seed_document("/docs/lei-10741.pdf", "hashlei000001", ["texto"])
        tools = {t.name: t for t in make_document_tools(USER, AGENT, "sess-fuzzy3")}
        out = await tools["read_document"].ainvoke({"doc_id": "documento-inexistente", "start_page": 1, "end_page": 1})
        assert "não encontrado" in out
        assert "lei-10741.pdf" in out  # catalog echoed → model fixes it in one step

    async def test_start_page_zero_gives_clear_range_message(self, client: AsyncClient):
        # After resolution (by filename), an invalid start_page must give a range message — not
        # the misleading "não encontrado" the old code returned.
        await _seed_document("/docs/relatorio.pdf", "hashrel000001", ["p1", "p2"])
        tools = {t.name: t for t in make_document_tools(USER, AGENT, "sess-fuzzy4")}
        out = await tools["read_document"].ainvoke({"doc_id": "relatorio", "start_page": 0, "end_page": 1})
        assert "Intervalo inválido" in out

    async def test_read_page_image_resolves_by_filename(self, client: AsyncClient, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "SANDBOX_ALLOWED_ROOTS", [str(tmp_path)])
        pdf = tmp_path / "laudo_pericial.pdf"
        _make_pdf(pdf, pages=1)
        await _seed_document(str(pdf), "hashlaudo0001", ["p"])
        tools = {t.name: t for t in make_document_tools(USER, AGENT, "sess-fuzzy5")}
        call = {"name": "read_page_image", "args": {"doc_id": "laudo_pericial", "page": 1}, "id": "c", "type": "tool_call"}
        result = await tools["read_page_image"].ainvoke(call)
        assert any(b.get("type") == "image" for b in result.content_blocks)


def _content_json(parsed) -> str:
    """Serialize parsed sections into the manifest ``content`` JSON (what the reading tools slice)."""
    import json

    return json.dumps(
        [{"location": s.location, "text": s.text, "needs_ocr": s.needs_ocr} for s in parsed.sections],
        ensure_ascii=False,
    )


class TestStructureTools:
    """get_document_structure (the tree outline) + get_node_content (section text from the index)."""

    async def test_outline_then_read_section(self, client: AsyncClient, tmp_path):
        import json

        from src.app.core.ingestion.parsers import extract_document
        from src.app.core.structure.builder import build_document_tree

        md = tmp_path / "notas.md"
        md.write_text("# Título\n\nintro\n\n## Seção A\ncorpo da seção A\n", encoding="utf-8")
        parsed = extract_document(str(md))
        tree = await build_document_tree(parsed)
        await IngestedFileRepository().upsert(
            USER, AGENT, str(md), "hash_md_00001", page_count=1,
            structure=tree.model_dump_json(), content=_content_json(parsed),
        )
        tools = {t.name: t for t in make_document_tools(USER, AGENT, None)}

        outline = await tools["get_document_structure"].ainvoke({"doc_id": "notas"})
        assert "Título" in outline and "Seção A" in outline

        flat, stack = [], list(json.loads(tree.model_dump_json())["structure"])
        while stack:
            node = stack.pop()
            flat.append(node)
            stack.extend(node["nodes"])
        node_id = next(n["node_id"] for n in flat if n["title"] == "Seção A")

        content = await tools["get_node_content"].ainvoke({"doc_id": "notas", "node_id": node_id})
        assert "corpo da seção A" in content

    async def test_get_node_content_unknown_node_id(self, client: AsyncClient, tmp_path):
        from src.app.core.ingestion.parsers import extract_document
        from src.app.core.structure.builder import build_document_tree

        md = tmp_path / "x.md"
        md.write_text("# H\n\ntexto\n", encoding="utf-8")
        parsed = extract_document(str(md))
        tree = await build_document_tree(parsed)
        await IngestedFileRepository().upsert(
            USER, AGENT, str(md), "hash_md_00002", page_count=1,
            structure=tree.model_dump_json(), content=_content_json(parsed),
        )
        tools = {t.name: t for t in make_document_tools(USER, AGENT, None)}

        out = await tools["get_node_content"].ainvoke({"doc_id": "x", "node_id": "9999"})
        assert "não existe" in out
