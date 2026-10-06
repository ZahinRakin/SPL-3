import asyncio
import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import aiofiles
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.dependencies import CaseAccess, get_case_index_registry, require_case_role
from backend.core.config import settings
from backend.core.database import get_db, session_scope
from backend.core.logger import logger
from backend.models import Document
from backend.pipeline.document_processor import extract_text
from backend.schemas.documents import DocRecord
from backend.services import audit
from backend.services.case_index_registry import CaseIndexRegistry
from backend.services.index_store import save_case_state, save_chunks
from backend.services.storage import MAX_FILE_MB, document_path

router = APIRouter(prefix="/cases/{case_id}/documents", tags=["documents"])

UPLOAD_CHUNK_BYTES = 1024 * 256
CLOSED_STATUSES = ("closed", "archived")


def to_record(doc: Document) -> DocRecord:
    return DocRecord(
        id=str(doc.id), filename=doc.filename, content_type=doc.content_type, size_bytes=doc.size_bytes,
        status=doc.status, error=doc.error, chunks=doc.chunk_count, case_id=str(doc.case_id),
        sha256=doc.sha256, uploaded_by=str(doc.uploaded_by) if doc.uploaded_by else None,
        created_at=doc.created_at,
    )


async def _get_doc(db: AsyncSession, case_id: uuid.UUID, doc_id: uuid.UUID) -> Document:
    # Always filter by case too, so a doc id from another case is just "not found".
    doc = (await db.execute(
        select(Document).where(Document.id == doc_id, Document.case_id == case_id)
    )).scalar_one_or_none()
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.")
    return doc


# ── background indexing ───────────────────────────────────────────────────────

async def _set_doc(doc_id: uuid.UUID, **values) -> None:
    async with session_scope() as s:
        await s.execute(update(Document).where(Document.id == doc_id).values(**values))


async def _run_indexing(
    case_id: uuid.UUID, doc_id: uuid.UUID, file_path: str, content_type: str,
    user_id: Optional[uuid.UUID], registry: CaseIndexRegistry,
):
    bundle = await registry.get(case_id)
    async with bundle.lock:
        logger.info(f"Starting indexing for document {doc_id} in case {case_id}")
        await _set_doc(doc_id, status="indexing", indexing_started_at=datetime.now(timezone.utc))
        try:
            text = await extract_text(file_path, content_type)
            if not text.strip():
                raise ValueError("No text could be extracted from the document.")

            chunks = bundle.graphrag.chunk_text(text, str(doc_id))
            # All three indexes build at the same time (decision D5): indexing takes as long
            # as the slowest one instead of GraphRAG + the slower of RAPTOR/HiPPO.
            graph_result, raptor_result, hippo_result = await asyncio.gather(
                bundle.graphrag.index_document(str(doc_id), text, chunks),
                bundle.raptor.build_tree(chunks),
                bundle.hippo.index_passages(chunks),
                return_exceptions=True,
            )
            # A GraphRAG failure fails the document; RAPTOR/HiPPO failures degrade (D6).
            if isinstance(graph_result, BaseException):
                raise graph_result
            result = graph_result
            for name, outcome in (("RAPTOR", raptor_result), ("HiPPO", hippo_result)):
                if isinstance(outcome, BaseException):
                    logger.warning(f"{name} indexing failed for {doc_id}: {outcome!r}")
            logger.debug(f"GraphRAG, RAPTOR and HiPPO indexing completed for {doc_id}: {result}")

            # One transaction: chunks + index snapshot + status, so the DB never
            # says "indexed" without the index rows to back it.
            async with session_scope() as s:
                await save_chunks(s, case_id, doc_id, chunks)
                await save_case_state(s, case_id, bundle.graphrag, bundle.raptor, bundle.hippo)
                await s.execute(update(Document).where(Document.id == doc_id).values(
                    status="indexed", chunk_count=result["chunks"], error=None,
                    indexed_at=datetime.now(timezone.utc),
                ))
                audit.record(s, audit.DOCUMENT_INDEXED, user_id=user_id, case_id=case_id,
                             target_type="document", target_id=doc_id,
                             details={"chunks": result["chunks"], "entities_total": result["entities_total"]})
            logger.info(
                f"Completed indexing for document {doc_id}: {result['chunks']} chunks, "
                f"{result['entities_total']} entities in case"
            )
        except Exception as exc:
            logger.error(f"Indexing failed for document {doc_id}: {exc}", exc_info=True)
            try:
                async with session_scope() as s:
                    await s.execute(update(Document).where(Document.id == doc_id).values(
                        status="error", error=str(exc)[:2000]))
                    audit.record(s, audit.DOCUMENT_INDEX_FAILED, user_id=user_id, case_id=case_id,
                                 target_type="document", target_id=doc_id, details={"error": str(exc)[:500]})
                # The in-memory indexes may be half-updated; reset them to what the DB holds.
                await registry.reload(case_id, bundle)
            except Exception as inner:
                logger.error(f"Recovery after failed indexing of {doc_id} failed: {inner}", exc_info=True)
                registry.evict(case_id)


