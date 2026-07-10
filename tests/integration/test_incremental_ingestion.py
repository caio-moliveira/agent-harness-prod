"""Integration tests for incremental folder sync (#15) — vectorless.

``sync_folder`` reconciles a folder's live files against per-file content hashes in the IngestedFile
manifest: new files added, changed files re-ingested (structure tree + located text rebuilt), removed
files soft-deleted, unchanged files skipped. No chunks, no embeddings — reads come from the manifest.
"""

import json

import pytest
from httpx import AsyncClient

from src.app.core.ingestion import sync_folder
from src.app.core.ingestion.source_model import IngestedFileStatus
from src.app.core.ingestion.source_repository import IngestedFileRepository

pytestmark = pytest.mark.asyncio

USER, AGENT = 1, 7


async def _manifest(user: int = USER, agent: int = AGENT) -> dict:
    """The (user, agent) manifest keyed by source_path."""
    return await IngestedFileRepository().get_known(user, agent)


class TestIncrementalSync:
    async def test_initial_then_unchanged_is_a_noop(self, client: AsyncClient, tmp_path):
        (tmp_path / "a.txt").write_text("vendas e receita", encoding="utf-8")
        (tmp_path / "b.txt").write_text("contrato e prazo", encoding="utf-8")

        first = await sync_folder(str(tmp_path), USER, AGENT)
        assert (first.added, first.updated, first.removed) == (2, 0, 0)
        known = await _manifest()
        assert len(known) == 2
        assert all(r.content is not None for r in known.values())  # located text persisted

        second = await sync_folder(str(tmp_path), USER, AGENT)
        assert (second.added, second.updated, second.removed, second.unchanged) == (0, 0, 0, 2)

    async def test_changed_file_is_reingested(self, client: AsyncClient, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("vendas antigas", encoding="utf-8")
        await sync_folder(str(tmp_path), USER, AGENT)

        f.write_text("contrato com prazo novo", encoding="utf-8")
        result = await sync_folder(str(tmp_path), USER, AGENT)
        assert (result.added, result.updated) == (0, 1)

        row = next(r for p, r in (await _manifest()).items() if p.endswith("a.txt"))
        text = json.loads(row.content)[0]["text"]
        assert "vendas" not in text and "contrato" in text  # content swapped

    async def test_removed_file_is_soft_deleted(self, client: AsyncClient, tmp_path):
        (tmp_path / "a.txt").write_text("vendas", encoding="utf-8")
        (tmp_path / "b.txt").write_text("contrato", encoding="utf-8")
        await sync_folder(str(tmp_path), USER, AGENT)

        (tmp_path / "b.txt").unlink()
        result = await sync_folder(str(tmp_path), USER, AGENT)
        assert result.removed == 1
        row = next(r for p, r in (await _manifest()).items() if p.endswith("b.txt"))
        assert row.status == IngestedFileStatus.DELETED

    async def test_sync_is_isolated_per_agent(self, client: AsyncClient, tmp_path):
        (tmp_path / "a.txt").write_text("vendas", encoding="utf-8")
        await sync_folder(str(tmp_path), USER, AGENT)
        assert await _manifest(USER, 8) == {}  # another agent sees nothing

    async def test_self_heals_manifest_without_content(self, client: AsyncClient, tmp_path):
        # A row from before the content column (content is NULL) must be re-ingested, not skipped —
        # otherwise a legacy corpus would never gain a structure tree / located text.
        from src.app.core.ingestion.sync import _hash_file

        f = tmp_path / "a.txt"
        f.write_text("vendas e receita", encoding="utf-8")
        digest = _hash_file(str(f))
        # Legacy manifest row: correct hash, but content left unset (None).
        await IngestedFileRepository().upsert(USER, AGENT, str(f), digest, page_count=1)

        result = await sync_folder(str(tmp_path), USER, AGENT)
        assert result.updated == 1  # re-ingested despite the matching hash
        assert (await _manifest())[str(f)].content is not None  # repaired

    async def test_empty_listing_does_not_wipe_corpus(self, client: AsyncClient, tmp_path):
        # A transiently unreadable/wrong folder returns no files; that must NOT purge the corpus.
        (tmp_path / "a.txt").write_text("contrato", encoding="utf-8")
        await sync_folder(str(tmp_path), USER, AGENT)

        empty = tmp_path / "empty"
        empty.mkdir()
        result = await sync_folder(str(empty), USER, AGENT)
        assert result.removed == 0
        assert len(await _manifest()) >= 1  # manifest preserved

    async def test_sync_populates_manifest_metadata(self, client: AsyncClient, tmp_path):
        # The IngestedFile row is the document manifest: sync fills doc_id/title/page_count so the
        # document tools can catalog the corpus without touching disk.
        (tmp_path / "contrato.txt").write_text("contrato com prazo", encoding="utf-8")
        await sync_folder(str(tmp_path), USER, AGENT)

        row = next(r for p, r in (await _manifest()).items() if p.endswith("contrato.txt"))
        assert row.doc_id.startswith("doc_") and len(row.doc_id) > 4
        assert row.title == "contrato.txt"  # display-only file name
        assert row.page_count == 1
        assert row.doc_id == f"doc_{row.content_hash[:12]}"  # content-addressed, ASCII

    async def test_sync_persists_structure_and_content(self, client: AsyncClient, tmp_path):
        (tmp_path / "notas.md").write_text("# Título\n\n## Seção A\ntexto\n\n## Seção B\n", encoding="utf-8")
        (tmp_path / "dados.csv").write_text("mes,receita\njan,1000\n", encoding="utf-8")
        await sync_folder(str(tmp_path), USER, AGENT)
        known = await _manifest()

        md = next(r for p, r in known.items() if p.endswith("notas.md"))
        assert "Título" in [n["title"] for n in json.loads(md.structure)["structure"]]
        assert "Seção A" in json.loads(md.content)[0]["text"]

        csv = next(r for p, r in known.items() if p.endswith("dados.csv"))
        assert [c["title"] for c in json.loads(csv.structure)["structure"][0]["nodes"]] == ["mes", "receita"]
