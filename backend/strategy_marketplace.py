"""Marketplace for community-shared strategies with verified reproducibility."""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .auth import require_auth


router = APIRouter(prefix="/api/strategy-marketplace", tags=["strategy-marketplace"])

# In-memory; replace with table when migration permitted.
_LISTINGS: List[Dict[str, Any]] = []


class Listing(BaseModel):
    name: str
    description: str
    author: str
    signals: List[Dict[str, Any]]
    backtest_metrics: Dict[str, Any]  # e.g. {"sharpe":1.2,"cagr":0.18,"max_drawdown":-0.12}
    config_hash: str | None = None


def _hash(listing: Dict[str, Any]) -> str:
    payload = json.dumps({k: listing.get(k) for k in ["signals", "backtest_metrics"]}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


@router.post("/publish")
async def publish(item: Listing, user=Depends(require_auth)):
    d = item.dict()
    d["id"] = f"strat_{int(time.time()*1000)}"
    d["config_hash"] = item.config_hash or _hash(d)
    d["published_at"] = time.time()
    d["votes"] = 0
    d["verified"] = False
    _LISTINGS.append(d)
    return d


@router.get("/list")
async def list_listings(min_sharpe: float | None = None, verified: bool | None = None):
    out = _LISTINGS
    if min_sharpe is not None:
        out = [l for l in out if (l.get("backtest_metrics") or {}).get("sharpe", 0) >= min_sharpe]
    if verified is not None:
        out = [l for l in out if l.get("verified") == verified]
    return {"count": len(out), "listings": out[-200:]}


@router.post("/verify/{listing_id}")
async def verify(listing_id: str, expected_hash: str, user=Depends(require_auth)):
    for l in _LISTINGS:
        if l["id"] == listing_id:
            computed = _hash(l)
            l["verified"] = (computed == expected_hash)
            l["verified_at"] = time.time()
            return {"id": listing_id, "verified": l["verified"], "expected": expected_hash, "computed": computed}
    raise HTTPException(status_code=404, detail="listing not found")


@router.post("/vote/{listing_id}")
async def vote(listing_id: str, direction: int = 1, user=Depends(require_auth)):
    for l in _LISTINGS:
        if l["id"] == listing_id:
            l["votes"] = (l.get("votes") or 0) + (1 if direction >= 0 else -1)
            return {"id": listing_id, "votes": l["votes"]}
    raise HTTPException(status_code=404, detail="listing not found")
