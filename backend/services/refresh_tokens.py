"""
Rotating refresh tokens.

fastapi-users issues short-lived access JWTs but has no refresh tokens, so they live here.
The raw token only ever exists in the httpOnly cookie; the DB stores its SHA-256 hash.
Presenting an already-rotated token is treated as theft: every token of that user is revoked.
"""
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import settings
from backend.core.logger import logger
from backend.models import RefreshToken, User

_TOKEN_BYTES = 48


class RefreshTokenError(Exception):
    """The refresh token is missing, unknown, expired or revoked."""


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def issue(session: AsyncSession, user_id: uuid.UUID, user_agent: Optional[str]) -> Tuple[str, RefreshToken]:
    raw = secrets.token_urlsafe(_TOKEN_BYTES)
    row = RefreshToken(
        user_id=user_id,
        token_hash=_hash(raw),
        expires_at=_now() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        user_agent=(user_agent or "")[:255] or None,
    )
    session.add(row)
    await session.flush()
    return raw, row


async def rotate(session: AsyncSession, raw: str, user_agent: Optional[str]) -> Tuple[User, str]:
    row = (await session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == _hash(raw))
    )).scalar_one_or_none()
    if row is None:
        raise RefreshTokenError("unknown token")
    if row.revoked_at is not None:
        logger.warning(f"Refresh token reuse detected for user {row.user_id}; revoking all sessions")
        await revoke_all(session, row.user_id)
        await session.commit()
        raise RefreshTokenError("token reuse")
    if row.expires_at <= _now():
        raise RefreshTokenError("expired")

    user = await session.get(User, row.user_id)
    if user is None or not user.is_active:
        raise RefreshTokenError("inactive user")

    new_raw, new_row = await issue(session, user.id, user_agent)
    row.revoked_at = _now()
    row.replaced_by = new_row.id
    return user, new_raw


async def revoke(session: AsyncSession, raw: str) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.token_hash == _hash(raw), RefreshToken.revoked_at.is_(None))
        .values(revoked_at=_now())
    )


async def revoke_all(session: AsyncSession, user_id: uuid.UUID) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=_now())
    )
