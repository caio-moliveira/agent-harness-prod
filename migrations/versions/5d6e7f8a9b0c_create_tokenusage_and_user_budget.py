"""create tokenusage table and user.token_budget_daily.

Cost governance (#73): per-user, per-UTC-day token accounting plus a per-account budget override.
One row per (user, day) — aggregated on purpose, so enforcement is a single indexed read and the
table stays small; per-call detail lives in the Langfuse traces.

``user.token_budget_daily`` is nullable by design: NULL means "follow the global
TOKEN_BUDGET_DAILY", while an explicit 0 means "this account is unlimited". That distinction is why
the column has no server_default.

Revision ID: 5d6e7f8a9b0c
Revises: 4c5d6e7f8a9b
Create Date: 2026-07-31 18:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5d6e7f8a9b0c"
down_revision: Union[str, Sequence[str], None] = "4c5d6e7f8a9b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create ``tokenusage`` and add the per-user budget override column."""
    op.create_table(
        "tokenusage",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("turns", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        # The arbiter for the concurrent-increment upsert in TokenUsageRepository.add_usage.
        sa.UniqueConstraint("user_id", "day", name="uq_tokenusage_user_day"),
    )
    op.create_index(op.f("ix_tokenusage_user_id"), "tokenusage", ["user_id"], unique=False)
    op.create_index(op.f("ix_tokenusage_day"), "tokenusage", ["day"], unique=False)

    op.add_column("user", sa.Column("token_budget_daily", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Drop the budget override column and the usage table."""
    op.drop_column("user", "token_budget_daily")
    op.drop_index(op.f("ix_tokenusage_day"), table_name="tokenusage")
    op.drop_index(op.f("ix_tokenusage_user_id"), table_name="tokenusage")
    op.drop_table("tokenusage")
