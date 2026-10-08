"""
Per-case retrieval indexes (derived data), shaped like the cascade:

  passages ──< entity_mentions >── entities ──< relationships
  (RAPTOR: chunks + summaries)     (GraphRAG graph)   communities

HippoRAG stores nothing: it ranks passages over this graph at query time.

Every table has an FK to cases (cascade) but deliberately no FK to documents:
deleting a document does not un-index it (ARCHITECTURE.md §8), so these rows may point
at deleted documents. `seq` columns keep the pipeline's insertion order so load_state()
rebuilds identical in-memory objects.
"""
import uuid
from typing import List, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint, Float, ForeignKey, Integer, SmallInteger, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
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


# ── stage 1: RAPTOR passages ──────────────────────────────────────────────────

class PassageRow(Base):
    """A RAPTOR tree node: level 0 is a document chunk, higher levels are summaries.
    These are the only texts the LLM ever sees as context."""
    __tablename__ = "passages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    case_id: Mapped[uuid.UUID] = _case_fk()
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    level: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))  # no self-FK: rows are bulk-replaced
    children: Mapped[List[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=False, default=list)
    doc_ids: Mapped[List[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    embedding: Mapped[Optional[List[float]]] = mapped_column(Vector(EMBED_DIM))

    __table_args__ = (
        CheckConstraint("level >= 0", name="level_nonneg"),
        UniqueConstraint("case_id", "seq"),
    )


# ── stage 2: GraphRAG graph ───────────────────────────────────────────────────

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
    """Which passages an entity was extracted from (Entity.source_chunks in the pipeline).
    HippoRAG scores a passage by the PageRank of the entities it mentions.
    `position` keeps the list order."""
    __tablename__ = "entity_mentions"

    entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True
    )
    passage_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("passages.id", ondelete="CASCADE"), primary_key=True, index=True
    )
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
