import asyncio
import uuid
from pathlib import Path
from typing import List

import aiofiles
from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile, Depends

from backend.core.config import settings
from backend.core.logger import logger
from backend.core.database import documents
from backend.schemas.documents import DocRecord
from backend.pipeline.document_processor import extract_text

from backend.app.dependencies import get_graphrag, get_raptor, get_hippo

router = APIRouter(prefix="/documents", tags=["documents"])

UPLOAD_DIR = Path(settings.UPLOAD_DIR) if settings.UPLOAD_DIR else Path("uploaded_docs")
MAX_FILE_MB = int(settings.MAX_FILE_SIZE_MB) if settings.MAX_FILE_SIZE_MB else 50
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

async def _run_indexing(doc_id: str, file_path: str, content_type: str, graphrag, raptor, hippo):
    logger.info(f"Starting indexing for document {doc_id} at {file_path}")
    documents[doc_id].status = "indexing"
    try:
        text = await extract_text(file_path, content_type)
        if not text.strip():
            raise ValueError("No text could be extracted from the document.")

        chunks = graphrag.chunk_text(text, doc_id)
        result = await graphrag.index_document(doc_id, text, chunks)
        logger.debug(f"GraphRAG indexing result for {doc_id}: {result}")
        
        await asyncio.gather(
            raptor.build_tree(chunks),
            hippo.index_passages(chunks),
            return_exceptions=True,
        )
        logger.debug(f"RAPTOR and HiPPO indexing completed for {doc_id}")

        documents[doc_id].status = "indexed"
        documents[doc_id].chunks = result["chunks"]
        logger.info(f"Completed indexing for document {doc_id}: {result['chunks']} chunks, {result['entities_total']} entities extracted")
    except Exception as exc:
        logger.error(f"Indexing failed for document {doc_id}: {exc}", exc_info=True)
        documents[doc_id].status = "error"
        documents[doc_id].error = str(exc)

@router.post("/upload", response_model=DocRecord)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    graphrag = Depends(get_graphrag),
    raptor = Depends(get_raptor),
    hippo = Depends(get_hippo)
):
    if not settings.GROQ_API_KEY:
        raise HTTPException(status_code=500, detail="GROQ_API_KEY not configured.")

    logger.info(f"Received upload request: filename={file.filename}, content_type={file.content_type}")

    size = 0
    doc_id = str(uuid.uuid4())
    dest = UPLOAD_DIR / f"{doc_id}_{file.filename}"

    async with aiofiles.open(dest, "wb") as out:
        while chunk := await file.read(1024 * 256):
            size += len(chunk)
            if size > MAX_FILE_MB * 1024 * 1024:
                await out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413, detail=f"File exceeds {MAX_FILE_MB} MB limit."
                )
            await out.write(chunk)
    logger.info(f"File {file.filename} saved to {dest} ({size} bytes)")

    record = DocRecord(
        id=doc_id,
        filename=file.filename or "unknown",
        content_type=file.content_type or "",
        size_bytes=size,
    )
    logger.debug(f"Document record created: {record}")
    documents[doc_id] = record
    background_tasks.add_task(
        _run_indexing, doc_id, str(dest), file.content_type or "", graphrag, raptor, hippo
    )
    logger.debug(f"Document {doc_id} saved to {dest}, indexing task scheduled")
    return record

@router.get("", response_model=List[DocRecord])
async def list_documents():
    logger.debug(f"Listing documents: {len(documents)} found")
    return list(documents.values())

@router.get("/{doc_id}", response_model=DocRecord)
async def get_document(doc_id: str):
    if doc_id not in documents:
        raise HTTPException(status_code=404, detail="Document not found.")
    return documents[doc_id]

@router.delete("/{doc_id}")
async def delete_document(doc_id: str):
    if doc_id not in documents:
        raise HTTPException(status_code=404, detail="Document not found.")
    logger.info(f"Deleting document {doc_id}")
    for f in UPLOAD_DIR.glob(f"{doc_id}_*"):
        f.unlink(missing_ok=True)
    del documents[doc_id]
    logger.info(f"Document {doc_id} deleted successfully")
    return {"deleted": doc_id}
