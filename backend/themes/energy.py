"""
Theme 3 — Energy-aware router.

Problem: Grid interconnect queues are now 3–7 years in the US; carbon
intensity and $/kWh vary 5-10x across regions; PUE adds another 10-40%.
The cheapest token is the one served where power is cheap AND clean.

Solution: multi-objective region selector. Score each region by
    effective_usd = price * pue * energy_per_token
    effective_co2 = carbon * pue * energy_per_token
  and combine with user weights under a latency SLA.

Usage:
  POST /api/themes/energy/route { request_tokens, latency_sla_ms,
                                  w_cost, w_carbon }
  GET  /api/themes/energy/regions
  GET  /api/themes/energy/decisions
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import EnergyRegion, EnergyDecision, EnergyAlgoRun, SessionLocal
from . import energy_algorithms as algos


ENERGY_PER_TOKEN_KWH = 3e-6  # 3 Wh/1000 tok (order-of-magnitude for a 70B-class model)


router = APIRouter(prefix="/api/themes/energy", tags=["energy"])


async def _session() -> AsyncSession:
    async with SessionLocal() as s:
        yield s


class RouteBody(BaseModel):
    request_tokens: int
    latency_sla_ms: int = 2000
    w_cost: float = 1.0
    w_carbon: float = 0.3


@router.get("/regions")
async def list_regions(s: AsyncSession = Depends(_session)):
    rows = (await s.execute(select(EnergyRegion).order_by(EnergyRegion.region))).scalars().all()
    return {
        "regions": [
            {
                "region": r.region,
                "price_usd_per_kwh": r.price_usd_per_kwh,
                "carbon_gco2_per_kwh": r.carbon_gco2_per_kwh,
                "pue": r.pue,
                "latency_ms": r.latency_ms,
                "available_gpus": r.available_gpus,
                "grid_queue_months": r.grid_queue_months,
            }
            for r in rows
        ]
    }


@router.post("/route")
async def route(body: RouteBody, s: AsyncSession = Depends(_session)):
    regions = (await s.execute(select(EnergyRegion))).scalars().all()
    eligible = [
        r for r in regions
        if r.latency_ms <= body.latency_sla_ms and r.available_gpus > 0
    ]
    if not eligible:
        raise HTTPException(400, "no regions meet SLA or have GPUs available")

    energy_kwh = body.request_tokens * ENERGY_PER_TOKEN_KWH

    def score(r: EnergyRegion) -> tuple[float, float, float]:
        cost = r.price_usd_per_kwh * r.pue * energy_kwh
        carbon = r.carbon_gco2_per_kwh * r.pue * energy_kwh
        obj = body.w_cost * cost + body.w_carbon * carbon / 1000.0  # g → kg × $1/kg-CO2e
        return obj, cost, carbon

    scored = [(score(r), r) for r in eligible]
    scored.sort(key=lambda x: x[0][0])
    (best_obj, best_cost, best_carbon), best = scored[0]
    worst_obj = scored[-1][0][0]
    savings = 100 * (worst_obj - best_obj) / max(worst_obj, 1e-9)

    reason = (
        f"picked {best.region} — "
        f"${best.price_usd_per_kwh:.3f}/kWh × PUE {best.pue:.2f} × "
        f"{best.carbon_gco2_per_kwh:.0f} gCO2/kWh; "
        f"{savings:.1f}% cheaper than worst eligible region"
    )
    row = EnergyDecision(
        request_tokens=body.request_tokens,
        latency_sla_ms=body.latency_sla_ms,
        chosen_region=best.region,
        cost_usd=best_cost,
        carbon_g=best_carbon,
        savings_vs_worst_pct=savings,
        reason=reason,
    )
    s.add(row)
    await s.commit()
    return {
        "chosen_region": best.region,
        "cost_usd": round(best_cost, 6),
        "carbon_g": round(best_carbon, 3),
        "latency_ms": best.latency_ms,
        "savings_vs_worst_pct": round(savings, 1),
        "reason": reason,
    }


@router.get("/algorithms")
async def list_energy_algos(s: AsyncSession = Depends(_session), limit: int = 200):
    rows = (await s.execute(
        select(EnergyAlgoRun).order_by(EnergyAlgoRun.created_at.desc()).limit(limit)
    )).scalars().all()
    return {
        "papers": algos.PAPERS,
        "runs": [
            {
                "id": r.id, "paper": r.paper, "arxiv": r.arxiv,
                "scenario": r.scenario,
                "metric_name": r.metric_name, "metric_value": round(r.metric_value, 3),
                "baseline_value": round(r.baseline_value, 3),
                "improvement_pct": round(r.improvement_pct, 1),
                "notes": r.notes,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


@router.get("/decisions")
async def decisions(s: AsyncSession = Depends(_session), limit: int = 100):
    rows = (await s.execute(
        select(EnergyDecision).order_by(EnergyDecision.created_at.desc()).limit(limit)
    )).scalars().all()
    return {
        "decisions": [
            {
                "id": r.id,
                "tokens": r.request_tokens,
                "sla_ms": r.latency_sla_ms,
                "chosen": r.chosen_region,
                "cost_usd": round(r.cost_usd, 6),
                "carbon_g": round(r.carbon_g, 3),
                "savings_pct": round(r.savings_vs_worst_pct, 1),
                "reason": r.reason,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    }
