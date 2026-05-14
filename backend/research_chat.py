"""Research chat — Q&A grounded on paper corpus and backtest history."""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from .ai_helpers import log_ai_result
from .auth import require_auth
from .db import AiResult, Backtest, SessionLocal
from . import openrouter


router = APIRouter(prefix="/api/research-chat", tags=["research-chat"])


class ChatReq(BaseModel):
    question: str
    theme: str | None = None
    top_k: int = 4


def _summaries_for_theme(theme: str | None, k: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    with SessionLocal() as db:
        q = db.execute(select(Backtest).order_by(Backtest.id.desc()).limit(k * 2))
        for row in q.scalars():
            if theme and getattr(row, "theme", None) and row.theme != theme:
                continue
            out.append({
                "id": row.id,
                "theme": getattr(row, "theme", None),
                "paper": getattr(row, "paper", None),
                "sharpe": getattr(row, "sharpe", None),
                "cagr": getattr(row, "cagr", None),
                "max_drawdown": getattr(row, "max_drawdown", None),
            })
            if len(out) >= k:
                break
    return out


@router.post("/ask")
async def ask(req: ChatReq, user=Depends(require_auth)):
    summaries = _summaries_for_theme(req.theme, req.top_k)
    system = (
        "You are a research analyst. Answer ONLY from the provided backtest summaries. "
        "Cite specific runs by id. If insufficient, say so."
    )
    user_msg = f"Backtests:\n{summaries}\n\nQuestion: {req.question}"
    raw = await openrouter.chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user_msg}],
        max_tokens=900,
    )
    try:
        await log_ai_result(
            feature="research-chat",
            user_key=str(getattr(user, "id", "") or (user.get("id") if isinstance(user, dict) else "")),
            input_data={"q": req.question},
            output_data={"answer": raw},
            raw_text=raw,
        )
    except Exception:
        pass
    return {"answer": raw, "grounding": summaries}
