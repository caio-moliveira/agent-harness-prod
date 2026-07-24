"""Unit tests for ``build_invoke_config``'s Langfuse session/user grouping.

``langfuse_session_id``/``langfuse_user_id`` are the metadata keys Langfuse's LangChain
``CallbackHandler`` specifically recognizes to group every trace from one conversation into a
single Session in the UI (see https://langfuse.com/docs/tracing-features/sessions) — a plain
``session_id``/``user_id`` key would just be inert metadata the handler never reads.
"""

from src.app.core.middleware.types import build_invoke_config


def test_session_id_is_set_under_the_langfuse_recognized_key():
    """``langfuse_session_id`` carries the session id so the Sessions view groups the trace."""
    config = build_invoke_config("session-123", user_id=None, agent_name="data_agent")

    assert config["metadata"]["langfuse_session_id"] == "session-123"


def test_user_id_is_set_under_the_langfuse_recognized_key_and_stringified():
    """``langfuse_user_id`` carries the user id (as a string) for per-user filtering/attribution."""
    config = build_invoke_config("session-123", user_id=42, agent_name="data_agent")

    assert config["metadata"]["langfuse_user_id"] == "42"


def test_user_id_key_is_omitted_when_absent():
    """No ``langfuse_user_id`` key at all for anonymous/unauthenticated invocations."""
    config = build_invoke_config("session-123", user_id=None, agent_name="data_agent")

    assert "langfuse_user_id" not in config["metadata"]
