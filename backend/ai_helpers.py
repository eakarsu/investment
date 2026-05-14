"""
Shared AI helpers for the investment backend.

- Standard model: anthropic/claude-3-5-sonnet-20241022 (set via settings).
- Per-user AI rate limiter: 20 calls / hour / user (configurable).
- 3-strategy JSON parser (stripped fences -> raw -> balanced JSON block).
- AiResult JSONB row helper for audit logging.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Any

from sqlalchemy import select

from .config import settings
from .db import AiResult, SessionLocal

logger = logging.getLogger(__name__)

# ---------- per-user AI rate limiter (20 / hr) ----------

_buckets: dict[str, dict[str, float]] = {}
_bucket_lock = asyncio.Lock()
_WINDOW_SECONDS = 60 * 60


async def ai_rate_limiter(user_key: str) -> dict:
    """Check + increment the per-user AI budget. Returns dict with allowed/remaining/reset."""
    now = time.time()
    max_calls = settings.ai_rate_limit_per_hour
    async with _bucket_lock:
        b = _buckets.get(user_key)
        if not b or now > b["reset_at"]:
            _buckets[user_key] = {"count": 1.0, "reset_at": now + _WINDOW_SECONDS}
            return {"allowed": True, "remaining": max_calls - 1, "reset_at": now + _WINDOW_SECONDS}
        if b["count"] >= max_calls:
            return {"allowed": False, "remaining": 0, "reset_at": b["reset_at"]}
        b["count"] += 1
        return {"allowed": True, "remaining": int(max_calls - b["count"]), "reset_at": b["reset_at"]}


# ---------- 3-strategy JSON parser ----------

def _strip_fences(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        nl = s.find("\n")
        if nl != -1:
            s = s[nl + 1:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def _balanced_block(text: str) -> str | None:
    first = text.find("{")
    if first == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(first, len(text)):
        ch = text[i]
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[first:i + 1]
    return None


def parse_ai_json(raw: str) -> Any:
    """Parse JSON returned by an LLM with three strategies in order:
       1. direct json.loads on the trimmed/fence-stripped content
       2. fall back to balanced-{...} block extraction
       3. last-ditch: strip non-printable chars and retry
    """
    stripped = _strip_fences(raw)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    block = _balanced_block(stripped)
    if block:
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            pass

    cleaned = "".join(ch for ch in stripped if ch.isprintable() or ch in "\n\r\t")
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"parse_ai_json: failed after 3 strategies; raw={raw[:240]}…"
        ) from e


# ---------- AiResult JSONB audit log ----------

async def log_ai_result(
    *,
    feature: str,
    user_key: str | None = None,
    ref_type: str | None = None,
    ref_id: str | None = None,
    input_data: Any | None = None,
    output_data: Any | None = None,
    raw_text: str | None = None,
    error: str | None = None,
    duration_ms: int | None = None,
) -> int | None:
    """Persist a row to ai_results — never raises."""
    try:
        async with SessionLocal() as db:
            row = AiResult(
                feature=feature,
                user_key=user_key,
                ref_type=ref_type,
                ref_id=ref_id,
                model=settings.openrouter_model,
                input=(input_data if isinstance(input_data, (dict, list)) else {"value": input_data}),
                output=(output_data if isinstance(output_data, (dict, list)) else {"value": output_data}) if output_data is not None else None,
                raw_text=raw_text,
                error=error,
                duration_ms=duration_ms,
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row.id
    except Exception as e:
        logger.error("log_ai_result failed: %s", e)
        return None
