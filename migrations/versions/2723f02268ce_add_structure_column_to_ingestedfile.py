"""add structure column to ingestedfile

Revision ID: 2723f02268ce
Revises: c2b3d4e5f6a7
Create Date: 2026-07-10 10:42:14.994297

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '2723f02268ce'
down_revision: Union[str, Sequence[str], None] = 'c2b3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the nullable ``structure`` column (per-file tree, JSON as text) to ``ingestedfile``."""
    op.add_column('ingestedfile', sa.Column('structure', sa.Text(), nullable=True))


def downgrade() -> None:
    """Drop the ``structure`` column."""
    op.drop_column('ingestedfile', 'structure')
