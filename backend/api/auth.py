from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import settings
from backend.core.database import get_db
from backend.core.logger import logger
from backend.schemas.auth import AuthConfig, TokenResponse, UserCreate, UserRead, UserUpdate
from backend.services import refresh_tokens
from backend.services.users import (
    auth_backend, clear_refresh_cookie, fastapi_users, get_jwt_strategy,
    google_oauth_client, token_response,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# ── fastapi-users routes ──────────────────────────────────────────────────────
# POST /auth/jwt/login  (form: username, password)   POST /auth/jwt/logout (all sessions)
router.include_router(fastapi_users.get_auth_router(auth_backend), prefix="/jwt")
# POST /auth/register
router.include_router(fastapi_users.get_register_router(UserRead, UserCreate))

if google_oauth_client is not None:
    # GET /auth/google/authorize → {authorization_url}; the SPA navigates there.
    # Google redirects to the SPA (/auth/google/callback), which calls
    # GET /api/auth/google/callback?code&state with credentials to get the token.
    router.include_router(
        fastapi_users.get_oauth_router(
            google_oauth_client,
            auth_backend,
            settings.JWT_SECRET_KEY,
            redirect_url=settings.google_redirect_url,
            associate_by_email=True,          # Google only returns verified primary emails
            is_verified_by_default=True,
            csrf_token_cookie_secure=settings.COOKIE_SECURE,
        ),
        prefix="/google",
    )
else:
    logger.warning("Google login disabled: GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET not set")


# ── our routes ────────────────────────────────────────────────────────────────

@router.get("/config", response_model=AuthConfig)
async def auth_config():
    return AuthConfig(google_enabled=google_oauth_client is not None)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request,
    refresh_token: Optional[str] = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
):
    if not refresh_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No refresh token.")
    try:
        user, new_raw = await refresh_tokens.rotate(db, refresh_token, request.headers.get("user-agent"))
    except refresh_tokens.RefreshTokenError as exc:
        logger.debug(f"Refresh rejected: {exc}")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token.")
    await db.commit()
    access = await get_jwt_strategy().write_token(user)
    return token_response(access, new_raw)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    refresh_token: Optional[str] = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Sign out this browser: revoke its refresh token and clear the cookie."""
    if refresh_token:
        await refresh_tokens.revoke(db, refresh_token)
        await db.commit()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_refresh_cookie(response)
    return response


# ── users (GET/PATCH /users/me) ───────────────────────────────────────────────

users_router = APIRouter(prefix="/users", tags=["users"])
users_router.include_router(fastapi_users.get_users_router(UserRead, UserUpdate))
