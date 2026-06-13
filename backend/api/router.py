from fastapi import APIRouter
from backend.api import documents, graph, query

api_router = APIRouter(prefix="/api")

api_router.include_router(documents.router)
api_router.include_router(graph.router)
api_router.include_router(query.router)
