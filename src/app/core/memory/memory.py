"""Long-term memory management using mem0 and pgvector.

This module provides functions for managing long-term memory operations including
initialization, search, and updates using the mem0 library with PostgreSQL/pgvector backend.
"""
import asyncio
from typing import Optional

from mem0 import AsyncMemory

from src.app.core.common.config import settings
from src.app.core.common.logging import logger
from src.app.core.llm.validation import embeddings_model_name, resolve_embeddings_provider

# Module-level singleton for memory instance
_memory_instance: Optional[AsyncMemory] = None
# Set once if the memory backend fails to initialize (e.g. the mem0 Azure embedder needs the optional
# ``azure-identity`` package). We then disable memory for the process instead of retrying and logging a
# traceback every turn — the agent keeps working, just without long-term memory. Provider-agnostic.
_memory_unavailable = False


def long_term_memory_enabled() -> bool:
    """True when long-term memory is switched on AND an embeddings provider is available.

    Anthropic has no embedding model, so an Anthropic-only deployment resolves to ``none`` and memory
    is disabled — the read/write helpers below then no-op instead of failing on a missing OpenAI key.
    Also returns False once the backend has failed to initialize (see ``_memory_unavailable``).
    """
    if _memory_unavailable:
        return False
    return settings.LONG_TERM_MEMORY_ENABLED and resolve_embeddings_provider() != "none"


def _azure_common() -> dict:
    """Shared Azure connection kwargs for mem0's Azure llm/embedder configs."""
    return {
        "api_key": settings.AZURE_OPENAI_API_KEY,
        "azure_endpoint": settings.AZURE_OPENAI_ENDPOINT,
        "api_version": settings.AZURE_OPENAI_API_VERSION,
    }


def _mem0_llm_config() -> dict:
    """mem0's fact-extraction LLM, derived from the agent's UTILITY_MODEL (→ MODEL) 'provider:model'.

    This is the low-stakes "librarian" that distills a conversation into memory entries — it reuses the
    utility model rather than a memory-specific one. Maps our provider prefix to mem0's provider name.
    """
    spec = settings.UTILITY_MODEL or settings.MODEL
    provider, _, model = spec.partition(":")
    provider = provider.lower()
    if provider in ("azure_openai", "azure"):
        return {
            "provider": "azure_openai",
            "config": {"model": model, "azure_kwargs": {**_azure_common(), "azure_deployment": model}},
        }
    if provider == "anthropic":
        return {"provider": "anthropic", "config": {"model": model}}
    return {"provider": "openai", "config": {"model": model or spec}}


def _mem0_embedder_config(provider: str) -> dict:
    """mem0's embedder for the resolved embeddings provider (``openai`` or ``azure``)."""
    model = embeddings_model_name(provider)
    if provider == "azure":
        return {
            "provider": "azure_openai",
            "config": {
                "model": model,
                "azure_kwargs": {**_azure_common(), "azure_deployment": settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT},
            },
        }
    return {"provider": "openai", "config": {"model": model}}


def _mem0_config() -> dict:
    """Build the mem0 config: pgvector store + the extraction LLM + the resolved embedder."""
    vector_store = {
        "provider": "pgvector",
        "config": {
            "collection_name": settings.LONG_TERM_MEMORY_COLLECTION_NAME,
            "dbname": settings.POSTGRES_DB,
            "user": settings.POSTGRES_USER,
            "password": settings.POSTGRES_PASSWORD,
            "host": settings.POSTGRES_HOST,
            "port": settings.POSTGRES_PORT,
        },
    }
    return {
        "vector_store": vector_store,
        "llm": _mem0_llm_config(),
        "embedder": _mem0_embedder_config(resolve_embeddings_provider()),
    }


