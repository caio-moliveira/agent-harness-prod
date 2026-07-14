"""Unit tests for startup LLM-config validation (single-``MODEL`` surface).

Verifies fail-fast when MODEL has no provider prefix or its key is missing, the happy path, and the
embeddings-provider resolution (including the Anthropic-only memory-degrade path). No network/DB.
"""

import pytest

from src.app.core.llm import validation
from src.app.core.llm.factory import LLMConfigError


def _clear_all(monkeypatch) -> None:
    for attr in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_API_VERSION",
        "EMBEDDINGS_MODEL",
        "UTILITY_MODEL",
    ):
        monkeypatch.setattr(validation.settings, attr, "", raising=False)
    monkeypatch.setattr(validation.settings, "LONG_TERM_MEMORY_ENABLED", True, raising=False)


def test_validate_anthropic_ok(monkeypatch):
    """anthropic:... with its key passes without touching OpenAI/Azure."""
    _clear_all(monkeypatch)
    monkeypatch.setattr(validation.settings, "MODEL", "anthropic:claude-sonnet-5", raising=False)
    monkeypatch.setattr(validation.settings, "ANTHROPIC_API_KEY", "sk-ant", raising=False)
    validation.validate_llm_config()  # no raise


def test_validate_azure_ok(monkeypatch):
    """azure_openai:<deployment> with key + endpoint + version passes."""
    _clear_all(monkeypatch)
    monkeypatch.setattr(validation.settings, "MODEL", "azure_openai:gpt-5.6-terra", raising=False)
    monkeypatch.setattr(validation.settings, "AZURE_OPENAI_API_KEY", "az", raising=False)
    monkeypatch.setattr(validation.settings, "AZURE_OPENAI_ENDPOINT", "https://x.openai.azure.com", raising=False)
    monkeypatch.setattr(validation.settings, "AZURE_OPENAI_API_VERSION", "2025-01-01-preview", raising=False)
    validation.validate_llm_config()  # no raise


def test_validate_missing_key_raises(monkeypatch):
    """MODEL's provider key missing fails fast."""
    _clear_all(monkeypatch)
    monkeypatch.setattr(validation.settings, "MODEL", "anthropic:claude-sonnet-5", raising=False)
    with pytest.raises(LLMConfigError):
        validation.validate_llm_config()


def test_validate_no_prefix_raises(monkeypatch):
    """A MODEL without a 'provider:' prefix is rejected at startup."""
    _clear_all(monkeypatch)
    monkeypatch.setattr(validation.settings, "MODEL", "gpt-4o", raising=False)
    with pytest.raises(LLMConfigError):
        validation.validate_llm_config()


def test_anthropic_only_memory_degrades_no_raise(monkeypatch):
    """Anthropic-only (no embeddings key) validates fine — memory just degrades, no exception."""
    _clear_all(monkeypatch)
    monkeypatch.setattr(validation.settings, "MODEL", "anthropic:claude-sonnet-5", raising=False)
    monkeypatch.setattr(validation.settings, "ANTHROPIC_API_KEY", "sk-ant", raising=False)
    validation.validate_llm_config()  # no raise
    assert validation.resolve_embeddings_provider() == "none"


def test_resolve_embeddings_from_model_prefix(monkeypatch):
    """EMBEDDINGS_MODEL's prefix wins over key auto-detection."""
    _clear_all(monkeypatch)
    monkeypatch.setattr(validation.settings, "EMBEDDINGS_MODEL", "azure_openai:text-embedding-3-small", raising=False)
    monkeypatch.setattr(validation.settings, "OPENAI_API_KEY", "sk-openai", raising=False)
    assert validation.resolve_embeddings_provider() == "azure"


def test_resolve_embeddings_auto_openai(monkeypatch):
    """Blank EMBEDDINGS_MODEL auto-picks OpenAI when its key is present."""
    _clear_all(monkeypatch)
    monkeypatch.setattr(validation.settings, "OPENAI_API_KEY", "sk-openai", raising=False)
    assert validation.resolve_embeddings_provider() == "openai"


def test_resolve_embeddings_auto_azure(monkeypatch):
    """Blank EMBEDDINGS_MODEL falls back to Azure when only Azure is configured."""
    _clear_all(monkeypatch)
    monkeypatch.setattr(validation.settings, "AZURE_OPENAI_API_KEY", "az", raising=False)
    monkeypatch.setattr(validation.settings, "AZURE_OPENAI_ENDPOINT", "https://x.openai.azure.com", raising=False)
    assert validation.resolve_embeddings_provider() == "azure"


def test_resolve_embeddings_none(monkeypatch):
    """No embeddings-capable key resolves to 'none'."""
    _clear_all(monkeypatch)
    assert validation.resolve_embeddings_provider() == "none"


def test_embeddings_model_name_default_and_explicit(monkeypatch):
    """embeddings_model_name uses the spec's model, else a per-provider default."""
    _clear_all(monkeypatch)
    monkeypatch.setattr(validation.settings, "AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "emb-deploy", raising=False)
    assert validation.embeddings_model_name("openai") == "text-embedding-3-small"
    assert validation.embeddings_model_name("azure") == "emb-deploy"
    monkeypatch.setattr(validation.settings, "EMBEDDINGS_MODEL", "openai:text-embedding-3-large", raising=False)
    assert validation.embeddings_model_name("openai") == "text-embedding-3-large"
