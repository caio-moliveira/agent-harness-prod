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
        "OPENAI_BASE_URL",
    ):
        monkeypatch.setattr(validation.settings, attr, "", raising=False)
    monkeypatch.setattr(validation.settings, "EMBEDDINGS_DIMS", 0, raising=False)
    monkeypatch.setattr(validation.settings, "OLLAMA_BASE_URL", "http://localhost:11434", raising=False)
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


def test_validate_ollama_ok_without_any_key(monkeypatch):
    """ollama:... validates with zero API keys configured (local key-less provider)."""
    _clear_all(monkeypatch)
    monkeypatch.setattr(validation.settings, "MODEL", "ollama:llama3.3", raising=False)
    validation.validate_llm_config()  # no raise


def test_validate_openai_compatible_server_without_key(monkeypatch):
    """openai:... + OPENAI_BASE_URL validates key-less (vLLM/LM Studio/LiteLLM-proxy servers)."""
    _clear_all(monkeypatch)
    monkeypatch.setattr(validation.settings, "MODEL", "openai:qwen2.5-coder", raising=False)
    monkeypatch.setattr(validation.settings, "OPENAI_BASE_URL", "http://localhost:8080/v1", raising=False)
    validation.validate_llm_config()  # no raise


def test_validate_unknown_provider_fails_fast_on_build(monkeypatch):
    """A provider outside the key map isn't rejected upfront but fails fast when unbuildable.

    Here the build fails because the langchain-groq integration package is not installed.
    """
    _clear_all(monkeypatch)
    monkeypatch.setattr(validation.settings, "MODEL", "groq:llama-3.3-70b-versatile", raising=False)
    with pytest.raises(LLMConfigError):
        validation.validate_llm_config()


def test_resolve_embeddings_ollama_explicit_only(monkeypatch):
    """Ollama embeddings resolve from an explicit EMBEDDINGS_MODEL, never from auto-detection."""
    _clear_all(monkeypatch)
    monkeypatch.setattr(validation.settings, "EMBEDDINGS_MODEL", "ollama:nomic-embed-text", raising=False)
    assert validation.resolve_embeddings_provider() == "ollama"
    monkeypatch.setattr(validation.settings, "EMBEDDINGS_MODEL", "", raising=False)
    assert validation.resolve_embeddings_provider() == "none"  # no auto-pick of a local server


def test_embeddings_dims_defaults_and_override(monkeypatch):
    """Dims follow the provider default (1536 openai/azure, 768 ollama) unless EMBEDDINGS_DIMS is set."""
    _clear_all(monkeypatch)
    assert validation.embeddings_dims("openai") == 1536
    assert validation.embeddings_dims("azure") == 1536
    assert validation.embeddings_dims("ollama") == 768
    monkeypatch.setattr(validation.settings, "EMBEDDINGS_DIMS", 1024, raising=False)
    assert validation.embeddings_dims("ollama") == 1024


def test_embeddings_model_name_default_and_explicit(monkeypatch):
    """embeddings_model_name uses the spec's model, else a per-provider default."""
    _clear_all(monkeypatch)
    monkeypatch.setattr(validation.settings, "AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "emb-deploy", raising=False)
    assert validation.embeddings_model_name("openai") == "text-embedding-3-small"
    assert validation.embeddings_model_name("azure") == "emb-deploy"
    assert validation.embeddings_model_name("ollama") == "nomic-embed-text"
    monkeypatch.setattr(validation.settings, "EMBEDDINGS_MODEL", "openai:text-embedding-3-large", raising=False)
    assert validation.embeddings_model_name("openai") == "text-embedding-3-large"
