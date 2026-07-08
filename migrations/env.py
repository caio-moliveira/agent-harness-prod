"""Alembic migration environment.

Wires Alembic to the app's single source of truth: the database URL is built from ``settings``
(the same one ``DatabaseFactory`` uses) and the target metadata is ``SQLModel.metadata`` with every
model imported (via ``models_registry``) so autogenerate sees the full schema. Migrations run
against the synchronous ``postgresql://`` engine — the async pool is a runtime concern, not a
migration one.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

from src.app.core.common.config import settings
from src.app.core.db import models_registry  # noqa: F401 - registers every table on the metadata

# Alembic Config object (reads alembic.ini).
config = context.config

# Build the DB URL from settings so there is one source of truth (never hardcode it in the .ini).
_db_url = (
    f"postgresql://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}"
    f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
)
config.set_main_option("sqlalchemy.url", _db_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata

# Tables created and owned by other libraries at runtime — NOT by SQLModel. Autogenerate must
# ignore them, otherwise it would propose dropping the LangGraph checkpointer / mem0 tables.
_EXTERNAL_TABLES = {
    "checkpoints",
    "checkpoint_writes",
    "checkpoint_blobs",
    "checkpoint_migrations",
    "mem0migrations",
    "longterm_memory",
}


def _include_name(name, type_, parent_names) -> bool:
    """Keep autogenerate scoped to SQLModel-owned tables (skip externally-managed ones)."""
    if type_ == "table":
        return name not in _EXTERNAL_TABLES
    return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL to stdout, no live connection)."""
    context.configure(
        url=_db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_name=_include_name,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode against a live connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_name=_include_name,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
