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

import aiofiles
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# allow `from pipeline import ...` regardless of working directory
sys.path.insert(0, str(Path(__file__).parent.parent))

load_dotenv()

from pipeline.document_processor import extract_text
from pipeline.graphrag_indexer import GraphRAGIndexer
from pipeline.hippo_retriever import HippoRetriever
from pipeline.llm_provider import active_api_key_set, provider_info
from pipeline.query_engine import QueryEngine
from pipeline.raptor_runner import RaptorRunner

# ── config ────────────────────────────────────────────────────────────────────

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "./uploads"))
MAX_FILE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "50"))
CORS_ORIGINS = os.getenv(
    "CORS_ORIGINS", "http://localhost:4200,http://localhost:3000"
).split(",")

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ── singleton pipeline objects ────────────────────────────────────────────────

graphrag = GraphRAGIndexer()
raptor = RaptorRunner()
hippo = HippoRetriever()
query_engine = QueryEngine(graphrag=graphrag, raptor=raptor, hippo=hippo)

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
    documents[doc_id].status = "indexing"
    try:
        text = await extract_text(file_path, content_type)
        if not text.strip():
            raise ValueError("No text could be extracted from the document.")

        chunks = graphrag.chunk_text(text, doc_id)
        # Pass pre-computed chunks so index_document doesn't re-chunk the text.
        result = await graphrag.index_document(doc_id, text, chunks)
        # RAPTOR and HiPPO are independent — run them concurrently.
        await asyncio.gather(
            raptor.build_tree(chunks),
            hippo.index_passages(chunks),
            return_exceptions=True,
        )

        documents[doc_id].status = "indexed"
        documents[doc_id].chunks = result["chunks"]
    except Exception as exc:
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
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY not configured.")

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

    record = DocRecord(
        id=doc_id,
        filename=file.filename or "unknown",
        content_type=file.content_type or "",
        size_bytes=size,
    )
    documents[doc_id] = record
    background_tasks.add_task(
        _run_indexing, doc_id, str(dest), file.content_type or ""
    )
    return record


@app.get("/api/documents", response_model=List[DocRecord])
async def list_documents():
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
    # remove uploaded file
    for f in UPLOAD_DIR.glob(f"{doc_id}_*"):
        f.unlink(missing_ok=True)
    del documents[doc_id]
    return {"deleted": doc_id}


# ── routes: graph ─────────────────────────────────────────────────────────────

@app.get("/api/graph")
async def get_graph():
    return graphrag.get_graph_data()


@app.get("/api/graph/stats")
async def get_graph_stats():
    stats = graphrag.get_stats()
    stats["raptor"] = raptor.get_stats()
    stats["hippo"] = hippo.get_stats()
    stats["indexed_documents"] = sum(
        1 for d in documents.values() if d.status == "indexed"
    )
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
    result = await query_engine.query(req.question, req.method, req.top_k)
    return QueryResponse(**result)


@app.get("/api/query/suggestions")
async def get_suggestions():
    return {"suggestions": query_engine.get_suggestions()}


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("ui.app:app", host="0.0.0.0", port=8000, reload=True)
