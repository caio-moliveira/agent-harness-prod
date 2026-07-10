"""Web search tool provider.

Default is Tavily (LLM-oriented search: relevant snippets + source URLs), exposed as a
``web_search(queries)`` tool that runs several queries in parallel. Provider-native options
(Anthropic/OpenAI server-side web search) are also available for agents that bind them directly.
"""

import asyncio
import logging
from enum import Enum
from typing import List

from langchain_core.tools import tool

from src.app.core.common.config import settings

_tavily = None

# Cap queries per web_search call so one call can't fan out to many Tavily requests. Combined with
# the researcher's small tool-call budget (open_deep_research/config.py), this bounds total searches.
_MAX_QUERIES_PER_CALL = 2


class SearchAPI(Enum):
    """Enumeration of available search API providers."""

    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    TAVILY = "tavily"
    NONE = "none"


def _get_tavily():
    """Lazily build the Tavily search tool (reads TAVILY_API_KEY from settings/env)."""
    global _tavily
    if _tavily is None:
        from langchain_tavily import TavilySearch

        kwargs = {"max_results": 3}
        if settings.TAVILY_API_KEY:
            kwargs["tavily_api_key"] = settings.TAVILY_API_KEY
        _tavily = TavilySearch(**kwargs)
    return _tavily


def _format_result(result) -> str:
    """Render one Tavily result payload (dict with a ``results`` list) to a compact string."""
    if isinstance(result, dict) and isinstance(result.get("results"), list):
        items = result["results"]
        if not items:
            return "(sem resultados)"
        return "\n".join(
            f"- {it.get('title', '')} ({it.get('url', '')})\n  {(it.get('content') or '')[:300]}"
            for it in items
        )
    return str(result)[:1500]


WEB_SEARCH_DESCRIPTION = (
    "Busca na web (Tavily) — devolve resultados relevantes com título, trecho e URL. Passe uma lista "
    "de consultas curtas e específicas. Use para embasar recomendações com fontes; cite a URL."
)


@tool(description=WEB_SEARCH_DESCRIPTION)
async def web_search(queries: List[str]) -> str:
    """Run web searches (Tavily) for each query in parallel and return formatted results + sources."""
    if len(queries) > _MAX_QUERIES_PER_CALL:
        logging.info("web_search_queries_capped", extra={"asked": len(queries), "kept": _MAX_QUERIES_PER_CALL})
        queries = queries[:_MAX_QUERIES_PER_CALL]
    tavily = _get_tavily()
    tasks = [tavily.ainvoke({"query": q}) for q in queries]
    try:
        results = await asyncio.gather(*tasks)
    except Exception:
        logging.warning("web_search_failed", exc_info=True)
        return "Busca web falhou. Tente consultas diferentes."

    output = "Resultados da busca web (Tavily):\n\n"
    for i, (query, result) in enumerate(zip(queries, results, strict=False)):
        output += f"--- CONSULTA {i + 1}: {query} ---\n{_format_result(result)}\n" + "-" * 60 + "\n\n"
    return output


def get_search_tool(search_api: SearchAPI):
    """Return the search tool(s) for the given provider.

    Args:
        search_api: The search API provider (Tavily, Anthropic native, OpenAI native, or None).

    Returns:
        A list of tool objects (or provider-native tool dicts) to bind to the agent.
    """
    if search_api == SearchAPI.ANTHROPIC:
        # Anthropic's native server-side web search.
        return [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}]
    if search_api == SearchAPI.OPENAI:
        # OpenAI's native web search preview.
        return [{"type": "web_search_preview"}]
    if search_api == SearchAPI.TAVILY:
        return [web_search]
    return []
