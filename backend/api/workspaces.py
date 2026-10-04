import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.cases import case_to_out, document_counts
from backend.app.dependencies import WorkspaceAccess, get_case_index_registry, require_workspace_role
from backend.core.database import get_db
from backend.core.logger import logger
from backend.models import Case, CaseMember, User, Workspace, WorkspaceMember
from backend.schemas.cases import CaseCreate, CaseOut, CaseStatus
from backend.schemas.workspaces import (
    WorkspaceCreate, WorkspaceMemberAdd, WorkspaceMemberOut, WorkspaceMemberRoleUpdate, WorkspaceOut,
    WorkspaceUpdate,
)
from backend.services import audit
from backend.services.access import has_workspace_role
from backend.services.case_index_registry import CaseIndexRegistry
from backend.services.storage import remove_case_files
from backend.services.users import current_active_user, ensure_personal_workspace

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


def _workspace_out(ws: Workspace, role: str, member_count: int) -> WorkspaceOut:
    return WorkspaceOut(
        id=str(ws.id), name=ws.name, kind=ws.kind, my_role=role,
        member_count=member_count, created_at=ws.created_at,
    )


def _member_out(member: WorkspaceMember, user: User) -> WorkspaceMemberOut:
    return WorkspaceMemberOut(
        user_id=str(user.id), email=user.email, full_name=user.full_name,
        role=member.role, created_at=member.created_at,
    )


async def _member_count(db: AsyncSession, workspace_id: uuid.UUID) -> int:
    return (await db.execute(
        select(func.count()).select_from(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id)
    )).scalar_one()


# ── workspaces ────────────────────────────────────────────────────────────────

@router.get("", response_model=List[WorkspaceOut])
async def list_workspaces(user: User = Depends(current_active_user), db: AsyncSession = Depends(get_db)):
    await ensure_personal_workspace(db, user)   # repairs accounts whose sign-up hook failed
    await db.commit()
    counts = (
        select(WorkspaceMember.workspace_id, func.count().label("n"))
        .group_by(WorkspaceMember.workspace_id).subquery()
    )
    rows = await db.execute(
        select(Workspace, WorkspaceMember.role, counts.c.n)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .join(counts, counts.c.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id)
        .order_by(Workspace.kind.desc(), Workspace.name)     # personal first
    )
    return [_workspace_out(ws, role, n) for ws, role, n in rows.all()]


