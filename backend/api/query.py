import time
from typing import List

from fastapi import APIRouter, HTTPException, Depends, Query, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.dependencies import CaseAccess, get_case_index_registry, require_case_role
from backend.core.database import get_db
from backend.core.logger import logger
from backend.models import ChatMessage, Document, User
from backend.schemas.query import ChatMessageOut, QueryRequest, QueryResponse
from backend.services import audit
from backend.services.case_index_registry import CaseIndexRegistry

router = APIRouter(prefix="/cases/{case_id}", tags=["query"])

CHAT_PAGE_MAX = 500

@router.post("/query", response_model=QueryResponse)
async def query_documents(
    req: QueryRequest,
    access: CaseAccess = Depends(require_case_role("viewer")),
    db: AsyncSession = Depends(get_db),
    registry: CaseIndexRegistry = Depends(get_case_index_registry),
):
    case_id = access.case.id
    has_docs = (await db.execute(
        select(func.count()).select_from(Document).where(Document.case_id == case_id)
    )).scalar_one()
    if not has_docs:
        raise HTTPException(status_code=400, detail="No documents indexed yet.")

    logger.info(f"Query received: case={case_id}, method={req.method}, question={req.question[:80]!r}")
    bundle = await registry.get(case_id)
    t0 = time.perf_counter()
    result = await bundle.engine.query(req.question, req.method, req.top_k)
    latency_ms = int((time.perf_counter() - t0) * 1000)

    db.add(ChatMessage(
        case_id=case_id, user_id=access.user.id, question=req.question, method=req.method,
        top_k=req.top_k, answer=result["answer"], reasoning=result.get("reasoning", ""),
        confidence=result["confidence"], entities=result["entities"], sources=result["sources"],
        latency_ms=latency_ms,
    ))
    audit.record(db, audit.QUERY_RUN, user_id=access.user.id, workspace_id=access.case.workspace_id,
                 case_id=case_id, details={"method": req.method, "latency_ms": latency_ms})
    await db.commit()
    logger.info(
        f"Query answered: confidence={result.get('confidence')}, sources={result.get('sources')}, "
        f"latency_ms={latency_ms}"
    )
    return QueryResponse(**result)

@router.get("/query/suggestions")
async def get_suggestions(
    access: CaseAccess = Depends(require_case_role("viewer")),
    registry: CaseIndexRegistry = Depends(get_case_index_registry),
):
    bundle = await registry.get(access.case.id)
    return {"suggestions": bundle.engine.get_suggestions()}

# ── chat history ──────────────────────────────────────────────────────────────

@router.get("/chat", response_model=List[ChatMessageOut])
async def get_chat_history(
    limit: int = Query(100, ge=1, le=CHAT_PAGE_MAX),
    access: CaseAccess = Depends(require_case_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """The case's conversation, oldest first (the latest `limit` turns)."""
    rows = (await db.execute(
        select(ChatMessage, User.full_name).outerjoin(User, User.id == ChatMessage.user_id)
        .where(ChatMessage.case_id == access.case.id)
        .order_by(ChatMessage.created_at.desc())
        .limit(limit)
    )).all()
    return [
        ChatMessageOut(
            id=str(m.id), question=m.question, method=m.method, top_k=m.top_k, answer=m.answer,
            reasoning=m.reasoning, confidence=m.confidence, entities=m.entities, sources=m.sources,
            latency_ms=m.latency_ms, user_id=str(m.user_id) if m.user_id else None,
            user_name=name, created_at=m.created_at,
        )
        for m, name in reversed(rows)
    ]

@router.delete("/chat", status_code=status.HTTP_204_NO_CONTENT)
async def clear_chat_history(
    access: CaseAccess = Depends(require_case_role("lead")),
    db: AsyncSession = Depends(get_db),
    registry: CaseIndexRegistry = Depends(get_case_index_registry),
):
    case_id = access.case.id
    result = await db.execute(delete(ChatMessage).where(ChatMessage.case_id == case_id))
    audit.record(db, audit.CHAT_CLEAR, user_id=access.user.id, workspace_id=access.case.workspace_id,
                 case_id=case_id, details={"deleted": result.rowcount})
    await db.commit()
    # The engine's prompt history must forget the cleared turns too.
    (await registry.get(case_id)).engine.load_state({"history": []})
    logger.info(f"Chat history cleared for case {case_id} ({result.rowcount} messages)")
