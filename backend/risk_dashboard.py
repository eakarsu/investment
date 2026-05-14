"""Risk dashboards — VaR, drawdown, correlation heatmap with alerting."""
from __future__ import annotations

import math
import statistics
from typing import Any, Dict, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .auth import require_auth


router = APIRouter(prefix="/api/risk", tags=["risk"])


class Series(BaseModel):
    name: str
    returns: List[float]  # daily returns as decimals (e.g. 0.01 = 1%)


class RiskReq(BaseModel):
    series: List[Series]
    confidence: float = 0.95


def historical_var(rets: List[float], confidence: float) -> float:
    if not rets:
        return 0.0
    s = sorted(rets)
    idx = max(0, int((1 - confidence) * len(s)))
    return s[idx]


def max_drawdown(rets: List[float]) -> float:
    equity = [1.0]
    for r in rets:
        equity.append(equity[-1] * (1 + r))
    peak = equity[0]
    dd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        dd = min(dd, (v - peak) / peak)
    return dd  # negative


def corr(a: List[float], b: List[float]) -> float:
    n = min(len(a), len(b))
    if n < 2:
        return 0.0
    a, b = a[-n:], b[-n:]
    ma, mb = statistics.mean(a), statistics.mean(b)
    sa = math.sqrt(sum((x - ma) ** 2 for x in a))
    sb = math.sqrt(sum((x - mb) ** 2 for x in b))
    if sa == 0 or sb == 0:
        return 0.0
    return sum((a[i] - ma) * (b[i] - mb) for i in range(n)) / (sa * sb)


@router.post("/metrics")
async def metrics(req: RiskReq, user=Depends(require_auth)):
    out: Dict[str, Any] = {"series": [], "correlations": []}
    for s in req.series:
        rets = s.returns or []
        out["series"].append({
            "name": s.name,
            "var": round(historical_var(rets, req.confidence), 6),
            "max_drawdown": round(max_drawdown(rets), 6),
            "mean": round(statistics.mean(rets), 6) if rets else 0,
            "stdev": round(statistics.stdev(rets), 6) if len(rets) > 1 else 0,
        })
    for i in range(len(req.series)):
        for j in range(i + 1, len(req.series)):
            out["correlations"].append({
                "a": req.series[i].name,
                "b": req.series[j].name,
                "corr": round(corr(req.series[i].returns, req.series[j].returns), 4),
            })
    return out


@router.post("/alert")
async def alert(req: RiskReq, threshold_var: float = -0.05, threshold_dd: float = -0.20, user=Depends(require_auth)):
    flags = []
    for s in req.series:
        var = historical_var(s.returns, 0.95)
        dd = max_drawdown(s.returns)
        if var < threshold_var:
            flags.append({"series": s.name, "type": "var_breach", "var": var, "threshold": threshold_var})
        if dd < threshold_dd:
            flags.append({"series": s.name, "type": "drawdown_breach", "dd": dd, "threshold": threshold_dd})
    return {"flags": flags}
