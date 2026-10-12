"""Authenticated, durable OpenRouter acceptance journey."""

from __future__ import annotations

import time
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .auth import require_auth
from .config import settings
from .db import SessionLocal
from .native_advisory_execution import run_native, connected


router = APIRouter(prefix="/api/runtime-ai", tags=["runtime-ai"])


class ReadinessRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=8000)


@router.post("/investment-readiness")
async def investment_readiness(transport: Request, body: ReadinessRequest, actor: dict = Depends(require_auth)):
    if not settings.openrouter_api_key or not settings.openrouter_model or not settings.openrouter_base_url:
        raise HTTPException(status_code=503, detail="OpenRouter is not configured")
    if settings.openrouter_base_url.rstrip("/") != "https://openrouter.ai/api/v1":
        raise HTTPException(503, "Canonical OpenRouter endpoint is required")
    started = time.monotonic()
    async def provider_call():
        async with httpx.AsyncClient(timeout=60.0) as client:
            return await client.post(
                f"{settings.openrouter_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {settings.openrouter_api_key}", "Content-Type": "application/json"},
                json={
                    "model": settings.openrouter_model,
                    "temperature": 0.2,
                    "messages": [
                        {"role": "system", "content": "Review a paper-investment workflow without financial advice. Return concise risks, missing evidence, next actions, uncertainty, and mandatory human risk/compliance approval gates."},
                        {"role": "user", "content": body.prompt.strip()},
                    ],
                },
            )
    try:
        response = await run_native(transport, actor["user_id"], provider_call)
    except httpx.HTTPError as error:
        raise HTTPException(status_code=502, detail="OpenRouter request failed") from error
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"OpenRouter returned {response.status_code}")
    payload = response.json()
    content = str(payload.get("choices", [{}])[0].get("message", {}).get("content", "")).strip()
    receipt = str(payload.get("id") or response.headers.get("x-request-id") or "").strip()
    if not content or not receipt:
        raise HTTPException(status_code=502, detail="OpenRouter returned an incomplete response")
    await connected(transport)
    result_id = uuid4()
    try:
        async with SessionLocal() as db, db.begin():
            await db.execute(
                text("""
                    INSERT INTO runtime_ai_results
                      (id,user_id,feature,prompt,content,provider,model,provider_response_id)
                    VALUES (:id,:user_id,'investment-readiness',:prompt,:content,'openrouter',:model,:receipt)
                """),
                {"id": result_id, "user_id": int(actor["user_id"]), "prompt": body.prompt.strip(),
                 "content": content, "model": settings.openrouter_model, "receipt": receipt},
            )
            await connected(transport)
    except (OSError, SQLAlchemyError) as error:
        raise HTTPException(status_code=503, detail="Saved reports database is unavailable") from error
    return {
        "id": str(result_id), "content": content, "provider": "openrouter",
        "model": settings.openrouter_model,
        "providerReceipt": {"id": receipt},
        "durationMs": round((time.monotonic() - started) * 1000),
    }

@router.get("/investment-readiness")
async def readiness_history(actor: dict = Depends(require_auth)):
    try:
        async with SessionLocal() as db:
            result=await db.execute(text("SELECT id,prompt,content,model,created_at FROM runtime_ai_results WHERE user_id=:owner AND feature='investment-readiness' ORDER BY created_at DESC LIMIT 50"), {"owner":int(actor["user_id"])})
            return {"reports":[{"id":str(row["id"]),"prompt":row["prompt"],"content":row["content"],"model":row["model"],"createdAt":row["created_at"].isoformat()} for row in result.mappings()]}
    except (OSError, SQLAlchemyError) as error:
        raise HTTPException(status_code=503, detail="Saved reports database is unavailable") from error
