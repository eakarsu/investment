"""
Scheduled price-refresh worker (NEW FEATURE 4).

Run as:
    python -m backend.workers.price_refresh

The default behaviour: read all distinct tickers from PortfolioHolding, fetch
the latest price from Alpha Vantage (or mock fallback), upsert PriceCacheEntry.

Designed to be invoked from cron / a Kubernetes CronJob — no external scheduler
dependency. Use --interval-seconds N to keep it running as a daemon instead of
exiting after one pass.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy import select

from ..config import settings
from ..db import SessionLocal, init_db
from ..portfolio import PortfolioHolding, _fetch_price_alpha_vantage

logger = logging.getLogger(__name__)


async def refresh_once() -> dict:
    await init_db()
    async with SessionLocal() as s:
        tickers = (
            await s.execute(select(PortfolioHolding.ticker).distinct())
        ).scalars().all()

    if not tickers:
        logger.info("No portfolio tickers to refresh.")
        return {"refreshed": 0, "tickers": []}

    refreshed = []
    for t in tickers:
        try:
            price, source = await _fetch_price_alpha_vantage(t)
            refreshed.append({"ticker": t, "price": price, "source": source})
            logger.info("refreshed %s = %.2f (%s)", t, price, source)
        except Exception as e:
            logger.error("failed to refresh %s: %s", t, e)
    return {"refreshed": len(refreshed), "tickers": refreshed}


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--interval-seconds", type=int, default=0,
                   help="If > 0, loop forever sleeping this many seconds between passes.")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.interval_seconds <= 0:
        result = await refresh_once()
        print(result)
        return

    while True:
        try:
            await refresh_once()
        except Exception as e:
            logger.exception("refresh pass failed: %s", e)
        await asyncio.sleep(args.interval_seconds)


if __name__ == "__main__":
    asyncio.run(main())
