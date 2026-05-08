"""
GraphRAG Intelligence API — FastAPI backend.

Start with:
    cd graphrag-project
    uvicorn ui.app:app --reload --port 8000
"""
import asyncio
import os
import sys
import uuid
from pathlib import Path
from typing import Dict, List, Literal, Optional

import aiofiles # type: ignore
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile # type: ignore
from fastapi.middleware.cors import CORSMiddleware # type: ignore
from fastapi.responses import JSONResponse # type: ignore
from pydantic import BaseModel # type: ignore

from core.config import settings
from core.logger import logger

from pipeline.document_processor import extract_text
from pipeline.graphrag_indexer import GraphRAGIndexer
from pipeline.hippo_retriever import HippoRetriever
from pipeline.llm_provider import active_api_key_set, provider_info
from pipeline.query_engine import QueryEngine
from pipeline.raptor_runner import RaptorRunner

# ── config ────────────────────────────────────────────────────────────────────

UPLOAD_DIR = Path(settings.UPLOAD_DIR) if settings.UPLOAD_DIR else Path("uploaded_docs")
MAX_FILE_MB = int(settings.MAX_FILE_SIZE_MB) if settings.MAX_FILE_SIZE_MB else 50
CORS_ORIGINS = settings.CORS_ORIGINS.split(",") if settings.CORS_ORIGINS else ["*"]

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ── singleton pipeline objects ────────────────────────────────────────────────

graphrag = GraphRAGIndexer()
raptor = RaptorRunner()
hippo = HippoRetriever()
query_engine = QueryEngine(graphrag=graphrag, raptor=raptor, hippo=hippo)
logger.info("Pipeline components initialized: GraphRAGIndexer, RaptorRunner, HippoRetriever, QueryEngine")

# ── in-memory document registry ──────────────────────────────────────────────

class DocRecord(BaseModel):
    id: str
    filename: str
    content_type: str
    size_bytes: int
    status: Literal["uploaded", "indexing", "indexed", "error"] = "uploaded"
    error: Optional[str] = None
    chunks: int = 0

documents: Dict[str, DocRecord] = {}

# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="GraphRAG Document Intelligence API",
    description="Upload documents, build a knowledge graph, and query across them.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── pydantic schemas ──────────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    question: str
    method: Literal["graphrag", "raptor", "hippo", "hybrid"] = "hybrid"
    top_k: int = 6


class QueryResponse(BaseModel):
    answer: str
    entities: List[Dict]
    sources: List[str]
    confidence: float
    reasoning: str
    method: str


# ── helpers ───────────────────────────────────────────────────────────────────

async def _run_indexing(doc_id: str, file_path: str, content_type: str):
    logger.info(f"Starting indexing for document {doc_id} at {file_path}")
    documents[doc_id].status = "indexing"
    try:
        text = await extract_text(file_path, content_type)
        if not text.strip():
            raise ValueError("No text could be extracted from the document.")

        chunks = graphrag.chunk_text(text, doc_id)
        # Pass pre-computed chunks so index_document doesn't re-chunk the text.
        result = await graphrag.index_document(doc_id, text, chunks)
        logger.debug(f"GraphRAG indexing result for {doc_id}: {result}")
        # RAPTOR and HiPPO are independent — run them concurrently.
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


# ── routes: documents ─────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "api_key_set": active_api_key_set(), **provider_info()}


@app.post("/api/documents/upload", response_model=DocRecord)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    if not settings.GROQ_API_KEY:
        raise HTTPException(status_code=500, detail="GROQ_API_KEY not configured.")

    logger.info(f"Received upload request: filename={file.filename}, content_type={file.content_type}, size={file.spool_max_size} bytes")

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
        _run_indexing, doc_id, str(dest), file.content_type or ""
    )
    logger.debug(f"Document {doc_id} saved to {dest}, indexing task scheduled")
    return record


@app.get("/api/documents", response_model=List[DocRecord])
async def list_documents():
    logger.debug(f"Listing documents: {len(documents)} found")
    return list(documents.values())


@app.get("/api/documents/{doc_id}", response_model=DocRecord)
async def get_document(doc_id: str):
    if doc_id not in documents:
        raise HTTPException(status_code=404, detail="Document not found.")
    return documents[doc_id]


@app.delete("/api/documents/{doc_id}")
async def delete_document(doc_id: str):
    if doc_id not in documents:
        raise HTTPException(status_code=404, detail="Document not found.")
    logger.info(f"Deleting document {doc_id}")
    for f in UPLOAD_DIR.glob(f"{doc_id}_*"):
        f.unlink(missing_ok=True)
    del documents[doc_id]
    logger.info(f"Document {doc_id} deleted successfully")
    return {"deleted": doc_id}


# ── routes: graph ─────────────────────────────────────────────────────────────

@app.get("/api/graph")
async def get_graph():
    logger.debug("Fetching graph data")
    return graphrag.get_graph_data()


@app.get("/api/graph/stats")
async def get_graph_stats():
    logger.debug("Fetching graph statistics")
    stats = graphrag.get_stats()
    stats["raptor"] = raptor.get_stats()
    stats["hippo"] = hippo.get_stats()
    stats["indexed_documents"] = sum(
        1 for d in documents.values() if d.status == "indexed"
    )
    logger.debug(f"Graph statistics: {stats}")
    return stats


@app.get("/api/graph/entity/{entity_id}")
async def get_entity(entity_id: str):
    ent = graphrag.entities.get(entity_id)
    if not ent:
        raise HTTPException(status_code=404, detail="Entity not found.")
    nbrs = [
        {"id": n, "name": graphrag.entities[n].name, "type": graphrag.entities[n].type}
        for n in graphrag.graph.neighbors(entity_id)
        if n in graphrag.entities
    ]
    return {
        "id": ent.id,
        "name": ent.name,
        "type": ent.type,
        "description": ent.description,
        "neighbors": nbrs,
        "doc_count": len(ent.source_chunks),
        "community": graphrag.graph.nodes.get(entity_id, {}).get("community", -1),
    }


# ── routes: query ─────────────────────────────────────────────────────────────

@app.post("/api/query", response_model=QueryResponse)
async def query_documents(req: QueryRequest):
    if not documents:
        raise HTTPException(status_code=400, detail="No documents indexed yet.")
    logger.info(f"Query received: method={req.method}, question={req.question[:80]!r}")
    result = await query_engine.query(req.question, req.method, req.top_k)
    logger.info(f"Query answered: confidence={result.get('confidence')}, sources={result.get('sources')}")
    return QueryResponse(**result)


@app.get("/api/query/suggestions")
async def get_suggestions():
    return {"suggestions": query_engine.get_suggestions()}


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("ui.app:app", host="0.0.0.0", port=8000, reload=True)
