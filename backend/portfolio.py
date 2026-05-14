"""
Portfolio management with real price data.

Endpoints:
    POST /api/portfolio/holdings          — add a holding (ticker, shares, cost_basis)
    GET  /api/portfolio/holdings          — list all holdings for current user
    DELETE /api/portfolio/holdings/{id}   — remove a holding
    GET  /api/portfolio/value             — compute current portfolio value using real prices
    GET  /api/portfolio/returns           — total return and per-holding return

All endpoints require authentication (Bearer token from /api/auth/login).

Price data source:
    Alpha Vantage free tier (25 calls/day). Set ALPHA_VANTAGE_API_KEY in .env.
    Falls back to a lightweight mock price if the key is absent so the
    endpoint is always usable in development.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import Float, Integer, String, DateTime, ForeignKey, func, select
from sqlalchemy.orm import Mapped, mapped_column

from .auth import require_auth
from .config import settings
from .db import Base, SessionLocal

logger = logging.getLogger(__name__)

# ─────────────────────────── DB Model ─────────────────────────────────────────

class PortfolioHolding(Base):
    """One stock/ETF position belonging to a user."""
    __tablename__ = "portfolio_holdings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)           # FK to app_users.id
    ticker: Mapped[str] = mapped_column(String(16))                     # e.g. "NVDA"
    shares: Mapped[float] = mapped_column(Float)                        # number of shares
    cost_basis: Mapped[float] = mapped_column(Float)                    # total cost (USD)
    notes: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ─────────────────────────── Pydantic Schemas ─────────────────────────────────

class HoldingCreate(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=16, description="Stock ticker symbol, e.g. NVDA")
    shares: float = Field(..., gt=0, description="Number of shares held")
    cost_basis: float = Field(..., gt=0, description="Total cost basis in USD (shares × avg cost per share)")
    notes: str = Field("", max_length=256, description="Optional notes")


class HoldingOut(BaseModel):
    id: int
    ticker: str = Field(..., description="Stock ticker symbol")
    shares: float = Field(..., description="Number of shares")
    cost_basis: float = Field(..., description="Total cost basis in USD")
    notes: str
    created_at: datetime

    model_config = {"from_attributes": True}


class HoldingValue(BaseModel):
    id: int
    ticker: str = Field(..., description="Stock ticker symbol")
    shares: float
    cost_basis: float = Field(..., description="Total cost basis in USD")
    current_price: float = Field(..., description="Latest market price per share (USD)")
    current_value: float = Field(..., description="current_price × shares")
    gain_loss: float = Field(..., description="current_value − cost_basis")
    return_pct: float = Field(..., description="Percentage return vs cost basis")
    price_source: str = Field(..., description="'alpha_vantage' or 'mock'")


class PortfolioValue(BaseModel):
    total_cost_basis: float = Field(..., description="Sum of all cost bases in USD")
    total_current_value: float = Field(..., description="Sum of all current values in USD")
    total_gain_loss: float = Field(..., description="total_current_value − total_cost_basis")
    total_return_pct: float = Field(..., description="Percentage return on entire portfolio")
    holdings: list[HoldingValue]


# ─────────────────────────── Price fetcher (persistent cache) ────────────────

# Now backed by PriceCacheEntry in PostgreSQL so multiple processes share state
# (NEW FEATURE 4 — scheduled price-refresh worker can populate this table).
_CACHE_TTL_SECONDS = 300  # 5 minutes


async def _fetch_price_alpha_vantage(ticker: str) -> tuple[float, str]:
    """Fetch the latest close price from Alpha Vantage.
    Returns (price, source_label).
    Falls back to a deterministic mock if the key is absent or request fails.
    """
    now = datetime.utcnow()
    from .db import PriceCacheEntry  # local import to avoid circular deps

    # Try persistent cache first
    async with SessionLocal() as s:
        cached = (
            await s.execute(select(PriceCacheEntry).where(PriceCacheEntry.ticker == ticker))
        ).scalar_one_or_none()
        if cached:
            age = (now - cached.fetched_at.replace(tzinfo=None)).total_seconds()
            if age < _CACHE_TTL_SECONDS:
                return cached.price, "alpha_vantage_cached"

    if not settings.alpha_vantage_api_key:
        mock_price = 50.0 + (sum(ord(c) for c in ticker) % 1000)
        return mock_price, "mock"

    url = (
        "https://www.alphavantage.co/query"
        "?function=GLOBAL_QUOTE"
        f"&symbol={ticker}"
        f"&apikey={settings.alpha_vantage_api_key}"
    )
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url)
        data = r.json()
        quote = data.get("Global Quote", {})
        price_str = quote.get("05. price", "")
        if not price_str:
            raise ValueError(f"No price in response: {data}")
        price = float(price_str)
        # Upsert persistent cache
        async with SessionLocal() as s:
            existing = (
                await s.execute(select(PriceCacheEntry).where(PriceCacheEntry.ticker == ticker))
            ).scalar_one_or_none()
            if existing:
                existing.price = price
                existing.source = "alpha_vantage"
                existing.fetched_at = now
            else:
                s.add(PriceCacheEntry(ticker=ticker, price=price, source="alpha_vantage", fetched_at=now))
            await s.commit()
        return price, "alpha_vantage"
    except Exception as exc:
        logger.warning("Alpha Vantage fetch failed for %s: %s", ticker, exc)
        mock_price = 50.0 + (sum(ord(c) for c in ticker) % 1000)
        return mock_price, "mock"


# ─────────────────────────── Router ───────────────────────────────────────────

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


@router.post(
    "/holdings",
    response_model=HoldingOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a stock holding to your portfolio",
    description=(
        "Record a position: ticker, number of shares, and total cost basis. "
        "Use /api/portfolio/value to see its current market value."
    ),
)
async def add_holding(
    body: HoldingCreate,
    current_user: Annotated[dict, Depends(require_auth)],
) -> HoldingOut:
    body.ticker = body.ticker.upper().strip()
    async with SessionLocal() as db:
        holding = PortfolioHolding(
            user_id=current_user["user_id"],
            ticker=body.ticker,
            shares=body.shares,
            cost_basis=body.cost_basis,
            notes=body.notes,
        )
        db.add(holding)
        await db.commit()
        await db.refresh(holding)
    return HoldingOut.model_validate(holding)


@router.get(
    "/holdings",
    response_model=list[HoldingOut],
    summary="List all holdings for the authenticated user",
    description="Returns every position the current user has recorded, newest first.",
)
async def list_holdings(
    current_user: Annotated[dict, Depends(require_auth)],
) -> list[HoldingOut]:
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(PortfolioHolding)
                .where(PortfolioHolding.user_id == current_user["user_id"])
                .order_by(PortfolioHolding.created_at.desc())
            )
        ).scalars().all()
    return [HoldingOut.model_validate(r) for r in rows]


@router.delete(
    "/holdings/{holding_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a holding from your portfolio",
    description="Permanently deletes the holding. Only the owner can delete their own holdings.",
)
async def delete_holding(
    holding_id: int,
    current_user: Annotated[dict, Depends(require_auth)],
) -> None:
    async with SessionLocal() as db:
        holding = (
            await db.execute(
                select(PortfolioHolding).where(PortfolioHolding.id == holding_id)
            )
        ).scalar_one_or_none()

        if holding is None:
            raise HTTPException(status_code=404, detail="Holding not found")
        if holding.user_id != current_user["user_id"]:
            raise HTTPException(status_code=403, detail="Not your holding")

        await db.delete(holding)
        await db.commit()


@router.get(
    "/value",
    response_model=PortfolioValue,
    summary="Current portfolio market value with real price data",
    description=(
        "Fetches the latest price for each ticker (Alpha Vantage or mock fallback), "
        "then computes total value, gain/loss, and percentage return. "
        "Prices are cached for 5 minutes to stay within Alpha Vantage rate limits."
    ),
)
async def portfolio_value(
    current_user: Annotated[dict, Depends(require_auth)],
) -> PortfolioValue:
    async with SessionLocal() as db:
        holdings = (
            await db.execute(
                select(PortfolioHolding)
                .where(PortfolioHolding.user_id == current_user["user_id"])
            )
        ).scalars().all()

    if not holdings:
        return PortfolioValue(
            total_cost_basis=0.0,
            total_current_value=0.0,
            total_gain_loss=0.0,
            total_return_pct=0.0,
            holdings=[],
        )

    # Fetch prices concurrently (respecting AV rate limits via cache)
    prices: dict[str, tuple[float, str]] = {}
    unique_tickers = list({h.ticker for h in holdings})
    results = await asyncio.gather(
        *[_fetch_price_alpha_vantage(t) for t in unique_tickers],
        return_exceptions=True,
    )
    for ticker, result in zip(unique_tickers, results):
        if isinstance(result, Exception):
            prices[ticker] = (0.0, "error")
        else:
            prices[ticker] = result

    holding_values: list[HoldingValue] = []
    total_cost = 0.0
    total_value = 0.0

    for h in holdings:
        price, source = prices.get(h.ticker, (0.0, "unknown"))
        current_value = price * h.shares
        gain_loss = current_value - h.cost_basis
        return_pct = (gain_loss / h.cost_basis * 100) if h.cost_basis else 0.0
        total_cost += h.cost_basis
        total_value += current_value
        holding_values.append(
            HoldingValue(
                id=h.id,
                ticker=h.ticker,
                shares=h.shares,
                cost_basis=h.cost_basis,
                current_price=round(price, 4),
                current_value=round(current_value, 2),
                gain_loss=round(gain_loss, 2),
                return_pct=round(return_pct, 2),
                price_source=source,
            )
        )

    total_gain = total_value - total_cost
    total_return = (total_gain / total_cost * 100) if total_cost else 0.0

    return PortfolioValue(
        total_cost_basis=round(total_cost, 2),
        total_current_value=round(total_value, 2),
        total_gain_loss=round(total_gain, 2),
        total_return_pct=round(total_return, 2),
        holdings=holding_values,
    )
