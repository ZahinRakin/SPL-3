from fastapi import APIRouter, HTTPException, Depends
from backend.core.logger import logger
from backend.core.database import documents
from backend.schemas.query import QueryRequest, QueryResponse
from backend.app.dependencies import get_query_engine

router = APIRouter(prefix="/query", tags=["query"])

@router.post("", response_model=QueryResponse)
async def query_documents(req: QueryRequest, query_engine = Depends(get_query_engine)):
    if not documents:
        raise HTTPException(status_code=400, detail="No documents indexed yet.")
    logger.info(f"Query received: method={req.method}, question={req.question[:80]!r}")
    result = await query_engine.query(req.question, req.method, req.top_k)
    logger.info(f"Query answered: confidence={result.get('confidence')}, sources={result.get('sources')}")
    return QueryResponse(**result)

@router.get("/suggestions")
async def get_suggestions(query_engine = Depends(get_query_engine)):
    return {"suggestions": query_engine.get_suggestions()}
