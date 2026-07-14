"""Unit tests for long-term memory gating + mem0 config under the single-``MODEL`` surface.

No DB/network: exercises ``long_term_memory_enabled()`` and ``_mem0_config()`` under monkeypatched
settings for the openai, azure, and disabled (Anthropic-only) cases. The mem0 extraction LLM now
follows ``UTILITY_MODEL`` (→ ``MODEL``); the embedder follows ``EMBEDDINGS_MODEL``.
"""

import pytest

from src.app.core.memory import memory


def _reset(monkeypatch) -> None:
    for attr in (
        "OPENAI_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_API_VERSION",
        "AZURE_OPENAI_EMBEDDING_DEPLOYMENT",
        "EMBEDDINGS_MODEL",
        "UTILITY_MODEL",
    ):
        monkeypatch.setattr(memory.settings, attr, "", raising=False)
    monkeypatch.setattr(memory, "_memory_unavailable", False, raising=False)
    monkeypatch.setattr(memory.settings, "LONG_TERM_MEMORY_ENABLED", True, raising=False)
    monkeypatch.setattr(memory.settings, "MODEL", "anthropic:claude-sonnet-5", raising=False)


def test_memory_enabled_with_openai(monkeypatch):
    """OpenAI key present + memory on → enabled."""
    _reset(monkeypatch)
    monkeypatch.setattr(memory.settings, "OPENAI_API_KEY", "sk-openai", raising=False)
    assert memory.long_term_memory_enabled() is True


def test_memory_disabled_when_no_embeddings(monkeypatch):
    """Anthropic-only (no openai/azure key) → embeddings 'none' → memory disabled."""
    _reset(monkeypatch)
    assert memory.long_term_memory_enabled() is False


def test_memory_disabled_by_flag(monkeypatch):
    """Explicit LONG_TERM_MEMORY_ENABLED=false wins even with an embeddings key."""
    _reset(monkeypatch)
    monkeypatch.setattr(memory.settings, "OPENAI_API_KEY", "sk-openai", raising=False)
    monkeypatch.setattr(memory.settings, "LONG_TERM_MEMORY_ENABLED", False, raising=False)
    assert memory.long_term_memory_enabled() is False


def test_mem0_config_openai(monkeypatch):
    """OpenAI embeddings + an openai utility model → mem0 llm/embedder use the 'openai' provider."""
    _reset(monkeypatch)
    monkeypatch.setattr(memory.settings, "OPENAI_API_KEY", "sk-openai", raising=False)
    monkeypatch.setattr(memory.settings, "UTILITY_MODEL", "openai:gpt-5-nano", raising=False)
    cfg = memory._mem0_config()
    assert cfg["llm"]["provider"] == "openai"
    assert cfg["llm"]["config"]["model"] == "gpt-5-nano"
    assert cfg["embedder"]["provider"] == "openai"
    assert cfg["embedder"]["config"]["model"] == "text-embedding-3-small"
    assert cfg["vector_store"]["provider"] == "pgvector"


def test_mem0_config_azure(monkeypatch):
    """Azure embeddings + an azure utility model → mem0 uses 'azure_openai' with azure_kwargs."""
    _reset(monkeypatch)
    monkeypatch.setattr(memory.settings, "EMBEDDINGS_MODEL", "azure_openai:text-embedding-3-small", raising=False)
    monkeypatch.setattr(memory.settings, "UTILITY_MODEL", "azure_openai:gpt-5.4-mini", raising=False)
    monkeypatch.setattr(memory.settings, "AZURE_OPENAI_API_KEY", "az", raising=False)
    monkeypatch.setattr(memory.settings, "AZURE_OPENAI_ENDPOINT", "https://x.openai.azure.com", raising=False)
    monkeypatch.setattr(memory.settings, "AZURE_OPENAI_API_VERSION", "2025-01-01-preview", raising=False)
    monkeypatch.setattr(memory.settings, "AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "embed-deploy", raising=False)
    cfg = memory._mem0_config()
    assert cfg["llm"]["provider"] == "azure_openai"
    assert cfg["llm"]["config"]["azure_kwargs"]["azure_deployment"] == "gpt-5.4-mini"
    assert cfg["embedder"]["provider"] == "azure_openai"
    embed_kwargs = cfg["embedder"]["config"]["azure_kwargs"]
    assert embed_kwargs["azure_deployment"] == "embed-deploy"
    assert embed_kwargs["azure_endpoint"] == "https://x.openai.azure.com"
    assert embed_kwargs["api_version"] == "2025-01-01-preview"
    assert embed_kwargs["api_key"] == "az"


@pytest.mark.asyncio
async def test_read_write_noop_when_disabled(monkeypatch):
    """When memory is disabled, read returns '' and write no-ops without initializing mem0."""
    _reset(monkeypatch)  # no embeddings key → disabled

    called = {"init": False}

    async def _boom():
        called["init"] = True
        raise AssertionError("get_memory_instance must not be called when memory is disabled")

    monkeypatch.setattr(memory, "get_memory_instance", _boom)
    assert await memory.get_relevant_memory(user_id=1, query="hi") == ""
    await memory.update_memory(user_id=1, messages=[{"role": "user", "content": "hi"}])
    assert called["init"] is False
