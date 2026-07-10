"""Tests for the local document structure builder (the tree index that replaces vector RAG).

Hermetic: no DB, no LLM. The PDF path is exercised with an injected fake refiner so we test the
heuristic → refine → assemble machinery deterministically; the real LLM refiner is validated
separately (manually) to keep CI offline.
"""

import pytest

from src.app.core.ingestion.parsers import ParsedDocument, ParsedSection
from src.app.core.structure.builder import build_document_tree
from src.app.core.structure.headings import pdf_candidates
from src.app.core.structure.models import RawHeading

pytestmark = pytest.mark.asyncio


def _flatten(nodes):
    """Yield every node in the tree, depth-first."""
    for n in nodes:
        yield n
        yield from _flatten(n.nodes)


async def test_markdown_nests_by_heading_level():
    """Markdown ``#``/``##``/``###`` become a nested tree by level."""
    md = "# Título\n\nintro\n\n## Seção A\ntexto\n\n## Seção B\n### Sub B1\n"
    parsed = ParsedDocument(path="/w/notas.md", doc_type="text", sections=[ParsedSection(location="conteúdo", text=md)])

    tree = await build_document_tree(parsed)

    titles = [n.title for n in _flatten(tree.structure)]
    assert {"Título", "Seção A", "Seção B", "Sub B1"} <= set(titles)
    sec_b = next(n for n in _flatten(tree.structure) if n.title == "Seção B")
    assert any(c.title == "Sub B1" for c in sec_b.nodes)  # ### nests under ##


async def test_docx_blocks_become_nodes_skipping_corpo():
    """Each docx block becomes a node; the pre-heading ``corpo`` block is skipped."""
    parsed = ParsedDocument(
        path="/w/contrato.docx",
        doc_type="docx",
        sections=[
            ParsedSection(location="corpo", text="preâmbulo"),
            ParsedSection(location="Cláusula 1", text="..."),
            ParsedSection(location="Cláusula 2", text="..."),
            ParsedSection(location="tabela 1", text="a | b"),
        ],
    )

    tree = await build_document_tree(parsed)

    assert [n.title for n in tree.structure] == ["Cláusula 1", "Cláusula 2", "tabela 1"]
    assert all(n.node_id for n in tree.structure)


async def test_xlsx_schema_sheet_then_columns():
    """An xlsx becomes a node per sheet, with columns as children."""
    parsed = ParsedDocument(
        path="/w/vendas.xlsx",
        doc_type="xlsx",
        sections=[ParsedSection(location="planilha Vendas", text="mes,receita\njan,1000")],
    )

    tree = await build_document_tree(parsed)

    assert tree.structure[0].title == "planilha Vendas"
    assert [c.title for c in tree.structure[0].nodes] == ["mes", "receita"]


async def test_csv_schema_columns():
    """A csv becomes a single table node whose children are its columns."""
    parsed = ParsedDocument(
        path="/w/d.csv", doc_type="text", sections=[ParsedSection(location="conteúdo", text="a,b,c\n1,2,3")]
    )

    tree = await build_document_tree(parsed)

    assert [c.title for c in tree.structure[0].nodes] == ["a", "b", "c"]


async def test_pdf_refiner_drops_signatures_and_nests():
    """The refiner's rejects (a signature) are dropped; kept level-2 headings nest under level-1."""
    p1 = "EMENTA\nalgum texto da ementa\nI – RELATÓRIO\nrelato do processo"
    p2 = "II – FUNDAMENTAÇÃO\n1. Cadastro de reserva\nanálise técnica\nWANDERLEY ÁVILA"
    parsed = ParsedDocument(
        path="/w/acordao.pdf",
        doc_type="pdf",
        sections=[ParsedSection(location="página 1", text=p1), ParsedSection(location="página 2", text=p2)],
    )

    async def fake_refiner(candidates, doc_name):
        # Mimic the LLM: keep the heuristic candidates except the uppercase signature line.
        kept = [c for c in candidates if c.text != "WANDERLEY ÁVILA"]
        return [RawHeading(title=c.text, level=c.level, start=c.page) for c in kept]

    tree = await build_document_tree(parsed, refiner=fake_refiner)

    titles = [n.title for n in _flatten(tree.structure)]
    assert "WANDERLEY ÁVILA" not in titles
    assert {"EMENTA", "I – RELATÓRIO", "II – FUNDAMENTAÇÃO"} <= set(titles)
    fundamentacao = next(n for n in _flatten(tree.structure) if n.title.startswith("II"))
    assert any(c.title.startswith("1.") for c in fundamentacao.nodes)  # level-2 nests under level-1


async def test_pdf_heuristic_recall_includes_real_headings():
    """The PDF heuristic must at least propose the real headings (recall), before the LLM prunes."""
    # Without the LLM, the heuristic must at least PROPOSE the real headings (recall).
    parsed = ParsedDocument(
        path="/w/x.pdf",
        doc_type="pdf",
        sections=[ParsedSection(location="página 1", text="EMENTA\nI – RELATÓRIO\n1. Cadastro de reserva")],
    )

    candidates, total = pdf_candidates(parsed)

    texts = [c.text for c in candidates]
    assert {"EMENTA", "I – RELATÓRIO", "1. Cadastro de reserva"} <= set(texts)
    assert total == 1
