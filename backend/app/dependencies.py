"""
Shared FastAPI dependencies.

Pipeline components are no longer global singletons: each case has its own bundle,
served by CaseIndexRegistry (decision D11). Access checks live here so every protected
route declares its minimum role in its signature.
"""
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.database import get_db
from backend.models import Case, User, Workspace
from backend.services.access import case_role, has_case_role, has_workspace_role, workspace_role
from backend.services.case_index_registry import CaseIndexRegistry, case_index_registry
from backend.services.users import current_active_user


def get_case_index_registry() -> CaseIndexRegistry:
    return case_index_registry


# ── access ────────────────────────────────────────────────────────────────────

@dataclass
class WorkspaceAccess:
    workspace: Workspace
    role: str
    user: User


@dataclass
class CaseAccess:
    case: Case
    role: str
    user: User


def require_workspace_role(min_role: str) -> Callable[..., Awaitable[WorkspaceAccess]]:
    async def dep(
        workspace_id: uuid.UUID,
        user: User = Depends(current_active_user),
        db: AsyncSession = Depends(get_db),
    ) -> WorkspaceAccess:
        ws = await db.get(Workspace, workspace_id)
        role = await workspace_role(db, workspace_id, user.id) if ws else None
        # Non-members get 404 so they can't probe which workspaces exist.
        if ws is None or role is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Workspace not found.")
        if not has_workspace_role(role, min_role):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role.")
        return WorkspaceAccess(ws, role, user)
    return dep


def require_case_role(min_role: str) -> Callable[..., Awaitable[CaseAccess]]:
    async def dep(
        case_id: uuid.UUID,
        user: User = Depends(current_active_user),
        db: AsyncSession = Depends(get_db),
    ) -> CaseAccess:
        case = await db.get(Case, case_id)
        role = await case_role(db, case, user.id) if case else None
        if case is None or role is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Case not found.")
        if not has_case_role(role, min_role):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role.")
        return CaseAccess(case, role, user)
    return dep
