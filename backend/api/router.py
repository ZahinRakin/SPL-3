from fastapi import APIRouter
from backend.api import auth, cases, documents, graph, query, workspaces

api_router = APIRouter(prefix="/api")

api_router.include_router(auth.router)
api_router.include_router(auth.users_router)
api_router.include_router(workspaces.router)
api_router.include_router(cases.router)
api_router.include_router(documents.router)
api_router.include_router(graph.router)
api_router.include_router(query.router)
