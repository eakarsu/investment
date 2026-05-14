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

import os
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, Field
from sqlalchemy import String, DateTime, Integer, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import func

from .db import Base, SessionLocal

# ─────────────────────────── Config ───────────────────────────────────────────

SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "change-me-in-production-32chars!!!")
ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
EXPIRE_MINUTES: int = int(os.getenv("JWT_EXPIRE_MINUTES", "1440"))  # 24 h

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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ─────────────────────────── Pydantic Schemas ─────────────────────────────────

class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64, description="Unique username")
    email: str = Field(..., description="Valid email address")
    password: str = Field(..., min_length=8, description="Minimum 8 characters")


class LoginRequest(BaseModel):
    username: str = Field(..., description="Username or email")
    password: str = Field(..., description="Account password")


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
    created_at: datetime


# ─────────────────────────── Helpers ──────────────────────────────────────────

def _hash_password(password: str) -> str:
    return pwd_context.hash(password)


def _verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def _create_token(data: dict) -> str:
    payload = data.copy()
    payload["exp"] = datetime.utcnow() + timedelta(minutes=EXPIRE_MINUTES)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _decode_token(token: str) -> dict:
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])


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
        if payload.get("sub") is None:
            raise exc
        return payload
    except JWTError:
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

    token = _create_token({"sub": user.username, "user_id": user.id})
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

    token = _create_token({"sub": user.username, "user_id": user.id})
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
        created_at=user.created_at,
    )
