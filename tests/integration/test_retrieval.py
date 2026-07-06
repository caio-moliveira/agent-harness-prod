"""Integration tests for semantic retrieval (#14): index -> retrieve, scoped per agent.

Seams:
  1. ``index_chunks`` — embeds a (user, agent)'s pending chunks; skips empty ones.
  2. ``retrieve`` — returns the most similar chunks with ``Source`` provenance, isolated per agent.
  3. ``make_retrieval_tools`` — the agent-facing tool renders hits with their source.

A deterministic keyword-vector ``FakeEmbedder`` stands in for OpenAI so cosine ranking is exact and
hermetic (no network). End-to-end path exercised: ingest_folder (#13) -> index_chunks -> retrieve.
"""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


class FakeEmbedder:
    """Maps text to a keyword-count vector — similar texts get similar vectors, deterministically."""

    VOCAB = ["vendas", "receita", "contrato", "prazo", "cliente"]

    def _vec(self, text: str):
        low = (text or "").lower()
        return [float(low.count(word)) for word in self.VOCAB]

    async def embed_documents(self, texts):
        return [self._vec(t) for t in texts]

    async def embed_query(self, text):
        return self._vec(text)


async def _ingest_two_docs(tmp_path, user_id, agent_id):
    from src.app.core.ingestion import DocumentChunkRepository, ingest_folder

    (tmp_path / "vendas.txt").write_text(
        "Relatório de vendas e receita por cliente no trimestre.", encoding="utf-8"
    )
    (tmp_path / "contrato.txt").write_text(
        "Contrato de prestação com prazo de doze meses.", encoding="utf-8"
    )
    repo = DocumentChunkRepository()
    await ingest_folder(str(tmp_path), user_id=user_id, agent_id=agent_id, repo=repo)
    return repo


class TestIndexing:
    async def test_index_embeds_pending_chunks(self, client: AsyncClient, tmp_path):
        from src.app.core.retrieval import index_chunks

        repo = await _ingest_two_docs(tmp_path, user_id=1, agent_id=7)
        before = await repo.get_embedded_chunks(1, 7)
        assert before == []

        n = await index_chunks(1, 7, FakeEmbedder(), repo=repo)
        assert n >= 2
        after = await repo.get_embedded_chunks(1, 7)
        assert len(after) == n
        assert all(c.embedding for c in after)


class TestRetrieve:
    async def test_returns_most_similar_with_provenance(self, client: AsyncClient, tmp_path):
        from src.app.core.retrieval import index_chunks, retrieve

        repo = await _ingest_two_docs(tmp_path, user_id=1, agent_id=7)
        await index_chunks(1, 7, FakeEmbedder(), repo=repo)

        hits = await retrieve("vendas e receita", 1, 7, FakeEmbedder(), repo=repo, k=1)
        assert len(hits) == 1
        top = hits[0]
        assert "vendas" in top.content.lower()
        assert top.source.kind == "doc_chunk"
        assert top.source.document.endswith("vendas.txt")
        assert "vendas.txt" in top.source.render()

    async def test_scoped_per_agent_and_user(self, client: AsyncClient, tmp_path):
        from src.app.core.retrieval import index_chunks, retrieve

        repo = await _ingest_two_docs(tmp_path, user_id=1, agent_id=7)
        await index_chunks(1, 7, FakeEmbedder(), repo=repo)

        assert await retrieve("vendas", 1, 8, FakeEmbedder(), repo=repo) == []  # other agent
        assert await retrieve("vendas", 2, 7, FakeEmbedder(), repo=repo) == []  # other user

    async def test_empty_corpus_returns_nothing(self, client: AsyncClient, tmp_path):
        from src.app.core.retrieval import retrieve

        assert await retrieve("qualquer", 1, 7, FakeEmbedder()) == []


class TestRetrievalTool:
    async def test_tool_renders_hits_with_source(self, client: AsyncClient, tmp_path):
        from src.app.core.retrieval import index_chunks, make_retrieval_tools

        repo = await _ingest_two_docs(tmp_path, user_id=1, agent_id=7)
        await index_chunks(1, 7, FakeEmbedder(), repo=repo)

        tools = make_retrieval_tools(user_id=1, agent_id=7, embedder=FakeEmbedder())
        assert len(tools) == 1
        out = await tools[0].ainvoke({"consulta": "vendas e receita"})
        assert "vendas" in out.lower()
        assert "[fonte]" in out

    def test_no_user_no_tool(self):
        from src.app.core.retrieval import make_retrieval_tools

        assert make_retrieval_tools(user_id=None) == []
