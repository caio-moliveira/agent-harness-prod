"""Unit tests for DataAgent._compose_payload — the checkpointer dual-path (P1 Camada 1).

Without a checkpointer (tests/SQLite, Postgres down) the payload is unchanged: leading context +
the server-rebuilt window. With a checkpointer the thread holds prior turns, so a populated thread
gets only the fresh leading context + the new user message; an empty thread (a pre-checkpointer
session) is seeded once with the full window. This guards that the stateless path stays identical
and the stateful path never re-sends history it already has.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.agents.data_agent.agent_data import DataAgent

pytestmark = pytest.mark.asyncio

_LEADING = [{"role": "system", "content": "prefs"}]
_HISTORY = [
    {"role": "user", "content": "primeira pergunta"},
    {"role": "assistant", "content": "primeira resposta"},
    {"role": "user", "content": "pergunta atual"},
]
_LAST_USER = "pergunta atual"


def _snapshot(messages: list) -> SimpleNamespace:
    """A minimal stand-in for a LangGraph StateSnapshot (only .values is read)."""
    return SimpleNamespace(values={"messages": messages})


async def _compose(checkpointer, snapshot) -> list[dict]:
    """Call the unbound method against a stand-in self, avoiding a real deep-agent build."""
    agent = SimpleNamespace(aget_state=AsyncMock(return_value=snapshot))
    fake_self = SimpleNamespace(_checkpointer=checkpointer, agent=agent)
    return await DataAgent._compose_payload(fake_self, {}, _LEADING, _HISTORY, _LAST_USER)


_FOLDED = f'{_LEADING[0]["content"]}\n\n---\n\n{_LAST_USER}'


class TestComposePayload:
    """The payload adapts to the checkpointer; with one, no system message is ever appended mid-thread."""

    async def test_stateless_sends_leading_plus_full_window(self):
        """No checkpointer → unchanged behavior: leading (system) + the whole rebuilt window."""
        assert await _compose(None, None) == [*_LEADING, *_HISTORY]

    async def test_populated_thread_folds_context_into_the_new_user_turn(self):
        """A thread with prior turns → a single user message carrying the folded context (no system)."""
        result = await _compose(object(), _snapshot([{"role": "user", "content": "primeira pergunta"}]))
        assert result == [{"role": "user", "content": _FOLDED}]
        assert all(m["role"] != "system" for m in result)  # nothing that could sit mid-thread

    async def test_empty_thread_is_seeded_with_prior_turns_then_folded_new_turn(self):
        """An empty thread (pre-checkpointer session) → prior turns (user/assistant) + the folded new turn."""
        result = await _compose(object(), _snapshot([]))
        assert result == [*_HISTORY[:-1], {"role": "user", "content": _FOLDED}]
        assert all(m["role"] != "system" for m in result)

    async def test_missing_snapshot_is_treated_as_empty(self):
        """A None snapshot (fresh thread) is seeded, never crashes."""
        result = await _compose(object(), None)
        assert result == [*_HISTORY[:-1], {"role": "user", "content": _FOLDED}]
