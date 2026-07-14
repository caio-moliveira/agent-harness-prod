"""Unit tests for the thin, single-``MODEL`` LLM factory.

Pure unit tests: they monkeypatch the ``settings`` singleton the factory reads and assert the right
client class is built per ``provider:model`` prefix, that Azure threads endpoint/version, and that the
provider quirks (Anthropic drops temperature + always gets max_tokens) are applied. No network or DB.
"""

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_openai import AzureChatOpenAI, ChatOpenAI

from src.app.core.llm import factory


@pytest.fixture()
def all_keys(monkeypatch):
    """Configure every provider's key + Azure endpoint/version so no builder short-circuits."""
    monkeypatch.setattr(factory.settings, "OPENAI_API_KEY", "sk-openai-test", raising=False)
    monkeypatch.setattr(factory.settings, "ANTHROPIC_API_KEY", "sk-ant-test", raising=False)
    monkeypatch.setattr(factory.settings, "AZURE_OPENAI_API_KEY", "az-test", raising=False)
    monkeypatch.setattr(factory.settings, "AZURE_OPENAI_ENDPOINT", "https://x.openai.azure.com", raising=False)
    monkeypatch.setattr(factory.settings, "AZURE_OPENAI_API_VERSION", "2025-01-01-preview", raising=False)
    monkeypatch.setattr(factory.settings, "MODEL_MAX_TOKENS", 8192, raising=False)


def _set_model(monkeypatch, spec: str) -> None:
    monkeypatch.setattr(factory.settings, "MODEL", spec, raising=False)


def test_build_anthropic(all_keys, monkeypatch):
    """MODEL=anthropic:... builds a ChatAnthropic."""
    _set_model(monkeypatch, "anthropic:claude-sonnet-5")
    assert isinstance(factory.create_chat_model(), ChatAnthropic)


def test_build_openai(all_keys, monkeypatch):
    """MODEL=openai:... builds a plain ChatOpenAI (not the Azure subclass)."""
    _set_model(monkeypatch, "openai:gpt-4o")
    model = factory.create_chat_model()
    assert isinstance(model, ChatOpenAI) and not isinstance(model, AzureChatOpenAI)


def test_build_azure(all_keys, monkeypatch):
    """MODEL=azure_openai:<deployment> builds an AzureChatOpenAI from the Azure settings."""
    _set_model(monkeypatch, "azure_openai:gpt-5.6-terra")
    assert isinstance(factory.create_chat_model(), AzureChatOpenAI)


def test_provider_and_key_resolution(all_keys, monkeypatch):
    """provider_of + api_key_for read the provider prefix and its key from settings."""
    assert factory.provider_of("azure_openai:x") == "azure_openai"
    assert factory.provider_of("bare-model") == ""
    _set_model(monkeypatch, "anthropic:claude-sonnet-5")
    assert factory.api_key_for(factory.settings.MODEL) == "sk-ant-test"


def test_anthropic_drops_temperature_and_sets_max_tokens():
    """Anthropic quirk: temperature is never forwarded; max_tokens is always present."""
    kwargs = factory._build_kwargs("anthropic:claude-sonnet-5", max_tokens=None, temperature=0.5)
    assert "temperature" not in kwargs
    assert kwargs["max_tokens"] == factory.settings.MODEL_MAX_TOKENS


def test_openai_keeps_temperature_and_azure_threads_endpoint(all_keys):
    """OpenAI forwards temperature; Azure adds endpoint + api_version."""
    openai_kwargs = factory._build_kwargs("openai:gpt-4o", max_tokens=1234, temperature=0.3)
    assert openai_kwargs["temperature"] == 0.3 and openai_kwargs["max_tokens"] == 1234
    azure_kwargs = factory._build_kwargs("azure_openai:dep", max_tokens=None, temperature=None)
    assert azure_kwargs["azure_endpoint"] == "https://x.openai.azure.com"
    assert azure_kwargs["api_version"] == "2025-01-01-preview"


def test_utility_reuses_model_when_blank(all_keys, monkeypatch):
    """UTILITY_MODEL blank → the utility model is built from MODEL."""
    _set_model(monkeypatch, "anthropic:claude-sonnet-5")
    monkeypatch.setattr(factory.settings, "UTILITY_MODEL", "", raising=False)
    assert isinstance(factory.create_utility_chat_model(), ChatAnthropic)


def test_utility_overrides_model(all_keys, monkeypatch):
    """UTILITY_MODEL set → the utility model follows it (Anthropic chat + OpenAI utility)."""
    _set_model(monkeypatch, "anthropic:claude-sonnet-5")
    monkeypatch.setattr(factory.settings, "UTILITY_MODEL", "openai:gpt-5-nano", raising=False)
    model = factory.create_utility_chat_model()
    assert isinstance(model, ChatOpenAI) and not isinstance(model, AzureChatOpenAI)


def test_active_model_name_is_the_model_string(monkeypatch):
    """active_model_name returns the configured provider:model string for traces."""
    _set_model(monkeypatch, "azure_openai:gpt-5.6-terra")
    assert factory.active_model_name() == "azure_openai:gpt-5.6-terra"
