"""
JWT Authentication for the investment backend.

Endpoints:
    POST /api/auth/register  — create a new user (returns access token)
    POST /api/auth/login     — authenticate, return access token
    GET  /api/auth/me        — return current user info (requires token)

Protection:
    Use `Depends(require_auth)` on any endpoint to require a valid JWT.
    The token is expected in the Authorization: Bearer <token> header.

Configuration (add to .env):
    JWT_SECRET_KEY=<random 32+ char string>
    JWT_ALGORITHM=HS256       (default)
    JWT_EXPIRE_MINUTES=1440   (default: 24 hours)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt
from jwt import InvalidTokenError
from passlib.context import CryptContext
from pydantic import BaseModel, Field
from sqlalchemy import String, DateTime, Integer, select
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import func

from .config import settings
from .db import Base, SessionLocal

# ─────────────────────────── Config ───────────────────────────────────────────

SECRET_KEY: str = settings.jwt_secret_key
ALGORITHM: str = "HS256"
EXPIRE_MINUTES: int = settings.jwt_expire_minutes

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer(auto_error=False)

# ─────────────────────────── DB Model ─────────────────────────────────────────

class AppUser(Base):
    """Users table — kept deliberately minimal."""
    __tablename__ = "app_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(256))
    is_active: Mapped[bool] = mapped_column(default=True)
    role: Mapped[str] = mapped_column(String(24), default="INVESTOR")
    token_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ─────────────────────────── Pydantic Schemas ─────────────────────────────────

class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$", description="Unique username")
    email: str = Field(..., min_length=5, max_length=128, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$", description="Valid email address")
    password: str = Field(..., min_length=12, max_length=256, description="Minimum 12 characters")


class LoginRequest(BaseModel):
    username: str = Field(..., description="Username or email")
    password: str = Field(..., min_length=1, max_length=256, description="Account password")


class TokenResponse(BaseModel):
    access_token: str = Field(..., description="JWT bearer token")
    token_type: str = Field("bearer", description="Always 'bearer'")
    expires_in: int = Field(..., description="Seconds until token expires")
    username: str


class UserInfo(BaseModel):
    id: int
    username: str
    email: str
    is_active: bool
    role: str
    created_at: datetime


# ─────────────────────────── Helpers ──────────────────────────────────────────

def _hash_password(password: str) -> str:
    return pwd_context.hash(password)


def _verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def _create_token(data: dict) -> str:
    payload = data.copy()
    now = datetime.now(timezone.utc)
    payload.update({
        "iat": now,
        "exp": now + timedelta(minutes=EXPIRE_MINUTES),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    })
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _decode_token(token: str) -> dict:
    return jwt.decode(
        token, SECRET_KEY, algorithms=[ALGORITHM],
        issuer=settings.jwt_issuer, audience=settings.jwt_audience,
    )


# ─────────────────────────── FastAPI Dependency ───────────────────────────────

async def require_auth(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> dict:
    """
    FastAPI dependency — raises HTTP 401 if no valid JWT is supplied.

    Usage::

        @router.get("/protected")
        async def protected(user: dict = Depends(require_auth)):
            return {"hello": user["sub"]}
    """
    exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated — supply a Bearer token from /api/auth/login",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise exc
    try:
        payload = _decode_token(credentials.credentials)
        if payload.get("sub") is None or payload.get("user_id") is None:
            raise exc
        async with SessionLocal() as db:
            user = (
                await db.execute(select(AppUser).where(AppUser.id == int(payload["user_id"])))
            ).scalar_one_or_none()
        if (
            user is None or not user.is_active or user.username != payload["sub"]
            or user.token_version != int(payload.get("token_version", 0))
        ):
            raise exc
        return {**payload, "role": user.role, "token_version": user.token_version}
    except (InvalidTokenError, TypeError, ValueError):
        raise exc


# ─────────────────────────── Router ───────────────────────────────────────────

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user account",
    description="Creates a user in the database and returns a JWT access token.",
)
async def register(body: RegisterRequest) -> TokenResponse:
    if settings.is_production:
        raise HTTPException(status_code=403, detail="Public registration is disabled")
    async with SessionLocal() as db:
        # Check username / email uniqueness
        existing = (
            await db.execute(
                select(AppUser).where(
                    (AppUser.username == body.username) | (AppUser.email == body.email)
                )
            )
        ).scalar_one_or_none()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Username or email already registered",
            )

        user = AppUser(
            username=body.username,
            email=body.email,
            hashed_password=_hash_password(body.password),
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

    token = _create_token({"sub": user.username, "user_id": user.id, "token_version": user.token_version})
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=EXPIRE_MINUTES * 60,
        username=user.username,
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Authenticate and receive a JWT",
    description="Accepts username and password, returns a short-lived bearer token.",
)
async def login(body: LoginRequest) -> TokenResponse:
    async with SessionLocal() as db:
        user = (
            await db.execute(
                select(AppUser).where(AppUser.username == body.username)
            )
        ).scalar_one_or_none()

    if user is None or not _verify_password(body.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    if not user.is_active:
        raise HTTPException(status_code=401, detail="Account is disabled")
    token = _create_token({"sub": user.username, "user_id": user.id, "token_version": user.token_version})
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=EXPIRE_MINUTES * 60,
        username=user.username,
    )


@router.get(
    "/me",
    response_model=UserInfo,
    summary="Return current authenticated user's info",
    description="Requires a valid Bearer token in the Authorization header.",
)
async def me(current_user: Annotated[dict, Depends(require_auth)]) -> UserInfo:
    async with SessionLocal() as db:
        user = (
            await db.execute(
                select(AppUser).where(AppUser.username == current_user["sub"])
            )
        ).scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    return UserInfo(
        id=user.id,
        username=user.username,
        email=user.email,
        is_active=user.is_active,
        role=user.role,
        created_at=user.created_at,
    )
