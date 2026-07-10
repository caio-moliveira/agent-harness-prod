"""add content column to ingestedfile

Revision ID: 3a1b2c3d4e5f
Revises: 2723f02268ce
Create Date: 2026-07-10 11:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '3a1b2c3d4e5f'
down_revision: Union[str, Sequence[str], None] = '2723f02268ce'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the nullable ``content`` column (located document text, JSON as text) to ``ingestedfile``."""
    op.add_column('ingestedfile', sa.Column('content', sa.Text(), nullable=True))


def downgrade() -> None:
    """Drop the ``content`` column."""
    op.drop_column('ingestedfile', 'content')
