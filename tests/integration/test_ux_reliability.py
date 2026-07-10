"""Tests for the reliability/UX fixes: memory hygiene and corpus self-heal.

These target the exact causes of the frontend's erratic behavior: durable memory was poisoned with
the assistant's "no documents" statements (making the agent give up before calling tools), and a
session opened on a stale corpus never repaired itself.
"""

import asyncio

import pytest

pytestmark = pytest.mark.asyncio


class _FakeMem:
    """Captures what would be sent to mem0.add without any network call."""

    def __init__(self):
        self.added = None

    async def add(self, messages, **kwargs):
        self.added = (messages, kwargs)


class TestMemoryHygiene:
    async def test_only_user_messages_are_persisted(self, monkeypatch):
        from src.app.core.memory import memory as memmod

        fake = _FakeMem()

        async def fake_get():
            return fake

        monkeypatch.setattr(memmod, "get_memory_instance", fake_get)
        await memmod.update_memory(
            2,
            [
                {"role": "user", "content": "em qual página está a EC 100?"},
                {"role": "assistant", "content": "Não tenho acesso a nenhum documento indexado."},
            ],
            agent_id=4,
        )
        assert fake.added is not None
        persisted = fake.added[0]
        assert persisted and all(m["role"] == "user" for m in persisted)
        # The poisoning statement (an assistant line) must never reach memory.
        assert not any("tenho acesso" in m["content"].lower() for m in persisted)

    async def test_turn_without_user_content_persists_nothing(self, monkeypatch):
        from src.app.core.memory import memory as memmod

        fake = _FakeMem()

        async def fake_get():
            return fake

        monkeypatch.setattr(memmod, "get_memory_instance", fake_get)
        await memmod.update_memory(2, [{"role": "assistant", "content": "só o assistente falou"}], agent_id=4)
        assert fake.added is None  # nothing to remember → no write
