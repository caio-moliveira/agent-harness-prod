"""Integration tests for incremental folder sync (#15).

``sync_folder`` reconciles a folder's live files against per-file content hashes: new files are
added, changed files re-ingested (old chunks dropped), removed files purged, unchanged files
skipped — and only the delta is re-embedded. A deterministic FakeEmbedder keeps it hermetic.
"""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


class FakeEmbedder:
    VOCAB = ["vendas", "receita", "contrato", "prazo", "cliente"]

    def _vec(self, text: str):
        low = (text or "").lower()
        return [float(low.count(w)) for w in self.VOCAB]

    async def embed_documents(self, texts):
        return [self._vec(t) for t in texts]

    async def embed_query(self, text):
        return self._vec(text)


class TestIncrementalSync:
    async def test_initial_then_unchanged_is_a_noop(self, client: AsyncClient, tmp_path):
        from src.app.core.ingestion import DocumentChunkRepository, sync_folder

        (tmp_path / "a.txt").write_text("vendas e receita", encoding="utf-8")
        (tmp_path / "b.txt").write_text("contrato e prazo", encoding="utf-8")
        crepo = DocumentChunkRepository()

        first = await sync_folder(str(tmp_path), 1, 7, FakeEmbedder(), chunk_repo=crepo)
        assert (first.added, first.updated, first.removed) == (2, 0, 0)
        assert first.chunks_indexed >= 2
        assert len(await crepo.get_embedded_chunks(1, 7)) == first.chunks_indexed

        # Nothing changed on disk → a full no-op, no re-embedding.
        second = await sync_folder(str(tmp_path), 1, 7, FakeEmbedder(), chunk_repo=crepo)
        assert (second.added, second.updated, second.removed, second.unchanged) == (0, 0, 0, 2)
        assert second.chunks_indexed == 0

    async def test_changed_file_is_reingested(self, client: AsyncClient, tmp_path):
        from src.app.core.ingestion import DocumentChunkRepository, sync_folder

        f = tmp_path / "a.txt"
        f.write_text("vendas antigas", encoding="utf-8")
        crepo = DocumentChunkRepository()
        await sync_folder(str(tmp_path), 1, 7, FakeEmbedder(), chunk_repo=crepo)

        f.write_text("contrato com prazo novo", encoding="utf-8")
        result = await sync_folder(str(tmp_path), 1, 7, FakeEmbedder(), chunk_repo=crepo)
        assert (result.added, result.updated) == (0, 1)

        chunks = await crepo.get_chunks(1, 7)
        assert all("vendas" not in c.content for c in chunks)  # old content gone
        assert any("contrato" in c.content for c in chunks)    # new content present

    async def test_removed_file_is_purged(self, client: AsyncClient, tmp_path):
        from src.app.core.ingestion import DocumentChunkRepository, sync_folder

        (tmp_path / "a.txt").write_text("vendas", encoding="utf-8")
        (tmp_path / "b.txt").write_text("contrato", encoding="utf-8")
        crepo = DocumentChunkRepository()
        await sync_folder(str(tmp_path), 1, 7, FakeEmbedder(), chunk_repo=crepo)

        (tmp_path / "b.txt").unlink()
        result = await sync_folder(str(tmp_path), 1, 7, FakeEmbedder(), chunk_repo=crepo)
        assert result.removed == 1

        chunks = await crepo.get_chunks(1, 7)
        assert all(not c.source_path.endswith("b.txt") for c in chunks)
        assert any(c.source_path.endswith("a.txt") for c in chunks)

    async def test_sync_is_isolated_per_agent(self, client: AsyncClient, tmp_path):
        from src.app.core.ingestion import DocumentChunkRepository, sync_folder

        (tmp_path / "a.txt").write_text("vendas", encoding="utf-8")
        crepo = DocumentChunkRepository()
        await sync_folder(str(tmp_path), 1, 7, FakeEmbedder(), chunk_repo=crepo)
        assert await crepo.get_chunks(1, 8) == []

    async def test_self_heals_manifest_without_chunks(self, client: AsyncClient, tmp_path):
        # Reproduce the dead state: manifest says "ingested" but the chunks were wiped (an earlier
        # sync interrupted between delete and re-insert). A re-sync must repair, not skip forever.
        from src.app.core.ingestion import DocumentChunkRepository, sync_folder

        (tmp_path / "a.txt").write_text("vendas e receita", encoding="utf-8")
        crepo = DocumentChunkRepository()
        await sync_folder(str(tmp_path), 1, 7, FakeEmbedder(), chunk_repo=crepo)
        src = str(tmp_path / "a.txt")
        await crepo.delete_by_source(1, 7, src)  # wipe chunks, keep the manifest row
        assert await crepo.count_by_source(1, 7, src) == 0

        # Hash is unchanged, so the old code would skip — the fix must re-ingest the missing chunks.
        result = await sync_folder(str(tmp_path), 1, 7, FakeEmbedder(), chunk_repo=crepo)
        assert result.unchanged == 0
        assert await crepo.count_by_source(1, 7, src) > 0

    async def test_empty_listing_does_not_wipe_corpus(self, client: AsyncClient, tmp_path):
        # A transiently unreadable/wrong folder returns no files; that must NOT purge the corpus.
        from src.app.core.ingestion import DocumentChunkRepository, sync_folder
        from src.app.core.ingestion.source_repository import IngestedFileRepository

        (tmp_path / "a.txt").write_text("contrato", encoding="utf-8")
        crepo = DocumentChunkRepository()
        await sync_folder(str(tmp_path), 1, 7, FakeEmbedder(), chunk_repo=crepo)
        src = str(tmp_path / "a.txt")
        assert await crepo.count_by_source(1, 7, src) > 0

        empty = tmp_path / "empty"
        empty.mkdir()
        result = await sync_folder(str(empty), 1, 7, FakeEmbedder(), chunk_repo=crepo)
        assert result.removed == 0
        assert await crepo.count_by_source(1, 7, src) > 0  # corpus preserved
        assert len(await IngestedFileRepository().get_known(1, 7)) >= 1  # manifest preserved

    async def test_reingest_swaps_atomically_no_duplicates(self, client: AsyncClient, tmp_path):
        from src.app.core.ingestion import DocumentChunkRepository, sync_folder

        f = tmp_path / "a.txt"
        f.write_text("vendas antigas", encoding="utf-8")
        crepo = DocumentChunkRepository()
        await sync_folder(str(tmp_path), 1, 7, FakeEmbedder(), chunk_repo=crepo)

        f.write_text("contrato com prazo novo", encoding="utf-8")  # changed → re-ingest
        await sync_folder(str(tmp_path), 1, 7, FakeEmbedder(), chunk_repo=crepo)

        chunks = await crepo.get_chunks_by_source(1, 7, str(f))
        assert chunks  # not wiped
        assert all("vendas" not in c.content for c in chunks)  # old content swapped out
        assert any("contrato" in c.content for c in chunks)  # new content present

    async def test_sync_persists_document_structure(self, client: AsyncClient, tmp_path):
        # The manifest also stores the per-file structure tree (the vectorless index). It is built
        # from the parsed document at ingest and left untouched on an unchanged re-sync.
        import json

        from src.app.core.ingestion import DocumentChunkRepository, sync_folder
        from src.app.core.ingestion.source_repository import IngestedFileRepository

        (tmp_path / "notas.md").write_text("# Título\n\n## Seção A\ntexto\n\n## Seção B\n", encoding="utf-8")
        (tmp_path / "dados.csv").write_text("mes,receita\njan,1000\n", encoding="utf-8")
        crepo = DocumentChunkRepository()

        await sync_folder(str(tmp_path), 1, 7, FakeEmbedder(), chunk_repo=crepo)
        known = await IngestedFileRepository().get_known(1, 7)

        md_row = next(r for p, r in known.items() if p.endswith("notas.md"))
        md_tree = json.loads(md_row.structure)
        md_titles = [n["title"] for n in md_tree["structure"]]
        assert "Título" in md_titles  # markdown headings became the tree

        csv_row = next(r for p, r in known.items() if p.endswith("dados.csv"))
        csv_tree = json.loads(csv_row.structure)
        assert [c["title"] for c in csv_tree["structure"][0]["nodes"]] == ["mes", "receita"]  # schema

        # Unchanged re-sync: nothing rebuilt, structure preserved.
        second = await sync_folder(str(tmp_path), 1, 7, FakeEmbedder(), chunk_repo=crepo)
        assert (second.added, second.updated, second.unchanged) == (0, 0, 2)
        again = await IngestedFileRepository().get_known(1, 7)
        assert next(r for p, r in again.items() if p.endswith("notas.md")).structure == md_row.structure

    async def test_sync_populates_manifest_metadata(self, client: AsyncClient, tmp_path):
        # The IngestedFile row is the document manifest: sync must fill doc_id/title/page_count so
        # the document tools can catalog the corpus without touching disk.
        from src.app.core.ingestion import DocumentChunkRepository, sync_folder
        from src.app.core.ingestion.source_repository import IngestedFileRepository

        (tmp_path / "contrato.txt").write_text("contrato com prazo", encoding="utf-8")
        await sync_folder(str(tmp_path), 1, 7, FakeEmbedder(), chunk_repo=DocumentChunkRepository())

        known = await IngestedFileRepository().get_known(1, 7)
        record = next(r for p, r in known.items() if p.endswith("contrato.txt"))
        assert record.doc_id.startswith("doc_") and len(record.doc_id) > 4
        assert record.title == "contrato.txt"  # display-only file name
        assert record.page_count == 1
        assert record.text_layer == "native"
        assert record.ocr_confidence == 1.0
        # The id is derived from the content hash — stable and ASCII.
        assert record.doc_id == f"doc_{record.content_hash[:12]}"
