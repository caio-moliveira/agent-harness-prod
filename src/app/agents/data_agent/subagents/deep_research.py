"""``deep_research`` subagent: multi-source web research delegated by the Data Agent.

Wraps the ``open_deep_research`` supervisor/researcher graph as a deepagents
``CompiledSubAgent``. The Data Agent delegates to it via ``task()``; the pipeline runs its
parallel web searches in an isolated context and only the final cited report returns to the
parent (deepagents extracts the runnable's last message), keeping the parent's context clean.

The graph is **session-independent** (it manages its own models and tools), so it is compiled
once and shared across sessions — the async compile happens off the per-session build path and
the resulting runnable is injected into the Data Agent. Clarification is disabled: as a delegated
subagent there is no user in the loop to answer a clarifying question, so it goes straight to
research. It internally uses the Tavily search tool (which is why Tavily stays in the codebase).
"""

from typing import Any, Optional

from langchain_core.runnables import Runnable

from src.app.agents.open_deep_research.agent_deep_research import DeepResearchAgent
from src.app.core.common.config import settings
from src.app.core.common.logging import logger

SUBAGENT_NAME = "deep_research"

_DESCRIPTION = (
    "Pesquisa aprofundada na WEB (múltiplas fontes, com verificação e citações). Delegue a este "
    "subagente perguntas que exigem informação EXTERNA/atual que não está nos documentos nem no "
    "banco do usuário — panorama de mercado, notícias, comparativos, fatos recentes, embasamento "
    "com fontes. Passe a pergunta COMPLETA e diga o que deve constar no relatório. É stateless e "
    "pode levar dezenas de segundos (roda vários pesquisadores em paralelo); devolve um relatório "
    "citado. Não use para perguntas simples respondíveis sem a web."
)

# Compiled once and reused (the graph does not depend on session/user/agent).
_runnable: Optional[Runnable] = None
_compiled = False


async def get_deep_research_subagent_runnable() -> Optional[Runnable]:
    """Compile (once) and return the deep-research graph as a runnable, or None if unavailable.

    Returns None — with a clear warning, not an exception — when ``OPENAI_API_KEY`` is missing, so
    a session without the key simply runs without the web-research subagent instead of crashing the
    whole Data Agent build (the pinned research models are ``openai:gpt-4.1``).
    """
    global _runnable, _compiled
    if _compiled:
        return _runnable
    _compiled = True
    if not settings.OPENAI_API_KEY:
        logger.warning("deep_research_subagent_unavailable", reason="missing_openai_api_key")
        _runnable = None
        return None
    # No checkpointer: a single-shot delegated run needs no persistence, and an ephemeral graph
    # can never collide with the parent turn's checkpointer. Clarification off (no user in loop).
    agent = DeepResearchAgent("Deep Research (subagent)", checkpointer=None, allow_clarification=False)
    _runnable = await agent.compile()
    logger.info("deep_research_subagent_compiled")
    return _runnable


def make_deep_research_subagent_spec(runnable: Runnable) -> dict[str, Any]:
    """Build the deepagents ``CompiledSubAgent`` spec around a compiled deep-research runnable.

    Args:
        runnable: The compiled deep-research graph (its state schema includes ``messages``, as
            deepagents requires to return the subagent's final message to the parent).

    Returns:
        A ``CompiledSubAgent`` spec (name/description/runnable) for ``create_deep_agent(subagents)``.
    """
    return {
        "name": SUBAGENT_NAME,
        "description": _DESCRIPTION,
        "runnable": runnable,
    }
