"""add manifest columns to ingestedfile

Adds the document-manifest columns to ``ingestedfile`` (the catalog the document tools read):
``doc_id`` (stable id, indexed), ``title``, ``page_count``, ``text_layer``, ``ocr_confidence``.

Scoped deliberately to this change only — autogenerate also surfaced pre-existing drift in the
``skill``/``session`` tables, which is left out of this migration. Each column is added with a
server_default so the ALTER is safe even if the table already holds rows (the app-side defaults on
the model then take over for new inserts).

Revision ID: 8b739cc1da95
Revises:
Create Date: 2026-07-08 14:56:20.661810
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8b739cc1da95"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the manifest columns + the doc_id index."""
    op.add_column("ingestedfile", sa.Column("doc_id", sa.String(), nullable=False, server_default=""))
    op.add_column("ingestedfile", sa.Column("title", sa.String(), nullable=False, server_default=""))
    op.add_column("ingestedfile", sa.Column("page_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column(
        "ingestedfile", sa.Column("text_layer", sa.String(), nullable=False, server_default="native")
    )
    op.add_column(
        "ingestedfile", sa.Column("ocr_confidence", sa.Float(), nullable=False, server_default="1.0")
    )
    op.create_index(op.f("ix_ingestedfile_doc_id"), "ingestedfile", ["doc_id"], unique=False)


def downgrade() -> None:
    """Drop the manifest columns + the doc_id index."""
    op.drop_index(op.f("ix_ingestedfile_doc_id"), table_name="ingestedfile")
    op.drop_column("ingestedfile", "ocr_confidence")
    op.drop_column("ingestedfile", "text_layer")
    op.drop_column("ingestedfile", "page_count")
    op.drop_column("ingestedfile", "title")
    op.drop_column("ingestedfile", "doc_id")
