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
