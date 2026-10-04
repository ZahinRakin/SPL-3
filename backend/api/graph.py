from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.dependencies import CaseAccess, get_case_index_registry, require_case_role
from backend.core.database import get_db
from backend.core.logger import logger
from backend.models import Document
from backend.services.case_index_registry import CaseIndexRegistry

router = APIRouter(prefix="/cases/{case_id}/graph", tags=["graph"])

@router.get("")
async def get_graph(
    access: CaseAccess = Depends(require_case_role("viewer")),
    registry: CaseIndexRegistry = Depends(get_case_index_registry),
):
    logger.debug(f"Fetching graph data for case {access.case.id}")
    bundle = await registry.get(access.case.id)
    return bundle.graphrag.get_graph_data()

@router.get("/stats")
async def get_graph_stats(
    access: CaseAccess = Depends(require_case_role("viewer")),
    db: AsyncSession = Depends(get_db),
    registry: CaseIndexRegistry = Depends(get_case_index_registry),
):
    bundle = await registry.get(access.case.id)
    stats = bundle.graphrag.get_stats()
    stats["raptor"] = bundle.raptor.get_stats()
    stats["hippo"] = bundle.hippo.get_stats()
    stats["indexed_documents"] = (await db.execute(
        select(func.count()).select_from(Document)
        .where(Document.case_id == access.case.id, Document.status == "indexed")
    )).scalar_one()
    logger.debug(f"Graph statistics for case {access.case.id}: {stats}")
    return stats

@router.get("/entity/{entity_id}")
async def get_entity(
    entity_id: str,
    access: CaseAccess = Depends(require_case_role("viewer")),
    registry: CaseIndexRegistry = Depends(get_case_index_registry),
):
    graphrag = (await registry.get(access.case.id)).graphrag
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
