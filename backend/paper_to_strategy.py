"""Paper-to-strategy converter — LLM reads a paper and proposes implementable signals."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .ai_helpers import log_ai_result, parse_ai_json
from . import openrouter
from .auth import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/paper-to-strategy", tags=["paper-to-strategy"])


class ConvertReq(BaseModel):
    paper_text: str
    target_asset_class: Optional[str] = "equities"
    constraints: Optional[Dict[str, Any]] = None


@router.post("/convert")
async def convert(req: ConvertReq, user=Depends(require_auth)):
    if not req.paper_text or len(req.paper_text) < 200:
        raise HTTPException(status_code=400, detail="paper_text too short")

    system = (
        "You convert academic finance papers into implementable signals. "
        "Return JSON only: {\"signals\":[{\"name\":string,\"formula\":string,"
        "\"frequency\":\"daily|weekly|monthly\",\"rationale\":string,\"data_required\":[string]}],"
        "\"backtest_plan\":string,\"risks\":[string]}"
    )
    user_msg = (
        f"Target asset class: {req.target_asset_class}\n"
        f"Constraints: {req.constraints or {}}\n\n"
        f"Paper:\n{req.paper_text[:8000]}"
    )

    try:
        raw = await openrouter.chat([{"role": "system", "content": system}, {"role": "user", "content": user_msg}], max_tokens=1500)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI call failed: {e}")

    parsed = parse_ai_json(raw) or {"raw": raw}
    try:
        await log_ai_result(
            feature="paper-to-strategy",
            user_key=str(getattr(user, "id", "") or (user.get("id") if isinstance(user, dict) else "")),
            input_data={"len": len(req.paper_text)},
            output_data=parsed if isinstance(parsed, (dict, list)) else None,
            raw_text=raw,
        )
    except Exception as e:
        logger.warning("log_ai_result failed: %s", e)
    return parsed


@router.post("/critique")
async def critique(payload: Dict[str, Any], user=Depends(require_auth)):
    """Have the LLM critique a proposed strategy for survivorship bias, lookahead, etc."""
    sig = payload.get("signals") or []
    if not sig:
        raise HTTPException(status_code=400, detail="signals required")
    system = "You are a quant reviewer. Identify lookahead bias, data snooping, and capacity issues. JSON output."
    user_msg = f"Signals: {sig}\nBacktest plan: {payload.get('backtest_plan')}"
    raw = await openrouter.chat([{"role": "system", "content": system}, {"role": "user", "content": user_msg}], max_tokens=900)
    return parse_ai_json(raw) or {"raw": raw}
