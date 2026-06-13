from typing import Dict
from backend.schemas.documents import DocRecord

# In-memory document registry
documents: Dict[str, DocRecord] = {}
