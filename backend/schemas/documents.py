from typing import Dict, Literal, Optional
from pydantic import BaseModel

class DocRecord(BaseModel):
    id: str
    filename: str
    content_type: str
    size_bytes: int
    status: Literal["uploaded", "indexing", "indexed", "error"] = "uploaded"
    error: Optional[str] = None
    chunks: int = 0