async def get_memory_instance() -> AsyncMemory:
    """Initialize and return the long-term memory singleton.

    Returns:
        AsyncMemory: The initialized memory instance.
    """
    global _memory_instance, _memory_unavailable
    if _memory_instance is None:
        try:
            _memory_instance = await AsyncMemory.from_config(config_dict=_mem0_config())
        except Exception as e:
            # Disable memory for the rest of the process so we don't retry + log every turn. The most
            # common cause is the mem0 Azure embedder importing the optional ``azure-identity`` package
            # (install it, or set EMBEDDINGS_PROVIDER=openai / LONG_TERM_MEMORY_ENABLED=false).
            _memory_unavailable = True
            logger.warning(
                "long_term_memory_unavailable_disabled",
                error=str(e),
                embeddings_provider=resolve_embeddings_provider(),
            )
            raise
    return _memory_instance


async def get_relevant_memory(user_id: int, query: str, agent_id: Optional[int] = None) -> str:
    """Get relevant memories for a user, partitioned by agent when given.

    Memory is isolated per ``(user_id, agent_id)``: passing ``agent_id`` scopes retrieval to
    that agent so facts learned by one agent are never surfaced to another. When ``agent_id``
    is None the retrieval is user-scoped only (used by agents without an agent identity).

    Args:
        user_id: The user ID to search memories for.
        query: The query to search for relevant memories.
        agent_id: Optional agent scope for per-agent isolation.

    Returns:
        str: Formatted string of relevant memories, or empty string on error/when disabled.
    """
    if not long_term_memory_enabled():
        return ""
    try:
        memory = await get_memory_instance()
        kwargs = {"user_id": str(user_id), "query": query}
        if agent_id is not None:
            kwargs["agent_id"] = str(agent_id)
        results = await memory.search(**kwargs)
        memory_result = "\n".join([f"* {result['memory']}" for result in results["results"]])
        logger.debug("retrieved_relevant_memory", memory=memory_result)
        return memory_result
    except Exception as e:
        logger.error("failed_to_get_relevant_memory", error=str(e), user_id=user_id, query=query)
        return ""


async def update_memory(
    user_id: int, messages: list[dict], metadata: dict = None, agent_id: Optional[int] = None
) -> None:
    """Update long-term memory with new messages, partitioned by agent when given.

    Args:
        user_id: The user ID to update memory for.
        messages: The messages to add to memory.
        metadata: Optional metadata to include with the memory update.
        agent_id: Optional agent scope for per-agent isolation.
    """
    if not long_term_memory_enabled():
        return
    try:
        # Only the USER's turns become durable memory — never the assistant's transient statements
        # (e.g. "não encontrei" / "não tenho documentos"), which otherwise get stored as "facts" and
        # then replayed every turn, making the agent give up before even calling its tools. This is
        # mem0's own recommended user-only extraction pattern.
        user_messages = [
            m
            for m in messages
            if isinstance(m, dict) and m.get("role") == "user" and isinstance(m.get("content"), str) and m["content"].strip()
        ]
        if not user_messages:
            return
        memory = await get_memory_instance()
        kwargs = {"user_id": str(user_id), "metadata": metadata}
        if agent_id is not None:
            kwargs["agent_id"] = str(agent_id)
        await memory.add(user_messages, **kwargs)
        logger.info("long_term_memory_updated_successfully", user_id=user_id, agent_id=agent_id)
    except Exception as e:
        logger.exception(
            "failed_to_update_long_term_memory",
            user_id=user_id,
            error=str(e),
        )

def bg_update_memory(
    user_id: int, messages: list[dict], metadata: dict = None, agent_id: Optional[int] = None
) -> None:
    """Run memory update in background without blocking the response.

    Args:
        user_id: The user ID to update memory for.
        messages: The messages to add to memory.
        metadata: Optional metadata to include with the memory update.
        agent_id: Optional agent scope for per-agent isolation.
    """
    if not long_term_memory_enabled():
        return
    asyncio.create_task(
        update_memory(user_id, messages, metadata, agent_id=agent_id)
    )

