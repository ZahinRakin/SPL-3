import uuid
from datetime import datetime
from typing import Literal, Optional

from fastapi_users import schemas
from pydantic import BaseModel, Field


class UserRead(schemas.BaseUser[uuid.UUID]):
    full_name: str
    created_at: datetime


class UserCreate(schemas.BaseUserCreate):
    full_name: str = Field(min_length=1, max_length=120)


class UserUpdate(schemas.BaseUserUpdate):
    full_name: Optional[str] = Field(default=None, min_length=1, max_length=120)


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int            # seconds until the access token expires


class AuthConfig(BaseModel):
    google_enabled: bool
