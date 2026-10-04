"""Investigation tables: cases, their members, evidence documents and chunks."""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint, func, text,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.database import Base
from backend.models.common import check_in

CASE_STATUSES = ("open", "in_progress", "closed", "archived")
CASE_PRIORITIES = ("low", "medium", "high", "critical")
CASE_ROLES = ("lead", "investigator", "viewer")
DOC_STATUSES = ("uploaded", "indexing", "indexed", "error")


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    reference_code: Mapped[Optional[str]] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open", server_default="open")
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="medium", server_default="medium")
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(check_in("status", CASE_STATUSES), name="status"),
        CheckConstraint(check_in("priority", CASE_PRIORITIES), name="priority"),
        CheckConstraint("length(title) BETWEEN 1 AND 200", name="title_length"),
        Index("ix_cases_workspace_status", "workspace_id", "status"),
        Index(
            "uq_cases_workspace_reference", "workspace_id", "reference_code",
            unique=True, postgresql_where=text("reference_code IS NOT NULL"),
        ),
    )


class CaseMember(Base):
    """Who can open a case. Workspace owners/admins also get implicit 'lead' access."""
    __tablename__ = "case_members"

    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    added_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (CheckConstraint(check_in("role", CASE_ROLES), name="role"),)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    uploaded_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    filename: Mapped[str] = mapped_column(String(255), nullable=False)       # original, display only
    stored_path: Mapped[str] = mapped_column(Text, nullable=False)           # never sent to clients
    content_type: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)          # evidence integrity
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="uploaded", server_default="uploaded")
    error: Mapped[Optional[str]] = mapped_column(Text)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    indexing_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    indexed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(check_in("status", DOC_STATUSES), name="status"),
        CheckConstraint("size_bytes >= 0", name="size_nonneg"),
        Index("ix_documents_case_sha", "case_id", "sha256"),
    )


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)   # "{doc_id}_c{idx}" from chunk_text
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (UniqueConstraint("document_id", "chunk_index"),)
