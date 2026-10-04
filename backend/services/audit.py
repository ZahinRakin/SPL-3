"""Audit trail. record() only adds the row; the caller's transaction commits it."""
import uuid
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import AuditEvent

# ── actions ───────────────────────────────────────────────────────────────────

AUTH_REGISTER = "auth.register"
AUTH_LOGIN = "auth.login"
WORKSPACE_CREATE = "workspace.create"
WORKSPACE_UPDATE = "workspace.update"
WORKSPACE_DELETE = "workspace.delete"
WORKSPACE_MEMBER_ADD = "workspace.member_add"
WORKSPACE_MEMBER_ROLE = "workspace.member_role_change"
WORKSPACE_MEMBER_REMOVE = "workspace.member_remove"
CASE_CREATE = "case.create"
CASE_UPDATE = "case.update"
CASE_DELETE = "case.delete"
CASE_MEMBER_ADD = "case.member_add"
CASE_MEMBER_ROLE = "case.member_role_change"
CASE_MEMBER_REMOVE = "case.member_remove"
DOCUMENT_UPLOAD = "document.upload"
DOCUMENT_INDEXED = "document.indexed"
DOCUMENT_INDEX_FAILED = "document.index_failed"
DOCUMENT_DELETE = "document.delete"
QUERY_RUN = "query.run"
CHAT_CLEAR = "chat.clear"


def record(
    session: AsyncSession,
    action: str,
    *,
    user_id: Optional[uuid.UUID] = None,
    workspace_id: Optional[uuid.UUID] = None,
    case_id: Optional[uuid.UUID] = None,
    target_type: Optional[str] = None,
    target_id: Optional[Any] = None,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    session.add(AuditEvent(
        action=action,
        user_id=user_id,
        workspace_id=workspace_id,
        case_id=case_id,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        details=details or {},
    ))
