"""passages schema: the index tables follow the cascade

- raptor_nodes → passages (RAPTOR chunks + summaries: everything the LLM sees as context).
- entity_mentions.chunk_id (free text, no FK) → passage_id (UUID, FK passages ON DELETE CASCADE),
  the link HippoRAG uses to score passages.
- chunks (write-only, duplicated the level-0 passages) and case_index_meta (only RAPTOR root
  ids, which nothing read) are dropped.

Old index rows can't be converted (their mentions point at "{doc_id}_c{n}" chunk ids), so the
upgrade empties every case's index and marks its documents `error` with a re-upload message.
Cases, members, chat history and the stored files are kept. downgrade() restores the old
tables empty and likewise marks documents `error`.

Revision ID: 80d2d067ef08
Revises: 3c9e2a7d41f0
Create Date: 2026-10-08 12:11:53.154657

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import pgvector.sqlalchemy  # noqa: F401  (autogenerate renders VECTOR columns with this path)
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '80d2d067ef08'
down_revision: Union[str, Sequence[str], None] = '3c9e2a7d41f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_REUPLOAD = "The index format changed (2026-10-08). Delete this document and upload it again."


def _reset_indexes() -> None:
    """Empty every case's derived index and mark its documents for re-upload."""
    op.execute("DELETE FROM entity_mentions")
    op.execute("DELETE FROM relationships")
    op.execute("DELETE FROM entities")
    op.execute("DELETE FROM communities")
    op.execute(
        sa.text("UPDATE documents SET status = 'error', error = :msg, chunk_count = 0, indexed_at = NULL")
        .bindparams(msg=_REUPLOAD)
    )


def upgrade() -> None:
    """Upgrade schema."""
    _reset_indexes()

    op.create_table('passages',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('case_id', sa.Uuid(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('level', sa.SmallInteger(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('parent_id', sa.UUID(), nullable=True),
    sa.Column('children', postgresql.ARRAY(sa.UUID()), nullable=False),
    sa.Column('doc_ids', postgresql.ARRAY(sa.Text()), nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=768), nullable=True),
    sa.CheckConstraint('level >= 0', name=op.f('ck_passages_level_nonneg')),
    sa.ForeignKeyConstraint(['case_id'], ['cases.id'], name=op.f('fk_passages_case_id_cases'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_passages')),
    sa.UniqueConstraint('case_id', 'seq', name=op.f('uq_passages_case_id_seq'))
    )
    op.create_index(op.f('ix_passages_case_id'), 'passages', ['case_id'], unique=False)
    op.drop_index(op.f('ix_raptor_nodes_case_id'), table_name='raptor_nodes')
    op.drop_table('raptor_nodes')
    op.drop_table('case_index_meta')
    op.drop_index(op.f('ix_chunks_case_id'), table_name='chunks')
    op.drop_table('chunks')

    # entity_mentions is empty now; swap chunk_id for passage_id, including in the primary key.
    op.drop_constraint(op.f('pk_entity_mentions'), 'entity_mentions', type_='primary')
    op.drop_column('entity_mentions', 'chunk_id')
    op.add_column('entity_mentions', sa.Column('passage_id', sa.Uuid(), nullable=False))
    op.create_primary_key(op.f('pk_entity_mentions'), 'entity_mentions', ['entity_id', 'passage_id'])
    op.create_index(op.f('ix_entity_mentions_passage_id'), 'entity_mentions', ['passage_id'], unique=False)
    op.create_foreign_key(op.f('fk_entity_mentions_passage_id_passages'), 'entity_mentions', 'passages', ['passage_id'], ['id'], ondelete='CASCADE')


def downgrade() -> None:
    """Downgrade schema."""
    _reset_indexes()

    op.drop_constraint(op.f('fk_entity_mentions_passage_id_passages'), 'entity_mentions', type_='foreignkey')
    op.drop_index(op.f('ix_entity_mentions_passage_id'), table_name='entity_mentions')
    op.drop_constraint(op.f('pk_entity_mentions'), 'entity_mentions', type_='primary')
    op.drop_column('entity_mentions', 'passage_id')
    op.add_column('entity_mentions', sa.Column('chunk_id', sa.String(length=80), nullable=False))
    op.create_primary_key(op.f('pk_entity_mentions'), 'entity_mentions', ['entity_id', 'chunk_id'])

    op.create_table('chunks',
    sa.Column('id', sa.String(length=80), nullable=False),
    sa.Column('document_id', sa.Uuid(), nullable=False),
    sa.Column('case_id', sa.Uuid(), nullable=False),
    sa.Column('chunk_index', sa.Integer(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['case_id'], ['cases.id'], name=op.f('fk_chunks_case_id_cases'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], name=op.f('fk_chunks_document_id_documents'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_chunks')),
    sa.UniqueConstraint('document_id', 'chunk_index', name=op.f('uq_chunks_document_id_chunk_index'))
    )
    op.create_index(op.f('ix_chunks_case_id'), 'chunks', ['case_id'], unique=False)
    op.create_table('case_index_meta',
    sa.Column('case_id', sa.Uuid(), nullable=False),
    sa.Column('raptor_root_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['case_id'], ['cases.id'], name=op.f('fk_case_index_meta_case_id_cases'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('case_id', name=op.f('pk_case_index_meta'))
    )
    op.create_table('raptor_nodes',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('case_id', sa.Uuid(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('level', sa.SmallInteger(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('parent_id', sa.UUID(), nullable=True),
    sa.Column('children', postgresql.ARRAY(sa.UUID()), nullable=False),
    sa.Column('doc_ids', postgresql.ARRAY(sa.Text()), nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=768), nullable=True),
    sa.ForeignKeyConstraint(['case_id'], ['cases.id'], name=op.f('fk_raptor_nodes_case_id_cases'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_raptor_nodes')),
    sa.UniqueConstraint('case_id', 'seq', name=op.f('uq_raptor_nodes_case_id_seq'))
    )
    op.create_index(op.f('ix_raptor_nodes_case_id'), 'raptor_nodes', ['case_id'], unique=False)
    op.drop_index(op.f('ix_passages_case_id'), table_name='passages')
    op.drop_table('passages')
