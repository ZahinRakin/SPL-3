from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel


class AuditEventOut(BaseModel):
    id: int
    action: str
    user_id: Optional[str]
    user_email: Optional[str]
    target_type: Optional[str]
    target_id: Optional[str]
    details: Dict[str, Any]
    created_at: datetime
