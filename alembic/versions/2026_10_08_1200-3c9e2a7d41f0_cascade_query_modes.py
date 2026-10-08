"""cascade: standard/refined query modes, drop the HiPPO pyramid

The three parallel retrievers became one cascade (RAPTOR → GraphRAG → HippoRAG PageRank),
answered in two modes: `standard` (plain RAG) and `refined` (the cascade).

- chat_messages: rows with the old methods (graphrag/raptor/hippo/hybrid) are deleted
  (owner's decision) and the method check allows only standard/refined.
- hippo_nodes and case_index_meta.hippo_levels are dropped: HippoRAG now ranks over the
  graph and RAPTOR nodes at query time and stores nothing.

downgrade() restores the old structure, but not the deleted chat rows or HiPPO nodes.

Revision ID: 3c9e2a7d41f0
Revises: be6073f3677c
Create Date: 2026-10-08 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import pgvector.sqlalchemy  # noqa: F401
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '3c9e2a7d41f0'
down_revision: Union[str, Sequence[str], None] = 'be6073f3677c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(op.f('ck_chat_messages_method'), 'chat_messages', type_='check')
    op.execute("DELETE FROM chat_messages WHERE method NOT IN ('standard', 'refined')")
    op.create_check_constraint(
        op.f('ck_chat_messages_method'), 'chat_messages', "method IN ('standard', 'refined')"
    )

    op.drop_index(op.f('ix_hippo_nodes_case_id'), table_name='hippo_nodes')
    op.drop_table('hippo_nodes')
    op.drop_column('case_index_meta', 'hippo_levels')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        'case_index_meta',
        sa.Column('hippo_levels', postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
    )
    op.alter_column('case_index_meta', 'hippo_levels', server_default=None)
    op.create_table('hippo_nodes',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('case_id', sa.Uuid(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('level', sa.SmallInteger(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('parent_id', sa.UUID(), nullable=True),
    sa.Column('child_ids', postgresql.ARRAY(sa.UUID()), nullable=False),
    sa.Column('doc_id', sa.Text(), nullable=False),
    sa.Column('chunk_idx', sa.Integer(), nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=768), nullable=True),
    sa.ForeignKeyConstraint(['case_id'], ['cases.id'], name=op.f('fk_hippo_nodes_case_id_cases'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_hippo_nodes')),
    sa.UniqueConstraint('case_id', 'seq', name=op.f('uq_hippo_nodes_case_id_seq'))
    )
    op.create_index(op.f('ix_hippo_nodes_case_id'), 'hippo_nodes', ['case_id'], unique=False)

    # Old rows would violate the restored check, and the old methods can't be inferred.
    op.drop_constraint(op.f('ck_chat_messages_method'), 'chat_messages', type_='check')
    op.execute("DELETE FROM chat_messages")
    op.create_check_constraint(
        op.f('ck_chat_messages_method'), 'chat_messages',
        "method IN ('graphrag', 'raptor', 'hippo', 'hybrid')",
    )
