"""LangGraph tools for enhanced language model capabilities.

This package contains custom tools that can be used with LangGraph to extend
the capabilities of language models. Currently includes a web search tool
(Tavily) and other external integrations.
"""

from langchain_core.tools.base import BaseTool

from src.app.agents.tools.search_tool import web_search

tools: list[BaseTool] = [web_search]
