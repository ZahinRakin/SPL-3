"""
fastapi-users wiring: user manager, auth backend, Google OAuth client.

Access tokens: JWT (fastapi-users JWTStrategy), sent as `Authorization: Bearer`, held in memory
by the SPA. Refresh tokens: our own rotating opaque tokens in an httpOnly cookie
(services/refresh_tokens.py), because fastapi-users has no refresh tokens.
"""
import uuid
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

from fastapi import Depends, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi_users import BaseUserManager, FastAPIUsers, InvalidPasswordException, UUIDIDMixin
from fastapi_users.authentication import AuthenticationBackend, BearerTransport, JWTStrategy, Strategy
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from httpx_oauth.clients.google import GoogleOAuth2
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import settings
from backend.core.database import SessionLocal, get_db
from backend.core.logger import logger
from backend.models import OAuthAccount, User, Workspace, WorkspaceMember
from backend.schemas.auth import TokenResponse
from backend.services import audit, refresh_tokens

MIN_PASSWORD_LENGTH = 8
REFRESH_COOKIE = "refresh_token"
REFRESH_COOKIE_PATH = "/api/auth"     # the browser only sends it to auth endpoints


# ── personal workspace ────────────────────────────────────────────────────────

async def ensure_personal_workspace(session: AsyncSession, user: User) -> None:
    """Every user owns exactly one personal workspace. Idempotent."""
    existing = (await session.execute(
        select(Workspace.id)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(Workspace.kind == "personal", WorkspaceMember.user_id == user.id)
    )).first()
    if existing:
        return
    ws = Workspace(name=f"{user.full_name or user.email}'s workspace"[:120], kind="personal", created_by=user.id)
    session.add(ws)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner"))
    audit.record(session, audit.WORKSPACE_CREATE, user_id=user.id, workspace_id=ws.id,
                 target_type="workspace", target_id=ws.id, details={"kind": "personal"})


# ── user manager ──────────────────────────────────────────────────────────────

class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    # Only used by the reset/verify routers, which aren't mounted (no email server).
    reset_password_token_secret = settings.JWT_SECRET_KEY
    verification_token_secret = settings.JWT_SECRET_KEY

    @property
    def session(self) -> AsyncSession:
        return self.user_db.session  # type: ignore[attr-defined]

    async def validate_password(self, password: str, user) -> None:
        if len(password) < MIN_PASSWORD_LENGTH:
            raise InvalidPasswordException(
                reason=f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
            )
        if user.email and user.email.split("@")[0].lower() in password.lower():
            raise InvalidPasswordException(reason="Password must not contain your email name.")

    async def on_after_register(self, user: User, request: Optional[Request] = None) -> None:
        # Google sign-ups arrive without a name.
        if not user.full_name:
            user.full_name = user.email.split("@")[0][:120]
        await ensure_personal_workspace(self.session, user)
        audit.record(self.session, audit.AUTH_REGISTER, user_id=user.id,
                     details={"oauth": bool(user.oauth_accounts)})
        await self.session.commit()
        logger.info(f"User registered: {user.id}")

    async def on_after_login(
        self, user: User, request: Optional[Request] = None, response: Optional[Response] = None
    ) -> None:
        user.last_login_at = datetime.now(timezone.utc)
        audit.record(self.session, audit.AUTH_LOGIN, user_id=user.id)
        await self.session.commit()
        logger.info(f"User logged in: {user.id}")


async def get_user_db(session: AsyncSession = Depends(get_db)) -> AsyncIterator[SQLAlchemyUserDatabase]:
    yield SQLAlchemyUserDatabase(session, User, OAuthAccount)


async def get_user_manager(user_db: SQLAlchemyUserDatabase = Depends(get_user_db)) -> AsyncIterator[UserManager]:
    yield UserManager(user_db)


# ── token responses ───────────────────────────────────────────────────────────

def token_response(access_token: str, refresh_raw: str) -> JSONResponse:
    body = TokenResponse(access_token=access_token, expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60)
    response = JSONResponse(body.model_dump())
    response.set_cookie(
        REFRESH_COOKIE, refresh_raw,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        httponly=True, secure=settings.COOKIE_SECURE, samesite="lax",
        path=REFRESH_COOKIE_PATH,
    )
    return response


def clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        REFRESH_COOKIE, path=REFRESH_COOKIE_PATH,
        httponly=True, secure=settings.COOKIE_SECURE, samesite="lax",
    )


# ── auth backend ──────────────────────────────────────────────────────────────

def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(
        secret=settings.JWT_SECRET_KEY,
        lifetime_seconds=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


class RefreshCookieBackend(AuthenticationBackend[User, uuid.UUID]):
    """Bearer JWT + a refresh cookie on every login (password or Google)."""

    async def login(self, strategy: Strategy[User, uuid.UUID], user: User) -> Response:
        access = await strategy.write_token(user)
        async with SessionLocal() as session:
            raw, _ = await refresh_tokens.issue(session, user.id, user_agent=None)
            await session.commit()
        return token_response(access, raw)

    async def logout(self, strategy: Strategy[User, uuid.UUID], user: User, token: str) -> Response:
        # /auth/jwt/logout = sign out everywhere. /auth/logout signs out this browser only.
        async with SessionLocal() as session:
            await refresh_tokens.revoke_all(session, user.id)
            await session.commit()
        response = Response(status_code=status.HTTP_204_NO_CONTENT)
        clear_refresh_cookie(response)
        return response


auth_backend = RefreshCookieBackend(
    name="jwt",
    transport=BearerTransport(tokenUrl="api/auth/jwt/login"),   # also powers /docs "Authorize"
    get_strategy=get_jwt_strategy,
)

fastapi_users = FastAPIUsers[User, uuid.UUID](get_user_manager, [auth_backend])
current_active_user = fastapi_users.current_user(active=True)

google_oauth_client: Optional[GoogleOAuth2] = (
    GoogleOAuth2(settings.GOOGLE_CLIENT_ID, settings.GOOGLE_CLIENT_SECRET)
    if settings.google_enabled else None
)
