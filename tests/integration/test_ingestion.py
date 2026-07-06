"""Integration tests for document ingestion (#13): parse -> chunk -> persist, scoped per agent.

Three pre-agreed seams:
  1. ``extract_document`` — the per-type parsers (PDF/Word/Excel/text) preserve text + location.
  2. ``chunk_document`` — splitting carries provenance metadata (section, order, author, needs_ocr).
  3. ``ingest_folder`` — end-to-end: a folder becomes chunks persisted for (user_id, agent_id),
     isolated from other agents/users.

Fixtures are synthesized with the real writer libraries (python-docx / openpyxl / reportlab) so the
parsers run against genuine files, not hand-rolled bytes.
"""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


# --------------------------- fixture builders ---------------------------

def _make_docx(path):
    from docx import Document

    d = Document()
    d.add_heading("Contrato", level=1)
    d.add_paragraph("Parte A contrata Parte B pelo prazo de 12 meses.")
    d.core_properties.author = "Ana Jurídica"
    d.save(str(path))


def _make_xlsx(path):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Vendas"
    ws.append(["mes", "receita"])
    ws.append(["jan", 1000])
    wb.save(str(path))


def _make_pdf(path):
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path))
    c.drawString(72, 720, "Relatorio de vendas janeiro")
    c.save()


# --------------------------- Seam 1: parsers ---------------------------

class TestParsers:
    def test_pdf_text_extracted(self, tmp_path):
        from src.app.core.ingestion.parsers import extract_document

        p = tmp_path / "rel.pdf"
        _make_pdf(p)
        doc = extract_document(str(p))
        assert doc.doc_type == "pdf"
        assert "vendas" in " ".join(s.text for s in doc.sections).lower()

    def test_docx_sections_author_and_headings(self, tmp_path):
        from src.app.core.ingestion.parsers import extract_document

        p = tmp_path / "c.docx"
        _make_docx(p)
        doc = extract_document(str(p))
        assert doc.doc_type == "docx"
        assert doc.author == "Ana Jurídica"
        joined = " ".join(s.text for s in doc.sections)
        assert "Parte A" in joined
        assert any(s.location == "Contrato" for s in doc.sections)  # heading became a location

    def test_xlsx_sheet_preserves_schema(self, tmp_path):
        from src.app.core.ingestion.parsers import extract_document

        p = tmp_path / "v.xlsx"
        _make_xlsx(p)
        doc = extract_document(str(p))
        assert doc.doc_type == "xlsx"
        section = doc.sections[0]
        assert section.location == "planilha Vendas"
        assert "mes" in section.text and "jan" in section.text  # header + row preserved

    def test_unsupported_extension_raises(self, tmp_path):
        from src.app.core.ingestion.parsers import UnsupportedDocumentError, extract_document

        p = tmp_path / "x.zip"
        p.write_bytes(b"PK\x03\x04")
        with pytest.raises(UnsupportedDocumentError):
            extract_document(str(p))


# --------------------------- Seam 2: chunking ---------------------------

class TestChunking:
    def test_metadata_carried_and_ordered(self):
        from src.app.core.ingestion.chunking import chunk_document
        from src.app.core.ingestion.parsers import ParsedDocument, ParsedSection

        long_text = "\n".join(f"Parágrafo {i} com algum conteúdo." for i in range(200))
        doc = ParsedDocument(
            path="/w/c.docx", doc_type="docx", author="Ana",
            sections=[ParsedSection(location="Cláusula 1", text=long_text)],
        )
        chunks = chunk_document(doc, max_chars=300)
        assert len(chunks) > 1
        assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
        assert all(c.source_path == "/w/c.docx" for c in chunks)
        assert all(c.section == "Cláusula 1" for c in chunks)
        assert all(c.author == "Ana" for c in chunks)

    def test_scanned_page_yields_needs_ocr_placeholder(self):
        from src.app.core.ingestion.chunking import chunk_document
        from src.app.core.ingestion.parsers import ParsedDocument, ParsedSection

        doc = ParsedDocument(
            path="/w/scan.pdf", doc_type="pdf",
            sections=[ParsedSection(location="página 1", text="", needs_ocr=True)],
        )
        chunks = chunk_document(doc)
        assert len(chunks) == 1
        assert chunks[0].needs_ocr is True


# --------------------------- Seam 3: ingest_folder ---------------------------

class TestIngestFolder:
    async def test_folder_becomes_scoped_chunks(self, client: AsyncClient, tmp_path):
        from src.app.core.ingestion import DocumentChunkRepository, ingest_folder

        _make_docx(tmp_path / "contrato.docx")
        _make_xlsx(tmp_path / "vendas.xlsx")
        (tmp_path / "nota.txt").write_text("Observação importante sobre o cliente.", encoding="utf-8")

        repo = DocumentChunkRepository()
        result = await ingest_folder(str(tmp_path), user_id=1, agent_id=7, repo=repo)

        assert result.files_ingested == 3
        assert result.chunks > 0

        chunks = await repo.get_chunks(user_id=1, agent_id=7)
        assert len(chunks) == result.chunks
        assert {c.doc_type for c in chunks} == {"docx", "xlsx", "text"}
        assert all(c.source_path for c in chunks)
        assert all(c.section for c in chunks)

        # Isolation: another agent and another user see nothing.
        assert await repo.get_chunks(user_id=1, agent_id=8) == []
        assert await repo.get_chunks(user_id=2, agent_id=7) == []

    async def test_unreadable_file_is_skipped_not_fatal(self, client: AsyncClient, tmp_path):
        from src.app.core.ingestion import DocumentChunkRepository, ingest_folder

        (tmp_path / "ok.txt").write_text("conteúdo válido", encoding="utf-8")
        # A .docx extension over non-docx bytes makes the parser raise; ingestion must survive it.
        (tmp_path / "broken.docx").write_bytes(b"not a real docx")

        result = await ingest_folder(str(tmp_path), user_id=1, agent_id=9, repo=DocumentChunkRepository())
        assert result.files_ingested == 1
        assert result.files_skipped == 1
