import uuid
from datetime import datetime, timezone
from typing import Dict, Iterable, List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.dependencies import CaseAccess, get_case_index_registry, require_case_role
from backend.core.database import get_db
from backend.core.logger import logger
from backend.models import AuditEvent, Case, CaseMember, Document, User, WorkspaceMember
from backend.schemas.audit import AuditEventOut
from backend.schemas.cases import CaseMemberAdd, CaseMemberOut, CaseMemberRoleUpdate, CaseOut, CaseUpdate
from backend.services import audit
from backend.services.case_index_registry import CaseIndexRegistry
from backend.services.storage import remove_case_files

router = APIRouter(prefix="/cases", tags=["cases"])

AUDIT_PAGE_MAX = 200


# ── helpers (also used by api/workspaces.py) ──────────────────────────────────

async def document_counts(db: AsyncSession, case_ids: Iterable[uuid.UUID]) -> Dict[uuid.UUID, int]:
    ids = list(case_ids)
    if not ids:
        return {}
    rows = await db.execute(
        select(Document.case_id, func.count()).where(Document.case_id.in_(ids)).group_by(Document.case_id)
    )
    return {cid: n for cid, n in rows.all()}


def case_to_out(case: Case, role: str, document_count: int = 0) -> CaseOut:
    return CaseOut(
        id=str(case.id), workspace_id=str(case.workspace_id), reference_code=case.reference_code,
        title=case.title, description=case.description, status=case.status, priority=case.priority,
        created_by=str(case.created_by) if case.created_by else None,
        created_at=case.created_at, updated_at=case.updated_at, closed_at=case.closed_at,
        my_role=role, document_count=document_count,
    )


def _member_out(member: CaseMember, user: User) -> CaseMemberOut:
    return CaseMemberOut(
        user_id=str(user.id), email=user.email, full_name=user.full_name,
        role=member.role, created_at=member.created_at,
    )


async def _lead_count(db: AsyncSession, case_id: uuid.UUID) -> int:
    return (await db.execute(
        select(func.count()).select_from(CaseMember)
        .where(CaseMember.case_id == case_id, CaseMember.role == "lead")
    )).scalar_one()


# ── case ──────────────────────────────────────────────────────────────────────

@router.get("/{case_id}", response_model=CaseOut)
async def get_case(access: CaseAccess = Depends(require_case_role("viewer")), db: AsyncSession = Depends(get_db)):
    counts = await document_counts(db, [access.case.id])
    return case_to_out(access.case, access.role, counts.get(access.case.id, 0))


@router.patch("/{case_id}", response_model=CaseOut)
async def update_case(
    body: CaseUpdate,
    access: CaseAccess = Depends(require_case_role("investigator")),
    db: AsyncSession = Depends(get_db),
):
    case = access.case
    changes = body.model_dump(exclude_unset=True)
    if "status" in changes and changes["status"] != case.status:
        # closed_at records when the case was closed; reopening clears it.
        case.closed_at = datetime.now(timezone.utc) if changes["status"] == "closed" else None
    for key, value in changes.items():
        setattr(case, key, value)
    audit.record(db, audit.CASE_UPDATE, user_id=access.user.id, workspace_id=case.workspace_id,
                 case_id=case.id, target_type="case", target_id=case.id,
                 details={k: v for k, v in changes.items() if k != "description"})
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Another case in this workspace already uses that reference code.")
    await db.refresh(case)
    counts = await document_counts(db, [case.id])
    return case_to_out(case, access.role, counts.get(case.id, 0))


@router.delete("/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_case(
    access: CaseAccess = Depends(require_case_role("lead")),
    db: AsyncSession = Depends(get_db),
    registry: CaseIndexRegistry = Depends(get_case_index_registry),
):
    case = access.case
    logger.info(f"Deleting case {case.id} ({case.title!r})")
    audit.record(db, audit.CASE_DELETE, user_id=access.user.id, workspace_id=case.workspace_id,
                 case_id=case.id, target_type="case", target_id=case.id, details={"title": case.title})
    await db.delete(case)       # cascades documents, chunks, indexes, members, chat history
    await db.commit()
    registry.evict(case.id)
    await remove_case_files(case.id)


