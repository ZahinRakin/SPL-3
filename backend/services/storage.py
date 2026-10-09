"""Evidence files on disk: {UPLOAD_DIR}/{case_id}/{doc_id}_{safe_filename}. Postgres keeps path + SHA-256."""
import asyncio
import re
import shutil
import uuid
from pathlib import Path

from backend.core.config import settings
from backend.core.logger import logger

UPLOAD_DIR = settings.BASE_DIR / (settings.UPLOAD_DIR or "data/uploads")
MAX_FILE_MB = int(settings.MAX_FILE_SIZE_MB) if settings.MAX_FILE_SIZE_MB else 50
MAX_FILENAME_CHARS = 100

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def resolve_stored_path(path: str) -> Path:
    """Keep existing root-relative evidence paths valid when launched from backend/."""
    return settings.BASE_DIR / path


def safe_filename(name: str) -> str:
    """Strip any directory part and odd characters, so '..\\..\\x' can't escape the case folder."""
    base = Path(name.replace("\\", "/")).name or "file"
    return _UNSAFE.sub("_", base)[:MAX_FILENAME_CHARS]


def case_dir(case_id: uuid.UUID) -> Path:
    return UPLOAD_DIR / str(case_id)


def document_path(case_id: uuid.UUID, doc_id: uuid.UUID, filename: str) -> Path:
    return case_dir(case_id) / f"{doc_id}_{safe_filename(filename)}"


async def remove_case_files(case_id: uuid.UUID) -> None:
    await asyncio.to_thread(shutil.rmtree, case_dir(case_id), True)
    logger.debug(f"Removed upload folder for case {case_id}")
