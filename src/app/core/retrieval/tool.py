"""The agent-facing semantic retrieval tool.

Gives the Data Agent a way to search its ingested documents by meaning (not just live grep), with
each result already carrying its source so the agent can cite it. Scoped to one ``(user, agent)``.
"""

from typing import Optional

from langchain_core.tools import BaseTool, tool

from src.app.core.retrieval.embedding import Embedder, get_default_embedder
from src.app.core.retrieval.retriever import retrieve


def make_retrieval_tools(
    user_id: Optional[int], agent_id: Optional[int] = None, embedder: Optional[Embedder] = None
) -> list[BaseTool]:
    """Build the semantic-search tool bound to one user/agent. Empty list if no user."""
    if user_id is None:
        return []
    embedder = embedder or get_default_embedder()

    @tool
    async def buscar_documentos(consulta: str) -> str:
        """Busca semântica nos documentos ingeridos desta pasta/agente.

        Use para encontrar trechos por significado (não só palavra exata). Cada resultado vem com
        a fonte (documento + seção) — cite essa fonte na sua resposta.
        """
        hits = await retrieve(consulta, user_id, agent_id, embedder)
        if not hits:
            return "Nenhum documento relevante encontrado."
        return "\n\n".join(f"{h.content}\n[fonte] {h.source.render()}" for h in hits)

    return [buscar_documentos]
