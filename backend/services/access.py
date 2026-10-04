"""
Who may do what.

Workspace roles (organization directory):  member < admin < owner
Case roles (per-case member list):         viewer < investigator < lead

A user's role on a case is the higher of:
  - their row in case_members, and
  - implicit 'lead' if they are owner/admin of the case's workspace (oversight).
They must be a workspace member either way; non-members have no access at all.
"""
import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Case, CaseMember, WorkspaceMember

WORKSPACE_ROLE_RANK = {"member": 0, "admin": 1, "owner": 2}
CASE_ROLE_RANK = {"viewer": 0, "investigator": 1, "lead": 2}


def has_workspace_role(role: Optional[str], min_role: str) -> bool:
    return role is not None and WORKSPACE_ROLE_RANK[role] >= WORKSPACE_ROLE_RANK[min_role]


def has_case_role(role: Optional[str], min_role: str) -> bool:
    return role is not None and CASE_ROLE_RANK[role] >= CASE_ROLE_RANK[min_role]


async def workspace_role(session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID) -> Optional[str]:
    member = await session.get(WorkspaceMember, (workspace_id, user_id))
    return member.role if member else None


async def case_role(session: AsyncSession, case: Case, user_id: uuid.UUID) -> Optional[str]:
    ws_role = await workspace_role(session, case.workspace_id, user_id)
    if ws_role is None:
        return None
    if has_workspace_role(ws_role, "admin"):
        return "lead"
    member = await session.get(CaseMember, (case.id, user_id))
    return member.role if member else None
