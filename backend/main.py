"""Production entry point for the bounded governed paper-trading service."""

from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from .auth import router as auth_router
from .config import settings, validate_runtime_config
from .db import SessionLocal
from .governed_trading import router as governed_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    validate_runtime_config()
    yield


app = FastAPI(
    title="Governed Paper Trading",
    description=(
        "A paper-only workflow for licensed evidence ingestion, deterministic risk limits, "
        "independent approval, custody-scoped fills, immutable ledger evidence, and reconciliation."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if not settings.is_production else None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

_rate_buckets: dict[str, dict[str, float | int]] = {}


@app.middleware("http")
async def security_boundary(request: Request, call_next):
    if request.url.path.startswith("/api/"):
        client = request.client.host if request.client else "unknown"
        now = time.monotonic()
        bucket = _rate_buckets.get(client)
        if not bucket or now >= float(bucket["reset"]):
            bucket = {"count": 0, "reset": now + 60}
            _rate_buckets[client] = bucket
        if int(bucket["count"]) >= 200:
            return JSONResponse({"error": "rate limit exceeded"}, status_code=429, headers={"Retry-After": "60"})
        bucket["count"] = int(bucket["count"]) + 1

    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if settings.is_production:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


app.include_router(auth_router)
app.include_router(governed_router)


@app.get("/healthz", include_in_schema=False)
async def healthz():
    return {"ok": True, "service": "governed-paper-trading"}


@app.get("/readyz", include_in_schema=False)
async def readyz():
    try:
        async with SessionLocal() as db:
            await db.execute(text("SELECT 1"))
            migration = (
                await db.execute(text("SELECT name FROM schema_migrations ORDER BY applied_at DESC,name DESC LIMIT 1"))
            ).scalar_one_or_none()
        if migration != "002_custody_controls.sql":
            return JSONResponse({"ok": False, "error": "required migrations are not applied"}, status_code=503)
        return {"ok": True, "migration": migration}
    except Exception:
        return JSONResponse({"ok": False, "error": "database is unavailable or unmigrated"}, status_code=503)
