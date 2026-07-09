"""create agentmemory table

Creates ``agentmemory`` (#23): the agent's two-tier experience memory — an embedded ``summary``
(tier 1) plus a full ``body``/``refs`` (tier 2), scoped per (user, agent). Mirrors the DocumentChunk
storage choice: the embedding is a JSON column searched with in-memory cosine (pgvector + ANN is a
later optimization).

Revision ID: c2b3d4e5f6a7
Revises: b1a2c3d4e5f6
Create Date: 2026-07-09 15:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c2b3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "b1a2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the agentmemory table and its indexes."""
    op.create_table(
        "agentmemory",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("agent_id", sa.Integer(), nullable=True),
        sa.Column("session_id", sa.String(), nullable=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("body", sa.JSON(), nullable=False),
        sa.Column("refs", sa.JSON(), nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["agent_id"], ["agent.id"]),
        # session_id is provenance only (no FK): long-term memory must outlive the session.
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_agentmemory_user_id"), "agentmemory", ["user_id"], unique=False)
    op.create_index(op.f("ix_agentmemory_agent_id"), "agentmemory", ["agent_id"], unique=False)
    op.create_index(op.f("ix_agentmemory_session_id"), "agentmemory", ["session_id"], unique=False)
    op.create_index(op.f("ix_agentmemory_kind"), "agentmemory", ["kind"], unique=False)


def downgrade() -> None:
    """Drop the agentmemory table and its indexes."""
    op.drop_index(op.f("ix_agentmemory_kind"), table_name="agentmemory")
    op.drop_index(op.f("ix_agentmemory_session_id"), table_name="agentmemory")
    op.drop_index(op.f("ix_agentmemory_agent_id"), table_name="agentmemory")
    op.drop_index(op.f("ix_agentmemory_user_id"), table_name="agentmemory")
    op.drop_table("agentmemory")
