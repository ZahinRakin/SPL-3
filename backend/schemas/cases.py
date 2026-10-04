from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, EmailStr, Field

CaseStatus = Literal["open", "in_progress", "closed", "archived"]
CasePriority = Literal["low", "medium", "high", "critical"]
CaseRole = Literal["lead", "investigator", "viewer"]


class CaseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    reference_code: Optional[str] = Field(default=None, max_length=50)
    priority: CasePriority = "medium"


class CaseUpdate(BaseModel):
    """Only the fields that are sent change."""
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=5000)
    reference_code: Optional[str] = Field(default=None, max_length=50)
    status: Optional[CaseStatus] = None
    priority: Optional[CasePriority] = None


class CaseOut(BaseModel):
    id: str
    workspace_id: str
    reference_code: Optional[str]
    title: str
    description: str
    status: CaseStatus
    priority: CasePriority
    created_by: Optional[str]
    created_at: datetime
    updated_at: datetime
    closed_at: Optional[datetime]
    my_role: CaseRole
    document_count: int = 0


class CaseMemberAdd(BaseModel):
    email: EmailStr
    role: CaseRole = "investigator"


class CaseMemberRoleUpdate(BaseModel):
    role: CaseRole


class CaseMemberOut(BaseModel):
    user_id: str
    email: str
    full_name: str
    role: CaseRole
    created_at: datetime
