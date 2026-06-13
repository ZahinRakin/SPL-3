from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.core.config import settings
from backend.core.logger import logger
from backend.api.router import api_router
from backend.pipeline.llm_provider import active_api_key_set, provider_info

app = FastAPI(
    title="GraphRAG Document Intelligence API",
    description="Upload documents, build a knowledge graph, and query across them.",
    version="1.0.0",
)

CORS_ORIGINS = settings.CORS_ORIGINS.split(",") if settings.CORS_ORIGINS else ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)

@app.get("/api/health")
async def health():
    return {"status": "ok", "api_key_set": active_api_key_set(), **provider_info()}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.app.main:app", host="0.0.0.0", port=8000, reload=True)