@router.post("", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    body: WorkspaceCreate,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    ws = Workspace(name=body.name, kind="organization", created_by=user.id)
    db.add(ws)
    await db.flush()
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner"))
    audit.record(db, audit.WORKSPACE_CREATE, user_id=user.id, workspace_id=ws.id,
                 target_type="workspace", target_id=ws.id, details={"kind": "organization", "name": ws.name})
    await db.commit()
    await db.refresh(ws)
    logger.info(f"Workspace created: {ws.id} ({ws.name!r}) by {user.id}")
    return _workspace_out(ws, "owner", 1)


@router.get("/{workspace_id}", response_model=WorkspaceOut)
async def get_workspace(access: WorkspaceAccess = Depends(require_workspace_role("member")), db: AsyncSession = Depends(get_db)):
    return _workspace_out(access.workspace, access.role, await _member_count(db, access.workspace.id))


@router.patch("/{workspace_id}", response_model=WorkspaceOut)
async def update_workspace(
    body: WorkspaceUpdate,
    access: WorkspaceAccess = Depends(require_workspace_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    ws = access.workspace
    ws.name = body.name
    audit.record(db, audit.WORKSPACE_UPDATE, user_id=access.user.id, workspace_id=ws.id,
                 target_type="workspace", target_id=ws.id, details={"name": body.name})
    await db.commit()
    await db.refresh(ws)
    return _workspace_out(ws, access.role, await _member_count(db, ws.id))


@router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(
    access: WorkspaceAccess = Depends(require_workspace_role("owner")),
    db: AsyncSession = Depends(get_db),
    registry: CaseIndexRegistry = Depends(get_case_index_registry),
):
    ws = access.workspace
    if ws.kind == "personal":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A personal workspace can't be deleted.")
    case_ids = (await db.execute(select(Case.id).where(Case.workspace_id == ws.id))).scalars().all()
    logger.info(f"Deleting workspace {ws.id} with {len(case_ids)} case(s)")
    audit.record(db, audit.WORKSPACE_DELETE, user_id=access.user.id, workspace_id=ws.id,
                 target_type="workspace", target_id=ws.id, details={"name": ws.name, "cases": len(case_ids)})
    await db.delete(ws)
    await db.commit()
    for cid in case_ids:
        registry.evict(cid)
        await remove_case_files(cid)


# ── members ───────────────────────────────────────────────────────────────────

@router.get("/{workspace_id}/members", response_model=List[WorkspaceMemberOut])
async def list_members(access: WorkspaceAccess = Depends(require_workspace_role("member")), db: AsyncSession = Depends(get_db)):
    rows = await db.execute(
        select(WorkspaceMember, User).join(User, User.id == WorkspaceMember.user_id)
        .where(WorkspaceMember.workspace_id == access.workspace.id)
        .order_by(WorkspaceMember.created_at)
    )
    return [_member_out(m, u) for m, u in rows.unique().all()]


@router.post("/{workspace_id}/members", response_model=WorkspaceMemberOut, status_code=status.HTTP_201_CREATED)
async def add_member(
    body: WorkspaceMemberAdd,
    access: WorkspaceAccess = Depends(require_workspace_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    ws = access.workspace
    if ws.kind == "personal":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Create an organization to add members.")
    user = (await db.execute(
        select(User).where(func.lower(User.email) == body.email.lower())
    )).unique().scalar_one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No registered user with that email.")
    if await db.get(WorkspaceMember, (ws.id, user.id)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "That user is already a member.")
    member = WorkspaceMember(workspace_id=ws.id, user_id=user.id, role=body.role)
    db.add(member)
    audit.record(db, audit.WORKSPACE_MEMBER_ADD, user_id=access.user.id, workspace_id=ws.id,
                 target_type="user", target_id=user.id, details={"role": body.role})
    await db.commit()
    await db.refresh(member)
    return _member_out(member, user)


@router.patch("/{workspace_id}/members/{user_id}", response_model=WorkspaceMemberOut)
async def update_member(
    user_id: uuid.UUID,
    body: WorkspaceMemberRoleUpdate,
    access: WorkspaceAccess = Depends(require_workspace_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    member = await db.get(WorkspaceMember, (access.workspace.id, user_id))
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found.")
    if member.role == "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "The owner's role can't be changed.")
    member.role = body.role
    audit.record(db, audit.WORKSPACE_MEMBER_ROLE, user_id=access.user.id, workspace_id=access.workspace.id,
                 target_type="user", target_id=user_id, details={"role": body.role})
    await db.commit()
    return _member_out(member, await db.get(User, user_id))


@router.delete("/{workspace_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    user_id: uuid.UUID,
    access: WorkspaceAccess = Depends(require_workspace_role("member")),
    db: AsyncSession = Depends(get_db),
):
    """Admins remove members; anyone may remove themselves (leave). The owner can't leave."""
    ws = access.workspace
    if user_id != access.user.id and not has_workspace_role(access.role, "admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role.")
    member = await db.get(WorkspaceMember, (ws.id, user_id))
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found.")
    if member.role == "owner":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The owner can't be removed. Delete the workspace instead.")
    # Leaving the organization also removes access to its cases.
    await db.execute(
        delete(CaseMember).where(
            CaseMember.user_id == user_id,
            CaseMember.case_id.in_(select(Case.id).where(Case.workspace_id == ws.id)),
        )
    )
    await db.delete(member)
    audit.record(db, audit.WORKSPACE_MEMBER_REMOVE, user_id=access.user.id, workspace_id=ws.id,
                 target_type="user", target_id=user_id)
    await db.commit()


# ── cases ─────────────────────────────────────────────────────────────────────

@router.get("/{workspace_id}/cases", response_model=List[CaseOut])
async def list_cases(
    status_filter: Optional[CaseStatus] = Query(default=None, alias="status"),
    access: WorkspaceAccess = Depends(require_workspace_role("member")),
    db: AsyncSession = Depends(get_db),
):
    """Owners/admins see every case (as lead); members see the cases they were added to."""
    stmt = select(Case, CaseMember.role).outerjoin(
        CaseMember, (CaseMember.case_id == Case.id) & (CaseMember.user_id == access.user.id)
    ).where(Case.workspace_id == access.workspace.id)
    is_admin = has_workspace_role(access.role, "admin")
    if not is_admin:
        stmt = stmt.where(CaseMember.user_id.is_not(None))
    if status_filter:
        stmt = stmt.where(Case.status == status_filter)
    rows = (await db.execute(stmt.order_by(Case.updated_at.desc()))).all()
    counts = await document_counts(db, [c.id for c, _ in rows])
    return [case_to_out(c, "lead" if is_admin else role, counts.get(c.id, 0)) for c, role in rows]


@router.post("/{workspace_id}/cases", response_model=CaseOut, status_code=status.HTTP_201_CREATED)
async def create_case(
    body: CaseCreate,
    access: WorkspaceAccess = Depends(require_workspace_role("member")),
    db: AsyncSession = Depends(get_db),
):
    case = Case(
        workspace_id=access.workspace.id, title=body.title, description=body.description,
        reference_code=body.reference_code or None, priority=body.priority, created_by=access.user.id,
    )
    db.add(case)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Another case in this workspace already uses that reference code.")
    db.add(CaseMember(case_id=case.id, user_id=access.user.id, role="lead", added_by=access.user.id))
    audit.record(db, audit.CASE_CREATE, user_id=access.user.id, workspace_id=access.workspace.id,
                 case_id=case.id, target_type="case", target_id=case.id, details={"title": case.title})
    await db.commit()
    await db.refresh(case)
    logger.info(f"Case created: {case.id} ({case.title!r}) in workspace {access.workspace.id}")
    return case_to_out(case, "lead", 0)
