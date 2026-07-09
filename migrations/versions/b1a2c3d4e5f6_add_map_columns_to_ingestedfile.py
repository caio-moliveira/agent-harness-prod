"""add map columns (description, status) to ingestedfile

Adds the "map" columns (#23) to ``ingestedfile``: ``description`` (a one-line semantic summary
generated at ingest) and ``status`` (active | deleted | pending, indexed). Both get a server_default
so the ALTER is safe on a table that already holds rows.

Revision ID: b1a2c3d4e5f6
Revises: 8b739cc1da95
Create Date: 2026-07-09 15:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1a2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "8b739cc1da95"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the description + status columns and the status index."""
    op.add_column("ingestedfile", sa.Column("description", sa.String(), nullable=False, server_default=""))
    op.add_column("ingestedfile", sa.Column("status", sa.String(), nullable=False, server_default="active"))
    op.create_index(op.f("ix_ingestedfile_status"), "ingestedfile", ["status"], unique=False)


def downgrade() -> None:
    """Drop the description + status columns and the status index."""
    op.drop_index(op.f("ix_ingestedfile_status"), table_name="ingestedfile")
    op.drop_column("ingestedfile", "status")
    op.drop_column("ingestedfile", "description")
