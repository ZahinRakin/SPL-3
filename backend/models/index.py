"""
Per-case retrieval indexes (derived data).

Every table has an FK to cases (cascade) but deliberately no FK to documents/chunks:
deleting a document does not un-index it (ARCHITECTURE.md §8), so these rows may point
at deleted documents. `seq` columns keep the pipeline's insertion order so load_state()
rebuilds identical in-memory objects.
"""
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint, DateTime, Float, ForeignKey, Integer, SmallInteger, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.database import Base
from backend.models.common import check_in

EMBED_DIM = 768   # nomic-embed-text
ENTITY_TYPES = (
    "PERSON", "ORGANIZATION", "LOCATION", "DATE", "EVENT",
    "CONCEPT", "PRODUCT", "LAW", "DISEASE", "DRUG", "OTHER",
)


def _case_fk() -> Mapped[uuid.UUID]:
    return mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True)


# ── GraphRAG ──────────────────────────────────────────────────────────────────

class EntityRow(Base):
    __tablename__ = "entities"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    case_id: Mapped[uuid.UUID] = _case_fk()
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_key: Mapped[str] = mapped_column(Text, nullable=False)   # same key as _name_to_id
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    community: Mapped[int] = mapped_column(Integer, nullable=False, default=-1)

    __table_args__ = (
        CheckConstraint(check_in("type", ENTITY_TYPES), name="type"),
        UniqueConstraint("case_id", "normalized_key"),
        UniqueConstraint("case_id", "seq"),
    )


class EntityMention(Base):
    """Replaces Entity.source_chunks (a list); `position` keeps list order."""
    __tablename__ = "entity_mentions"

    entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True
    )
    chunk_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)


class RelationshipRow(Base):
    __tablename__ = "relationships"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    case_id: Mapped[uuid.UUID] = _case_fk()
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    source_entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    relation_type: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)

    __table_args__ = (UniqueConstraint("case_id", "seq"),)


class CommunityRow(Base):
    __tablename__ = "communities"

    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), primary_key=True
    )
    community_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)


# ── RAPTOR / HiPPO ────────────────────────────────────────────────────────────

class RaptorNodeRow(Base):
    __tablename__ = "raptor_nodes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    case_id: Mapped[uuid.UUID] = _case_fk()
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    level: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))  # no self-FK: rows are bulk-replaced
    children: Mapped[List[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=False, default=list)
    doc_ids: Mapped[List[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    embedding: Mapped[Optional[List[float]]] = mapped_column(Vector(EMBED_DIM))

    __table_args__ = (UniqueConstraint("case_id", "seq"),)


class HippoNodeRow(Base):
    __tablename__ = "hippo_nodes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    case_id: Mapped[uuid.UUID] = _case_fk()
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    level: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    child_ids: Mapped[List[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=False, default=list)
    doc_id: Mapped[str] = mapped_column(Text, nullable=False, default="")
    chunk_idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedding: Mapped[Optional[List[float]]] = mapped_column(Vector(EMBED_DIM))

    __table_args__ = (UniqueConstraint("case_id", "seq"),)


class CaseIndexMeta(Base):
    """Small pieces of index state that aren't rows."""
    __tablename__ = "case_index_meta"

    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), primary_key=True
    )
    raptor_root_ids: Mapped[List[str]] = mapped_column(JSONB, nullable=False, default=list)
    hippo_levels: Mapped[Dict[str, List[str]]] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
