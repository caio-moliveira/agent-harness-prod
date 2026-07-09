"""Per-user memory tools for the Data Agent (two-tier experience memory, #23).

``buscar_memoria`` scans the tier-1 summary index (what the agent already did/decided) and returns
each hit with its id; ``ler_memoria`` reads a hit's full body on demand (tier 2). This is how the
agent avoids redoing work across sessions. mem0 conversational recall is kept as a fallback for
"what was said before" when there's no structured memory hit.
"""

import json
from typing import Optional

from langchain_core.tools import BaseTool, tool

from src.app.core.memory.agent_memory_service import search_memory
from src.app.core.memory.agent_memory_repository import AgentMemoryRepository
from src.app.core.memory.memory import get_relevant_memory

_memory_repo = AgentMemoryRepository()


def make_memory_tools(user_id: Optional[int], agent_id: Optional[int] = None) -> list[BaseTool]:
    """Build the two-tier memory tools bound to a specific user and agent. Empty list if no user.

    Retrieval is isolated per ``(user_id, agent_id)`` so an agent never sees another's memory.
    """
    if user_id is None:
        return []

    @tool
    async def buscar_memoria(consulta: str) -> str:
        """Busca no que você JÁ FEZ e DECIDIU antes (entregáveis gerados, conclusões) — memória de experiência.

        Use ANTES de planejar ou gerar algo, para não refazer trabalho de sessões anteriores. Devolve
        cada resultado com um `id`; se um resultado for relevante, chame `ler_memoria(id)` para ver os
        detalhes completos (números, caminho do arquivo gerado, decisões).
        """
        hits = await search_memory(user_id, agent_id, consulta, k=5)
        if hits:
            lines = [f"[mem {h.memory.id}] ({h.memory.kind}) {h.memory.summary}" for h in hits]
            return (
                "Memória de experiência (use `ler_memoria(id)` para os detalhes de um item):\n"
                + "\n".join(lines)
            )
        # No structured outcome yet — fall back to mem0 conversational recall.
        recall = await get_relevant_memory(user_id, consulta, agent_id=agent_id)
        return recall or "Nenhuma memória relevante encontrada."

    @tool
    async def ler_memoria(id: int) -> str:
        """Lê os detalhes completos de uma memória de experiência pelo `id` (de `buscar_memoria`).

        Devolve o corpo (números, decisões) e as referências (ex.: o caminho do artefato já gerado),
        para você reaproveitar em vez de refazer.
        """
        row = await _memory_repo.get_by_id(id, user_id, agent_id)
        if row is None:
            return f"Memória {id} não encontrada."
        parts = [f"({row.kind}) {row.summary}"]
        if row.body:
            parts.append("Detalhes: " + json.dumps(row.body, ensure_ascii=False))
        if row.refs:
            parts.append("Referências: " + json.dumps(row.refs, ensure_ascii=False))
        return "\n".join(parts)

    return [buscar_memoria, ler_memoria]
