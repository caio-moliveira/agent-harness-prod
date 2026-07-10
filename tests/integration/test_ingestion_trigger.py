"""Tests for the fire-and-forget folder ingestion trigger (Phase 0: auto-ingest on folder grant).

The trigger runs ``sync_folder`` in the background so a granted folder becomes a searchable corpus
without a manual step. It must populate the manifest, release its in-flight guard, and — above all —
never let an ingestion failure escape (a failed ingest degrades to "no corpus", it can't break the
grant flow). The corpus is vectorless: parse → manifest (structure tree + located text), no embedder.
"""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


class TestIngestionTrigger:
    async def test_run_populates_manifest_and_releases_guard(self, client: AsyncClient, tmp_path):
        from src.app.core.ingestion import trigger
        from src.app.core.ingestion.source_repository import IngestedFileRepository

        (tmp_path / "lei.txt").write_text("artigo primeiro", encoding="utf-8")

        await trigger.run_folder_ingestion(1, 5, str(tmp_path))

        known = await IngestedFileRepository().get_known(1, 5)
        assert any(p.endswith("lei.txt") for p in known)
        assert not trigger.is_ingesting(1, 5)  # guard released after completion

    async def test_failure_is_swallowed(self, client: AsyncClient, tmp_path, monkeypatch):
        from src.app.core.ingestion import trigger

        async def boom(*_args, **_kwargs):
            raise RuntimeError("sync failed")

        monkeypatch.setattr(trigger, "sync_folder", boom)
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")

        # Must not raise — a failed ingestion degrades to "no corpus", never breaks the grant.
        await trigger.run_folder_ingestion(1, 6, str(tmp_path))
        assert not trigger.is_ingesting(1, 6)
