"""Database checkpointing and graph compilation utilities.

This module provides functions for managing PostgreSQL connection pooling,
graph compilation, and checkpoint management for the LangGraph agent.
"""

from typing import Optional

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

from src.app.core.common.config import Environment, settings
from src.app.core.db.connection_pool import get_connection_pool
from src.app.core.common.logging import logger

# Module-level singleton for connection pool
_connection_pool: Optional[AsyncConnectionPool] = None

async def get_checkpointer():
    """Return an ``AsyncPostgresSaver`` bound to the connection pool, or None when unavailable.

    Postgres-only: returns None if the pool can't be reached in production, and raises otherwise so
    the caller can decide how to degrade (see ``get_data_agent_checkpointer``).
    """
    # Get connection pool (may be None in production if DB unavailable)
    connection_pool = await get_connection_pool()
    if connection_pool:
        checkpointer = AsyncPostgresSaver(connection_pool)
        await checkpointer.setup()
    else:
        # In production, proceed without checkpointer if needed
        checkpointer = None
        if settings.ENVIRONMENT != Environment.PRODUCTION:
            raise Exception("Connection pool initialization failed")
    return checkpointer


async def clear_checkpoints(session_id: str) -> None:
    """Clear all checkpoints for a session from database.

    Args:
        session_id: The session ID to clear checkpoints for.

    Raises:
        Exception: If there's an error clearing the checkpoints.
    """
    try:
        # Make sure the pool is initialized in the current event loop
        conn_pool = await get_connection_pool()

        # Use a new connection for this specific operation
        async with conn_pool.connection() as conn:
            for table in settings.CHECKPOINT_TABLES:
                try:
                    await conn.execute(f"DELETE FROM {table} WHERE thread_id = %s", (session_id,))
                    logger.info("checkpoint_table_cleared", table=table, session_id=session_id)
                except Exception:
                    logger.exception("checkpoint_table_clear_failed", table=table, session_id=session_id)
                    raise

    except Exception:
        logger.exception("clear_checkpoints_failed", session_id=session_id)
        raise
