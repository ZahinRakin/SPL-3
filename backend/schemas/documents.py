from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel

class DocRecord(BaseModel):
    id: str
    filename: str
    content_type: str
    size_bytes: int
    status: Literal["uploaded", "indexing", "indexed", "error"] = "uploaded"
    error: Optional[str] = None
    chunks: int = 0
    case_id: str = ""
    sha256: str = ""
    uploaded_by: Optional[str] = None
    created_at: Optional[datetime] = None
