"""drop documentchunk table and chunk_count (vectorless corpus)

Revision ID: 4c5d6e7f8a9b
Revises: 3a1b2c3d4e5f
Create Date: 2026-07-10 12:10:00.000000

The document RAG (chunks + embeddings) is removed: reading/searching now come from the manifest's
structure tree + located text. This drops the ``documentchunk`` table and the now-unused
``ingestedfile.chunk_count`` column.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '4c5d6e7f8a9b'
down_revision: Union[str, Sequence[str], None] = '3a1b2c3d4e5f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop the chunk store and the chunk_count column."""
    op.drop_table('documentchunk')
    op.drop_column('ingestedfile', 'chunk_count')


def downgrade() -> None:
    """Recreate the chunk store and chunk_count (embedding kept as JSON, as in the original model)."""
    op.add_column('ingestedfile', sa.Column('chunk_count', sa.Integer(), nullable=False, server_default='0'))
    op.create_table(
        'documentchunk',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('agent_id', sa.Integer(), nullable=True),
        sa.Column('source_path', sa.String(), nullable=False),
        sa.Column('doc_type', sa.String(), nullable=False),
        sa.Column('section', sa.String(), nullable=False, server_default=''),
        sa.Column('chunk_index', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('content', sa.String(), nullable=False, server_default=''),
        sa.Column('meta', sa.JSON(), nullable=True),
        sa.Column('embedding', sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        sa.ForeignKeyConstraint(['agent_id'], ['agent.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_documentchunk_user_id', 'documentchunk', ['user_id'])
    op.create_index('ix_documentchunk_agent_id', 'documentchunk', ['agent_id'])
    op.create_index('ix_documentchunk_source_path', 'documentchunk', ['source_path'])
    op.create_index('ix_documentchunk_doc_type', 'documentchunk', ['doc_type'])
