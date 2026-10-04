"""SQLAlchemy ORM models. Importing this package registers every table on Base.metadata."""
from backend.models.user import OAuthAccount, RefreshToken, User
from backend.models.workspace import Workspace, WorkspaceMember
from backend.models.case import Case, CaseMember, Chunk, Document
from backend.models.index import (
    CaseIndexMeta, CommunityRow, EntityMention, EntityRow, HippoNodeRow, RaptorNodeRow,
    RelationshipRow,
)
from backend.models.activity import AuditEvent, ChatMessage

__all__ = [
    "User", "OAuthAccount", "RefreshToken",
    "Workspace", "WorkspaceMember",
    "Case", "CaseMember", "Document", "Chunk",
    "EntityRow", "EntityMention", "RelationshipRow", "CommunityRow",
    "RaptorNodeRow", "HippoNodeRow", "CaseIndexMeta",
    "ChatMessage", "AuditEvent",
]
