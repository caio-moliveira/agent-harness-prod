"""Per-user memory tools for the Data Agent.

Gives the agent an explicit way to pull relevant context from the user's long-term
memory (mem0) — so it can "filter at the right time" instead of the user managing sessions.
"""

from typing import Optional

from langchain_core.tools import BaseTool, tool

from src.app.core.memory.memory import get_relevant_memory


def make_memory_tools(user_id: Optional[int], agent_id: Optional[int] = None) -> list[BaseTool]:
    """Build memory tools bound to a specific user and agent. Empty list if no user.

    Passing ``agent_id`` scopes retrieval to this agent so memory stays isolated per
    ``(user_id, agent_id)``.
    """
    if user_id is None:
        return []

    @tool
    async def buscar_memoria(consulta: str) -> str:
        """Busca na memória de longo prazo do usuário fatos e contexto de conversas passadas.

        Use quando a pergunta depender de algo dito antes (preferências, decisões, dados de
        conversas anteriores). Passe uma consulta curta com o que você precisa lembrar.
        """
        result = await get_relevant_memory(user_id, consulta, agent_id=agent_id)
        return result or "Nenhuma memória relevante encontrada."

    return [buscar_memoria]
