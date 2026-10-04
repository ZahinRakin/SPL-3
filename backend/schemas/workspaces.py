from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field

WorkspaceRole = Literal["owner", "admin", "member"]
WorkspaceKind = Literal["personal", "organization"]


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class WorkspaceUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class WorkspaceOut(BaseModel):
    id: str
    name: str
    kind: WorkspaceKind
    my_role: WorkspaceRole
    member_count: int = 1
    created_at: datetime


class WorkspaceMemberAdd(BaseModel):
    email: EmailStr
    role: Literal["admin", "member"] = "member"     # ownership isn't granted by invitation


class WorkspaceMemberRoleUpdate(BaseModel):
    role: Literal["admin", "member"]


class WorkspaceMemberOut(BaseModel):
    user_id: str
    email: str
    full_name: str
    role: WorkspaceRole
    created_at: datetime
