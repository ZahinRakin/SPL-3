from fastapi import APIRouter, HTTPException, Depends
from backend.core.logger import logger
from backend.core.database import documents
from backend.app.dependencies import get_graphrag, get_raptor, get_hippo

router = APIRouter(prefix="/graph", tags=["graph"])

@router.get("")
async def get_graph(graphrag = Depends(get_graphrag)):
    logger.debug("Fetching graph data")
    return graphrag.get_graph_data()

@router.get("/stats")
async def get_graph_stats(
    graphrag = Depends(get_graphrag),
    raptor = Depends(get_raptor),
    hippo = Depends(get_hippo)
):
    logger.debug("Fetching graph statistics")
    stats = graphrag.get_stats()
    stats["raptor"] = raptor.get_stats()
    stats["hippo"] = hippo.get_stats()
    stats["indexed_documents"] = sum(
        1 for d in documents.values() if d.status == "indexed"
    )
    logger.debug(f"Graph statistics: {stats}")
    return stats

@router.get("/entity/{entity_id}")
async def get_entity(entity_id: str, graphrag = Depends(get_graphrag)):
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
