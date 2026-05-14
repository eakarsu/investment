"""Live brokerage integration — paper-trade then go live with risk controls.

TODO: configure credentials — ALPACA_KEY_ID, ALPACA_SECRET_KEY, ALPACA_BASE_URL
(paper: https://paper-api.alpaca.markets, live: https://api.alpaca.markets).
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .auth import require_auth

router = APIRouter(prefix="/api/brokerage", tags=["brokerage"])

MAX_NOTIONAL_PER_ORDER = float(os.environ.get("BROKER_MAX_NOTIONAL", "5000"))


def _headers() -> Dict[str, str]:
    key = os.environ.get("ALPACA_KEY_ID")
    sec = os.environ.get("ALPACA_SECRET_KEY")
    if not key or not sec:
        raise HTTPException(status_code=503, detail="Alpaca credentials not configured")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec}


def _base() -> str:
    return os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")


class OrderReq(BaseModel):
    symbol: str
    qty: Optional[float] = None
    notional: Optional[float] = None
    side: str = "buy"
    type: str = "market"
    time_in_force: str = "day"
    paper: bool = True


@router.get("/account")
async def account(user=Depends(require_auth)):
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{_base()}/v2/account", headers=_headers())
        return r.json()


@router.get("/positions")
async def positions(user=Depends(require_auth)):
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{_base()}/v2/positions", headers=_headers())
        return r.json()


@router.post("/order")
async def order(req: OrderReq, user=Depends(require_auth)):
    notional = req.notional or 0
    if notional and notional > MAX_NOTIONAL_PER_ORDER:
        raise HTTPException(status_code=400, detail=f"notional exceeds risk limit {MAX_NOTIONAL_PER_ORDER}")
    body: Dict[str, Any] = {
        "symbol": req.symbol.upper(),
        "side": req.side,
        "type": req.type,
        "time_in_force": req.time_in_force,
    }
    if req.qty is not None:
        body["qty"] = req.qty
    if req.notional is not None:
        body["notional"] = req.notional
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{_base()}/v2/orders", headers={**_headers(), "Content-Type": "application/json"}, json=body)
        return r.json()


@router.get("/orders")
async def list_orders(status: str = "open", user=Depends(require_auth)):
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{_base()}/v2/orders?status={status}", headers=_headers())
        return r.json()