# ── routes ────────────────────────────────────────────────────────────────────

@router.post("/upload", response_model=DocRecord)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    access: CaseAccess = Depends(require_case_role("investigator")),
    db: AsyncSession = Depends(get_db),
    registry: CaseIndexRegistry = Depends(get_case_index_registry),
):
    case = access.case
    if case.status in CLOSED_STATUSES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Case is {case.status}; reopen it to add evidence.")
    if not settings.LLM_API_KEY:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "LLM_API_KEY not configured.")

    logger.info(f"Received upload: case={case.id}, filename={file.filename!r}, content_type={file.content_type}")
    doc_id = uuid.uuid4()
    dest: Path = document_path(case.id, doc_id, file.filename or "file")
    dest.parent.mkdir(parents=True, exist_ok=True)

    size = 0
    digest = hashlib.sha256()
    async with aiofiles.open(dest, "wb") as out:
        while chunk := await file.read(UPLOAD_CHUNK_BYTES):
            size += len(chunk)
            if size > MAX_FILE_MB * 1024 * 1024:
                await out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"File exceeds {MAX_FILE_MB} MB limit.")
            digest.update(chunk)
            await out.write(chunk)
    logger.info(f"File {file.filename!r} saved to {dest} ({size} bytes)")

    doc = Document(
        id=doc_id, case_id=case.id, uploaded_by=access.user.id,
        filename=(file.filename or "unknown")[:255], stored_path=str(dest),
        content_type=file.content_type or "", size_bytes=size, sha256=digest.hexdigest(),
    )
    db.add(doc)
    audit.record(db, audit.DOCUMENT_UPLOAD, user_id=access.user.id, workspace_id=case.workspace_id,
                 case_id=case.id, target_type="document", target_id=doc_id,
                 details={"filename": doc.filename, "sha256": doc.sha256, "size_bytes": size})
    await db.commit()
    await db.refresh(doc)

    background_tasks.add_task(
        _run_indexing, case.id, doc_id, str(dest), file.content_type or "", access.user.id, registry
    )
    logger.debug(f"Document {doc_id} indexing task scheduled")
    return to_record(doc)


@router.get("", response_model=List[DocRecord])
async def list_documents(access: CaseAccess = Depends(require_case_role("viewer")), db: AsyncSession = Depends(get_db)):
    docs = (await db.execute(
        select(Document).where(Document.case_id == access.case.id).order_by(Document.created_at)
    )).scalars().all()
    logger.debug(f"Listing documents for case {access.case.id}: {len(docs)} found")
    return [to_record(d) for d in docs]


@router.get("/{doc_id}", response_model=DocRecord)
async def get_document(
    doc_id: uuid.UUID,
    access: CaseAccess = Depends(require_case_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    return to_record(await _get_doc(db, access.case.id, doc_id))


@router.delete("/{doc_id}")
async def delete_document(
    doc_id: uuid.UUID,
    access: CaseAccess = Depends(require_case_role("lead")),
    db: AsyncSession = Depends(get_db),
):
    # Known limitation (ARCHITECTURE.md §8): this does not remove the document's
    # knowledge from the case's indexes.
    doc = await _get_doc(db, access.case.id, doc_id)
    logger.info(f"Deleting document {doc_id} from case {access.case.id}")
    path = Path(doc.stored_path)
    audit.record(db, audit.DOCUMENT_DELETE, user_id=access.user.id, workspace_id=access.case.workspace_id,
                 case_id=access.case.id, target_type="document", target_id=doc_id,
                 details={"filename": doc.filename, "sha256": doc.sha256})
    await db.delete(doc)
    await db.commit()
    await asyncio.to_thread(path.unlink, True)
    return {"deleted": str(doc_id)}
