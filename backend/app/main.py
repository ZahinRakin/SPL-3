import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Lets `uvicorn app.main:app` run from inside backend/: the code imports itself as
# `backend.…`, so the folder containing backend/ must be on sys.path.
_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text, update

from backend.core.config import settings
from backend.core.database import engine, session_scope
from backend.core.logger import logger
from backend.api.router import api_router
from backend.models import Document
from backend.pipeline.llm_provider import active_api_key_set, provider_info


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.JWT_SECRET_KEY:
        raise RuntimeError("JWT_SECRET_KEY is not set. Add it to backend/.env (see backend/.env.example).")
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    logger.info("Database connected")

    # Background indexing doesn't survive a restart; without this, documents would
    # show "indexing" forever.
    async with session_scope() as session:
        result = await session.execute(
            update(Document)
            .where(Document.status.in_(("uploaded", "indexing")))
            .values(status="error", error="Interrupted by server restart. Please re-upload.")
        )
    if result.rowcount:
        logger.warning(f"Marked {result.rowcount} interrupted document(s) as error")

    yield
    await engine.dispose()


app = FastAPI(
    title="GraphRAG Investigation API",
    description="Register, open investigation cases, upload evidence, and query it with a RAPTOR → GraphRAG → HippoRAG cascade.",
    version="2.0.0",
    lifespan=lifespan,
)

CORS_ORIGINS = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]

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
