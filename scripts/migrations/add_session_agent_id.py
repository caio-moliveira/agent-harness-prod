"""Idempotent dev migration: create the ``agent`` table and add ``session.agent_id``.

This repo has no migration framework — ``SQLModel.metadata.create_all`` only creates
missing tables, it never alters an existing one. The ``session`` table predates the
``agent_id`` column introduced with the user-configurable agent harness, so an existing
database needs this additive, non-destructive step. A fresh database gets the column for
free via ``create_all`` at startup and does not need this script.

Usage (from the repo root)::

    uv run python scripts/migrations/add_session_agent_id.py            # development
    uv run python scripts/migrations/add_session_agent_id.py staging    # another env

Safe to run repeatedly: ``ADD COLUMN IF NOT EXISTS`` is a no-op once applied.
"""

import sys

from dotenv import load_dotenv

# Load the environment file BEFORE importing application modules, so the DB engine is
# built from the right settings (mirrors how the app boots).
_ENV = sys.argv[1] if len(sys.argv) > 1 else "development"
load_dotenv(f".env.{_ENV}")

from sqlalchemy import inspect, text  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

import src.app.core.agent.agent_model  # noqa: E402,F401  (register the Agent table)
import src.app.core.session.session_model  # noqa: E402,F401
import src.app.core.user.user_model  # noqa: E402,F401
from src.app.core.common.logging import logger  # noqa: E402
from src.app.core.db.database import database_factory  # noqa: E402


def main() -> None:
    """Ensure the agent table exists and the session.agent_id column is present."""
    engine = database_factory.engine

    # Create any missing tables (notably `agent`); never alters existing ones.
    SQLModel.metadata.create_all(engine)

    columns = {c["name"] for c in inspect(engine).get_columns("session")}
    if "agent_id" in columns:
        logger.info("migration_noop", column="session.agent_id", reason="already_present")
        return

    with engine.connect() as conn:
        conn.execute(text('ALTER TABLE "session" ADD COLUMN IF NOT EXISTS agent_id INTEGER'))
        conn.commit()
    logger.info("migration_applied", column="session.agent_id", environment=_ENV)


if __name__ == "__main__":
    main()
