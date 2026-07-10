"""Chat-model factory: one place that builds the agents' LLMs from configuration.

The provider is chosen by ``settings.LLM_PROVIDER``. Anthropic (Claude Sonnet 5) is the default so
prompt caching is available; ``openai`` keeps the previous behavior. This module also owns the
provider-specific quirks so the agents never have to know them:

- Sonnet 5 uses **adaptive thinking** and rejects ``temperature`` / ``top_p`` / ``budget_tokens``
  (they 400). The factory never forwards sampling params to Anthropic.
- Prompt caching is a **prefix match**: stable content must precede volatile content, with a
  ``cache_control`` breakpoint on the last stable block. Deep agents get this via
  :func:`caching_middleware`; a raw ``create_agent`` graph builds a cached system block via
  :func:`build_system_message`.

Long-term memory (mem0) and the evals framework keep their own OpenAI models — this factory only
builds the agents' chat models.
"""

from typing import Optional

from langchain.agents.middleware import AgentMiddleware
from langchain_anthropic import ChatAnthropic
from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_openai import ChatOpenAI

from src.app.core.common.config import settings
from src.app.core.common.logging import logger

_ANTHROPIC = "anthropic"


def _is_anthropic() -> bool:
    """Return True when the configured provider is Anthropic."""
    return settings.LLM_PROVIDER == _ANTHROPIC


def active_model_name() -> str:
    """Return the model id the factory would build — for metrics, labels and traces."""
    return settings.ANTHROPIC_MODEL if _is_anthropic() else settings.DEFAULT_LLM_MODEL


def create_chat_model(
    model: Optional[str] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    thinking: Optional[str] = None,
) -> BaseChatModel:
    """Build the agents' chat model for the configured provider.

    ``temperature`` is honored for OpenAI only; on Anthropic (Sonnet 5) it is dropped because the
    model rejects non-default sampling parameters. ``max_tokens`` defaults per provider. ``thinking``
    ("adaptive"/"disabled") overrides ``settings.ANTHROPIC_THINKING`` for this model — e.g. a
    tool-heavy agent forces "disabled" while a light agent opts into "adaptive".
    """
    if _is_anthropic():
        if temperature is not None:
            logger.debug("anthropic_temperature_ignored", model=model or settings.ANTHROPIC_MODEL)
        anthropic_kwargs: dict = {
            "model": model or settings.ANTHROPIC_MODEL,
            "max_tokens": max_tokens or settings.ANTHROPIC_MAX_TOKENS,
            "max_retries": settings.MAX_LLM_CALL_RETRIES,
        }
        # Explicitly control thinking. Adaptive uses display="summarized" so reasoning carries text
        # (streamable to the UI, and echoable in the tool loop); the default empty-text "omitted"
        # display breaks the loop with a 400. "disabled" turns thinking off entirely.
        mode = (thinking or settings.ANTHROPIC_THINKING).lower()
        if mode == "adaptive":
            anthropic_kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
        else:
            anthropic_kwargs["thinking"] = {"type": "disabled"}
        # Only pass api_key when configured; otherwise ChatAnthropic reads ANTHROPIC_API_KEY from the
        # environment (passing None fails validation).
        if settings.ANTHROPIC_API_KEY:
            anthropic_kwargs["api_key"] = settings.ANTHROPIC_API_KEY
        return ChatAnthropic(**anthropic_kwargs)

    kwargs: dict = {"model": model or settings.DEFAULT_LLM_MODEL, "api_key": settings.OPENAI_API_KEY}
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    return ChatOpenAI(**kwargs)


def caching_middleware() -> list[AgentMiddleware]:
    """Prompt-caching middleware for a hand-built ``create_agent``, or ``[]`` when not applicable.

    ``create_deep_agent`` already bundles this middleware, so the deep agents don't need it; this
    helper is for any agent assembled directly with ``create_agent``. Only Anthropic supports the
    ``cache_control`` breakpoints it inserts; OpenAI caches matching prefixes automatically.
    """
    if not (_is_anthropic() and settings.PROMPT_CACHING_ENABLED):
        return []
    ttl = "1h" if settings.PROMPT_CACHE_TTL == "1h" else "5m"
    return [AnthropicPromptCachingMiddleware(ttl=ttl)]


def build_system_message(stable: str, volatile: str = "") -> SystemMessage:
    """Build a system message that keeps its cacheable prefix byte-stable.

    On Anthropic with caching on, ``stable`` becomes a cached text block (``cache_control``
    breakpoint) and ``volatile`` (date, long-term memory, per-turn context) follows it uncached — so
    the cache prefix never changes between turns. Otherwise a plain concatenated message is returned.
    """
    if _is_anthropic() and settings.PROMPT_CACHING_ENABLED:
        cache_control: dict = {"type": "ephemeral"}
        if settings.PROMPT_CACHE_TTL == "1h":
            cache_control["ttl"] = "1h"
        blocks: list[dict] = [{"type": "text", "text": stable, "cache_control": cache_control}]
        if volatile:
            blocks.append({"type": "text", "text": volatile})
        return SystemMessage(content=blocks)

    content = f"{stable}\n\n{volatile}" if volatile else stable
    return SystemMessage(content=content)
