"""Test-suite bootstrap shared by ALL test packages (unit + integration + e2e).

Everything here must run *before* any application module is imported, because the settings
singleton reads the environment at import time and ``DatabaseFactory`` connects at import time.
Pytest imports this root conftest before any per-directory conftest and before collecting any
test module, which is exactly the guarantee we need — it also means ``pytest tests/unit`` behaves
identically to a full-suite run (no Postgres required: every engine becomes in-memory SQLite).
"""

import os

# ---------------------------------------------------------------------------
# Environment must be set BEFORE any application module is imported
# ---------------------------------------------------------------------------

os.environ["APP_ENV"] = "test"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-for-integration-tests"
# Fake provider keys: the factory builds real client objects (ChatAnthropic/ChatOpenAI) whose
# constructors require a key string — no network call ever happens in tests.
os.environ["OPENAI_API_KEY"] = "sk-test-fake-key"
os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-fake-key"
os.environ["LANGFUSE_PUBLIC_KEY"] = "pk-test"
os.environ["LANGFUSE_SECRET_KEY"] = "sk-test"
os.environ["LANGFUSE_HOST"] = "http://localhost:0"
os.environ["MCP_ENABLED"] = "false"

from unittest.mock import MagicMock, patch

import sqlmodel as _sqlmodel_module
from sqlalchemy.pool import StaticPool

# ---------------------------------------------------------------------------
# Intercept create_engine so DatabaseFactory uses an in-memory SQLite DB
# ---------------------------------------------------------------------------

_original_create_engine = _sqlmodel_module.create_engine
_shared_engine = None


def _sqlite_create_engine(*args, **kwargs):
    """Replace any engine creation with a shared in-memory SQLite engine.

    ``StaticPool`` is essential, not cosmetic: an in-memory SQLite database lives inside its
    connection, and SQLAlchemy's default pool hands a *different* connection to each thread — so
    code that legitimately moves DB work off the event loop with ``asyncio.to_thread`` (the
    retention purge, the health probe) would find an empty database. StaticPool keeps one shared
    connection so every thread sees the same schema and rows.
    """
    global _shared_engine
    if _shared_engine is None:
        _shared_engine = _original_create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    return _shared_engine


_sqlmodel_module.create_engine = _sqlite_create_engine


def get_shared_engine():
    """The shared in-memory SQLite engine (lazily created on first use)."""
    return _sqlite_create_engine()

# Prevent Langfuse from making real network calls during module-level init
_mock_langfuse_inst = MagicMock()
_mock_langfuse_inst.auth_check.return_value = True
patch("langfuse.Langfuse", return_value=_mock_langfuse_inst).start()
patch("langfuse.langchain.CallbackHandler", return_value=MagicMock()).start()