# ── members ───────────────────────────────────────────────────────────────────

@router.get("/{case_id}/members", response_model=List[CaseMemberOut])
async def list_case_members(access: CaseAccess = Depends(require_case_role("viewer")), db: AsyncSession = Depends(get_db)):
    rows = await db.execute(
        select(CaseMember, User).join(User, User.id == CaseMember.user_id)
        .where(CaseMember.case_id == access.case.id)
        .order_by(CaseMember.created_at)
    )
    return [_member_out(m, u) for m, u in rows.unique().all()]


@router.post("/{case_id}/members", response_model=CaseMemberOut, status_code=status.HTTP_201_CREATED)
async def add_case_member(
    body: CaseMemberAdd,
    access: CaseAccess = Depends(require_case_role("lead")),
    db: AsyncSession = Depends(get_db),
):
    case = access.case
    user = (await db.execute(
        select(User).where(func.lower(User.email) == body.email.lower())
    )).unique().scalar_one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No registered user with that email.")
    # Cases are shared inside an organization only.
    if await db.get(WorkspaceMember, (case.workspace_id, user.id)) is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That user is not a member of this case's workspace.")
    if await db.get(CaseMember, (case.id, user.id)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "That user is already a member of this case.")
    member = CaseMember(case_id=case.id, user_id=user.id, role=body.role, added_by=access.user.id)
    db.add(member)
    audit.record(db, audit.CASE_MEMBER_ADD, user_id=access.user.id, workspace_id=case.workspace_id,
                 case_id=case.id, target_type="user", target_id=user.id, details={"role": body.role})
    await db.commit()
    await db.refresh(member)
    return _member_out(member, user)


@router.patch("/{case_id}/members/{user_id}", response_model=CaseMemberOut)
async def update_case_member(
    user_id: uuid.UUID,
    body: CaseMemberRoleUpdate,
    access: CaseAccess = Depends(require_case_role("lead")),
    db: AsyncSession = Depends(get_db),
):
    case = access.case
    member = await db.get(CaseMember, (case.id, user_id))
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Case member not found.")
    if member.role == "lead" and body.role != "lead" and await _lead_count(db, case.id) <= 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A case needs at least one lead.")
    member.role = body.role
    audit.record(db, audit.CASE_MEMBER_ROLE, user_id=access.user.id, workspace_id=case.workspace_id,
                 case_id=case.id, target_type="user", target_id=user_id, details={"role": body.role})
    await db.commit()
    user = await db.get(User, user_id)
    return _member_out(member, user)


@router.delete("/{case_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_case_member(
    user_id: uuid.UUID,
    access: CaseAccess = Depends(require_case_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """Leads remove anyone; everyone else may only remove themselves (leave)."""
    case = access.case
    if user_id != access.user.id and access.role != "lead":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role.")
    member = await db.get(CaseMember, (case.id, user_id))
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Case member not found.")
    if member.role == "lead" and await _lead_count(db, case.id) <= 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A case needs at least one lead.")
    await db.delete(member)
    audit.record(db, audit.CASE_MEMBER_REMOVE, user_id=access.user.id, workspace_id=case.workspace_id,
                 case_id=case.id, target_type="user", target_id=user_id)
    await db.commit()


# ── audit ─────────────────────────────────────────────────────────────────────

@router.get("/{case_id}/audit", response_model=List[AuditEventOut])
async def case_audit(
    limit: int = Query(50, ge=1, le=AUDIT_PAGE_MAX),
    access: CaseAccess = Depends(require_case_role("lead")),
    db: AsyncSession = Depends(get_db),
):
    rows = await db.execute(
        select(AuditEvent, User.email).outerjoin(User, User.id == AuditEvent.user_id)
        .where(AuditEvent.case_id == access.case.id)
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .limit(limit)
    )
    return [
        AuditEventOut(
            id=e.id, action=e.action, user_id=str(e.user_id) if e.user_id else None, user_email=email,
            target_type=e.target_type, target_id=e.target_id, details=e.details, created_at=e.created_at,
        )
        for e, email in rows.all()
    ]
