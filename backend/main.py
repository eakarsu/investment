"""FastAPI app wiring all 5 themes + a small overview endpoint."""

import hashlib
import io
import logging
import tarfile
import time
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy import func, select

from .db import (
    AiResult, Backtest, HBMDecision, NetworkPlan, EnergyDecision, EnergyRegion,
    InferenceRun, PhotonicScenario, SessionLocal, init_db,
)
from .themes import hbm, networking, energy, inference, photonics
from .themes import sources as paper_sources
from . import openrouter
from .ai_helpers import ai_rate_limiter, log_ai_result, parse_ai_json
from .auth import require_auth, router as auth_router
from .config import settings
from .portfolio import router as portfolio_router

logger = logging.getLogger(__name__)


app = FastAPI(
    title="investment — 5-theme AI infrastructure solutions",
    description=(
        "Research tool implementing five AI/semiconductor investment algorithms: "
        "HBM memory, GPU networking, energy routing, inference optimization, and "
        "photonic scheduling. Uses OpenRouter LLMs to narrate deterministic algorithm outputs. "
        "All theme endpoints and portfolio management require a Bearer JWT from /api/auth/login."
    ),
    version="1.0.0",
)


# ============ CORS (env-driven) ============
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


# ============ Helmet-equivalent security headers + simple per-IP rate limit ============

_RL: dict[str, dict] = {}


@app.middleware("http")
async def security_headers_and_rate_limit(request: Request, call_next):
    # Per-IP general rate limit: 200 req / 60s for /api/*, 20 req / 60s for AI/narrate
    rl_headers: dict[str, str] = {}
    if request.url.path.startswith("/api/"):
        ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (
            request.client.host if request.client else "unknown"
        )
        bucket = "ai" if "/api/narrate/" in request.url.path or "/api/ai/" in request.url.path else "api"
        max_per_window = 20 if bucket == "ai" else 200
        key = f"{ip}:{bucket}"
        now = time.time()
        b = _RL.get(key)
        if not b or now > b["reset"]:
            _RL[key] = {"count": 1, "reset": now + 60}
        elif b["count"] >= max_per_window:
            # Surface the same X-RateLimit-* headers on the 429 for client UX.
            return JSONResponse(
                {"error": "Too many requests. Slow down."},
                status_code=429,
                headers={
                    "Retry-After": "60",
                    "X-RateLimit-Limit": str(max_per_window),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(b["reset"])),
                    "X-RateLimit-Bucket": bucket,
                },
            )
        else:
            b["count"] += 1
        # Successful path: stash headers to attach after call_next.
        cur = _RL[key]
        rl_headers = {
            "X-RateLimit-Limit": str(max_per_window),
            "X-RateLimit-Remaining": str(max(0, max_per_window - int(cur["count"]))),
            "X-RateLimit-Reset": str(int(cur["reset"])),
            "X-RateLimit-Bucket": bucket,
        }

    response = await call_next(request)
    for k, v in rl_headers.items():
        response.headers[k] = v

    # Helmet-equivalent headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["X-XSS-Protection"] = "1; mode=block"

    return response


# Auth endpoints (public)
app.include_router(auth_router)

# Portfolio management with real Alpha Vantage price data
app.include_router(portfolio_router)

# Theme routers
app.include_router(hbm.router)
app.include_router(networking.router)
app.include_router(energy.router)
app.include_router(inference.router)
app.include_router(photonics.router)
from . import paper_to_strategy as _pts, brokerage as _brk, research_chat as _rc, risk_dashboard as _rd, strategy_marketplace as _sm  # noqa: E402
app.include_router(_pts.router); app.include_router(_brk.router); app.include_router(_rc.router); app.include_router(_rd.router); app.include_router(_sm.router)


@app.on_event("startup")
async def _startup() -> None:
    await init_db()


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/api/tax-lot-drift", tags=["portfolio"])
async def tax_lot_drift(_user: Annotated[dict, Depends(require_auth)]):
    return {
        "feature": "Tax-Lot Drift",
        "summary": {
            "embedded_gain": 128400,
            "harvestable_loss": 27150,
            "wash_windows": 3,
            "priority": "Review before rebalance",
        },
        "lots": [
            {"symbol": "NVDA", "account": "Taxable", "gain_loss_pct": 42.6, "holding_period": "Long-term", "action": "Trim with gain budget"},
            {"symbol": "SMH", "account": "Taxable", "gain_loss_pct": -8.9, "holding_period": "Short-term", "action": "Harvest candidate"},
            {"symbol": "AVGO", "account": "IRA", "gain_loss_pct": 18.4, "holding_period": "Tax-deferred", "action": "Rebalance without tax impact"},
        ],
        "controls": [
            "Block replacement buys inside active wash-sale windows.",
            "Separate taxable, tax-deferred, and tax-exempt rebalance actions.",
            "Cap realized gains against household tax budget before trade approval.",
        ],
    }


@app.get("/api/source/{theme}/{paper}", tags=["algorithms"])
async def paper_source(
    theme: str,
    paper: str,
    _user: Annotated[dict, Depends(require_auth)],
):
    """Return the actual Python source code backing a paper's implementation.

    The source is extracted at runtime via `inspect.getsource`, so what you
    see here is exactly what the algorithm endpoints execute — no stubs.

    Requires authentication.
    """
    try:
        return paper_sources.get_source(theme, paper)
    except KeyError:
        raise HTTPException(404, f"unknown theme/paper: {theme}/{paper}")


@app.post("/api/run/{theme}/{paper}", tags=["algorithms"])
async def paper_run(
    theme: str,
    paper: str,
    body: dict | None = None,
    user: Annotated[dict, Depends(require_auth)] = None,
):
    """Execute a paper's deterministic algorithm locally with configurable inputs.

    Runs the real Python implementation — no LLM involvement at this stage.
    Pass a JSON body to override any of the default input parameters for the paper.

    Returns `{theme, paper, elapsed_ms, result, executed_by, source_hash, seed}`.
    The extra fields support reproducibility (NEW FEATURE 5 — see /api/reproducibility).

    Requires authentication.
    """
    import asyncio
    try:
        # Validate theme/paper exist (raises KeyError → 404)
        default_inputs = paper_sources.get_inputs(theme, paper)
        inputs = {**default_inputs, **(body or {})}

        src = paper_sources.get_source(theme, paper)
        source_hash = hashlib.sha256(src["source"].encode()).hexdigest()[:16]

        t0 = time.perf_counter()
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: paper_sources.run_paper_with_inputs(theme, paper, inputs),
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return {
            "theme": theme, "paper": paper,
            "elapsed_ms": round(elapsed_ms, 2),
            "result": result,
            "executed_by": "local",
            "source_hash": source_hash,
            "seed": inputs.get("seed", 0),
        }
    except KeyError:
        raise HTTPException(404, f"unknown theme/paper: {theme}/{paper}")
    except Exception as e:
        raise HTTPException(500, f"run failed: {type(e).__name__}: {e}")


@app.post("/api/narrate/{theme}/{paper}", tags=["algorithms"])
async def paper_narrate(
    theme: str,
    paper: str,
    body: dict | None = None,
    user: Annotated[dict, Depends(require_auth)] = None,
):
    """Run a paper's algorithm then generate an LLM narration and chart spec."""
    import asyncio
    user_key = user["sub"] if user else "anon"

    # Per-user AI rate limit (20/hr)
    rl = await ai_rate_limiter(user_key)
    if not rl["allowed"]:
        raise HTTPException(429, "AI rate limit exceeded — try again later.")

    try:
        src = paper_sources.get_source(theme, paper)
        default_inputs = paper_sources.get_inputs(theme, paper)
        inputs = {**default_inputs, **(body or {})}

        t0 = time.perf_counter()
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: paper_sources.run_paper_with_inputs(theme, paper, inputs),
        )
        run_ms = (time.perf_counter() - t0) * 1000

        t1 = time.perf_counter()
        narration_error = None
        try:
            llm_out = await openrouter.narrate_algorithm(
                theme=theme, paper=paper,
                source=src["source"], inputs=inputs, result=result,
            )
        except openrouter.OpenRouterError as e:
            narration_error = str(e)
            llm_out = {"narration_md": "", "chart": None}
        llm_ms = (time.perf_counter() - t1) * 1000

        await log_ai_result(
            feature=f"narrate.{theme}.{paper}",
            user_key=user_key,
            ref_type=theme,
            ref_id=paper,
            input_data=inputs,
            output_data=llm_out if not narration_error else None,
            error=narration_error,
            duration_ms=int(llm_ms),
        )

        if narration_error and "OPENROUTER_API_KEY" in narration_error:
            raise HTTPException(503, "OpenRouter not configured on this server.")
        if narration_error:
            raise HTTPException(502, f"OpenRouter: {narration_error}")

        return {
            "theme": theme, "paper": paper,
            "run": {"elapsed_ms": round(run_ms, 2), "result": result},
            "narration_md": llm_out.get("narration_md", ""),
            "chart": llm_out.get("chart"),
            "model": settings.openrouter_model,
            "llm_elapsed_ms": round(llm_ms, 2),
            "executed_by": "local+openrouter",
        }
    except KeyError:
        raise HTTPException(404, f"unknown theme/paper: {theme}/{paper}")


@app.get("/api/overview", tags=["dashboard"])
async def overview():
    async with SessionLocal() as s:
        hbm_total = (await s.execute(select(func.count(HBMDecision.id)))).scalar_one()
        net_total = (await s.execute(select(func.count(NetworkPlan.id)))).scalar_one()
        en_total  = (await s.execute(select(func.count(EnergyDecision.id)))).scalar_one()
        en_reg    = (await s.execute(select(func.count(EnergyRegion.id)))).scalar_one()
        inf_total = (await s.execute(select(func.count(InferenceRun.id)))).scalar_one()
        ph_total  = (await s.execute(select(func.count(PhotonicScenario.id)))).scalar_one()

        inf_cost = (await s.execute(select(func.coalesce(func.sum(InferenceRun.cost_usd), 0)))).scalar_one()
        en_saved = (await s.execute(select(func.coalesce(func.avg(EnergyDecision.savings_vs_worst_pct), 0)))).scalar_one()
        ph_speed = (await s.execute(select(func.coalesce(func.avg(PhotonicScenario.speedup_pct), 0)))).scalar_one()
        hbm_rej  = (await s.execute(select(func.count(HBMDecision.id)).where(HBMDecision.decision == "reject"))).scalar_one()

    return JSONResponse({
        "themes": [
            {"id": "hbm",        "name": "HBM-scarcity optimizer",        "rows": hbm_total,
             "kpi": f"{hbm_rej} rejects"},
            {"id": "networking", "name": "Network placement planner",     "rows": net_total,
             "kpi": f"{net_total} plans"},
            {"id": "energy",     "name": "Energy-aware router",           "rows": en_total,
             "kpi": f"{en_saved:.1f}% avg savings across {en_reg} regions"},
            {"id": "inference",  "name": "Inference auto-tuner",          "rows": inf_total,
             "kpi": f"${inf_cost:.2f} tracked"},
            {"id": "photonics",  "name": "Photonic collective scheduler", "rows": ph_total,
             "kpi": f"{ph_speed:.1f}% avg speedup"},
        ],
    })


# ====================================================================
# NEW FEATURES — see audit "Proposed NEW custom features"
# ====================================================================

# --- AI results listing (paginated) ---
@app.get("/api/ai-results", tags=["ai"])
async def list_ai_results(
    user: Annotated[dict, Depends(require_auth)],
    page: int = 1,
    page_size: int = 20,
    feature: str | None = None,
):
    page = max(1, page)
    page_size = min(100, max(1, page_size))
    async with SessionLocal() as s:
        q = select(AiResult)
        if feature:
            q = q.where(AiResult.feature == feature)
        total = (await s.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
        rows = (
            await s.execute(
                q.order_by(AiResult.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars().all()
    return {
        "data": [
            {
                "id": r.id, "feature": r.feature, "model": r.model,
                "user_key": r.user_key, "ref_type": r.ref_type, "ref_id": r.ref_id,
                "input": r.input, "output": r.output, "error": r.error,
                "duration_ms": r.duration_ms, "created_at": r.created_at,
            } for r in rows
        ],
        "pagination": {
            "page": page, "page_size": page_size,
            "total_items": total, "total_pages": (total + page_size - 1) // page_size,
        },
    }


# --- AI usage summary (aggregated over ai_results) ---
@app.get("/api/ai-results/summary", tags=["ai"])
async def ai_results_summary(
    user: Annotated[dict, Depends(require_auth)],
):
    """Aggregate ai_results by feature for a quick usage / latency / error overview.

    Pure read-only; works on existing rows so requires no schema migration.
    """
    async with SessionLocal() as s:
        rows = (await s.execute(select(AiResult))).scalars().all()

    by_feature: dict[str, dict] = {}
    by_model: dict[str, int] = {}
    total_calls = len(rows)
    total_errors = 0
    total_duration_ms = 0
    duration_count = 0

    for r in rows:
        f = r.feature or "unknown"
        bf = by_feature.setdefault(f, {
            "feature": f, "calls": 0, "errors": 0,
            "avg_duration_ms": 0.0, "_dur_sum": 0, "_dur_n": 0,
            "last_called_at": None,
        })
        bf["calls"] += 1
        if r.error:
            bf["errors"] += 1
            total_errors += 1
        if r.duration_ms is not None:
            bf["_dur_sum"] += r.duration_ms
            bf["_dur_n"] += 1
            total_duration_ms += r.duration_ms
            duration_count += 1
        if r.created_at and (bf["last_called_at"] is None or r.created_at > bf["last_called_at"]):
            bf["last_called_at"] = r.created_at
        m = r.model or "unknown"
        by_model[m] = by_model.get(m, 0) + 1

    by_feature_list = []
    for bf in by_feature.values():
        if bf["_dur_n"]:
            bf["avg_duration_ms"] = round(bf["_dur_sum"] / bf["_dur_n"], 2)
        bf.pop("_dur_sum", None); bf.pop("_dur_n", None)
        by_feature_list.append(bf)
    by_feature_list.sort(key=lambda x: x["calls"], reverse=True)

    return {
        "total_calls": total_calls,
        "total_errors": total_errors,
        "error_rate_pct": round((total_errors / total_calls) * 100, 2) if total_calls else 0.0,
        "avg_duration_ms": round(total_duration_ms / duration_count, 2) if duration_count else 0.0,
        "by_feature": by_feature_list,
        "by_model": [{"model": m, "calls": c} for m, c in sorted(by_model.items(), key=lambda x: x[1], reverse=True)],
    }


# --- NEW FEATURE 1 — HBM admission backtest with replay ---
@app.post("/api/backtest/hbm", tags=["new-features"])
async def backtest_hbm(
    user: Annotated[dict, Depends(require_auth)],
    body: dict,
):
    """Replay a historical request trace through the HBM admission controller.

    Body:
      { "trace": [{"prompt_tokens": int, "max_completion": int, "model": str}, ...],
        "hbm_capacity_mb": float (optional) }

    Returns counts of admit/evict/reject and OOM-avoidance vs naive baseline.
    Persists a Backtest row.
    """
    trace = body.get("trace") or []
    if not isinstance(trace, list) or not trace:
        raise HTTPException(400, "trace must be a non-empty list")

    capacity_mb = float(body.get("hbm_capacity_mb", 80_000))

    # Local baseline: naive admit-everything; OOM = sum_kv_mb > capacity at any step
    used_mb = 0.0
    naive_oom = 0
    for step in trace:
        kv_mb = (step.get("prompt_tokens", 0) + step.get("max_completion", 0)) * 0.0002
        used_mb += kv_mb
        if used_mb > capacity_mb:
            naive_oom += 1

    # Smart algo: admit if used_mb + kv_mb <= 0.9 * capacity, else evict-then-admit
    used_mb = 0.0
    admit = evict = reject = 0
    for step in trace:
        kv_mb = (step.get("prompt_tokens", 0) + step.get("max_completion", 0)) * 0.0002
        if used_mb + kv_mb <= 0.9 * capacity_mb:
            admit += 1
            used_mb += kv_mb
        elif used_mb + kv_mb <= capacity_mb:
            evict += 1
            used_mb = max(0.0, used_mb * 0.5) + kv_mb
        else:
            reject += 1

    smart_oom = 0  # by construction
    improvement_pct = (
        ((naive_oom - smart_oom) / max(1, naive_oom)) * 100 if naive_oom > 0 else 0.0
    )

    metrics = {"admit": admit, "evict": evict, "reject": reject, "oom": smart_oom}
    baseline = {"admit": len(trace), "evict": 0, "reject": 0, "oom": naive_oom}

    async with SessionLocal() as s:
        row = Backtest(
            user_key=user["sub"],
            theme="hbm",
            paper="HBM-Admission-Replay",
            trace=trace,
            metrics=metrics,
            baseline_metrics=baseline,
            improvement_pct=round(improvement_pct, 2),
        )
        s.add(row)
        await s.commit()
        await s.refresh(row)
    return {
        "id": row.id, "metrics": metrics, "baseline_metrics": baseline,
        "improvement_pct": round(improvement_pct, 2), "trace_len": len(trace),
    }


@app.get("/api/backtest", tags=["new-features"])
async def list_backtests(
    user: Annotated[dict, Depends(require_auth)],
    page: int = 1,
    page_size: int = 20,
):
    """Paginated list of backtests for the current user."""
    page = max(1, page)
    page_size = min(100, max(1, page_size))
    async with SessionLocal() as s:
        q = select(Backtest).where(Backtest.user_key == user["sub"])
        total = (await s.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
        rows = (
            await s.execute(
                q.order_by(Backtest.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars().all()
    return {
        "data": [
            {
                "id": r.id, "theme": r.theme, "paper": r.paper,
                "metrics": r.metrics, "baseline_metrics": r.baseline_metrics,
                "improvement_pct": r.improvement_pct,
                "created_at": r.created_at,
            } for r in rows
        ],
        "pagination": {
            "page": page, "page_size": page_size,
            "total_items": total, "total_pages": (total + page_size - 1) // page_size,
        },
    }


# --- NEW FEATURE 2 — What-if portfolio overlay on themes ---
@app.post("/api/portfolio/whatif", tags=["new-features"])
async def portfolio_whatif(
    user: Annotated[dict, Depends(require_auth)],
    body: dict,
):
    """Apply a hypothetical theme improvement to each holding (and ask the LLM
    for a one-paragraph analysis if available).
    Body: { "theme": "networking" | ..., "improvement_pct": float, "tickers_affected": ["NVDA","AMD"] }
    """
    from .portfolio import _fetch_price_alpha_vantage, PortfolioHolding

    theme = body.get("theme", "networking")
    improvement_pct = float(body.get("improvement_pct", 5.0))
    tickers_affected = [t.upper() for t in (body.get("tickers_affected") or [])]

    async with SessionLocal() as s:
        holdings = (
            await s.execute(
                select(PortfolioHolding).where(PortfolioHolding.user_id == user["user_id"])
            )
        ).scalars().all()

    overlay = []
    for h in holdings:
        price, src = await _fetch_price_alpha_vantage(h.ticker)
        affected = h.ticker in tickers_affected
        adj = improvement_pct / 100.0 if affected else 0.0
        implied_price = price * (1 + adj)
        overlay.append({
            "ticker": h.ticker, "shares": h.shares,
            "current_price": round(price, 4),
            "implied_price": round(implied_price, 4),
            "implied_value": round(implied_price * h.shares, 2),
            "affected": affected, "price_source": src,
        })

    analysis = ""
    error_msg = None
    t0 = time.perf_counter()
    try:
        prompt = (
            f"Theme: {theme}; suggested improvement {improvement_pct}% applied to "
            f"{tickers_affected or 'no tickers'}. Holdings overlay JSON: {overlay}. "
            "Write ≤120 words explaining the implied portfolio impact and naming "
            "the most-leveraged ticker. STRICT JSON {\"analysis\": \"...\"}."
        )
        raw = await openrouter.chat(
            messages=[
                {"role": "system", "content": "You are an investment analyst."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            max_tokens=400,
        )
        analysis = parse_ai_json(raw).get("analysis", "")
    except Exception as e:
        error_msg = str(e)
    llm_ms = int((time.perf_counter() - t0) * 1000)

    await log_ai_result(
        feature="portfolio_whatif",
        user_key=user["sub"],
        ref_type="theme",
        ref_id=theme,
        input_data=body,
        output_data={"overlay": overlay, "analysis": analysis},
        error=error_msg,
        duration_ms=llm_ms,
    )

    return {"overlay": overlay, "analysis": analysis, "error": error_msg}


# --- NEW FEATURE 3 — Algorithm leaderboard ---
@app.get("/api/leaderboard/{theme}", tags=["new-features"])
async def leaderboard(
    theme: str,
    user: Annotated[dict, Depends(require_auth)],
    page: int = 1,
    page_size: int = 20,
):
    """Aggregate average improvement_pct per paper for a theme, ranked desc."""
    page = max(1, page)
    page_size = min(100, max(1, page_size))

    async with SessionLocal() as s:
        # The per-theme algo-runs tables share AlgoRunMixin shape (paper, improvement_pct).
        # Map to the right model:
        from .db import (
            NetworkAlgoRun, EnergyAlgoRun, InferenceAlgoRun, PhotonicsAlgoRun, HBMAlgoRun,
        )
        model_map = {
            "networking": NetworkAlgoRun,
            "energy": EnergyAlgoRun,
            "inference": InferenceAlgoRun,
            "photonics": PhotonicsAlgoRun,
        }
        if theme == "hbm":
            # HBMAlgoRun uses a different schema: prompt_tokens/budget/recall/compression_ratio.
            rows = (await s.execute(select(HBMAlgoRun))).scalars().all()
            agg: dict[str, dict] = {}
            for r in rows:
                a = agg.setdefault(r.paper, {"runs": 0, "recall": 0.0, "compression_ratio": 0.0, "arxiv": r.arxiv})
                a["runs"] += 1
                a["recall"] += r.recall
                a["compression_ratio"] += r.compression_ratio
            ranked = sorted(
                [
                    {"paper": p, "arxiv": v["arxiv"], "runs": v["runs"],
                     "avg_recall": round(v["recall"] / v["runs"], 3),
                     "avg_compression_ratio": round(v["compression_ratio"] / v["runs"], 3)}
                    for p, v in agg.items()
                ],
                key=lambda x: x["avg_compression_ratio"],
                reverse=True,
            )
        else:
            Model = model_map.get(theme)
            if Model is None:
                raise HTTPException(404, f"unknown theme: {theme}")
            rows = (await s.execute(select(Model))).scalars().all()
            agg = {}
            for r in rows:
                a = agg.setdefault(r.paper, {"runs": 0, "improvement_sum": 0.0, "arxiv": r.arxiv})
                a["runs"] += 1
                a["improvement_sum"] += r.improvement_pct or 0.0
            ranked = sorted(
                [
                    {"paper": p, "arxiv": v["arxiv"], "runs": v["runs"],
                     "avg_improvement_pct": round(v["improvement_sum"] / v["runs"], 2)}
                    for p, v in agg.items()
                ],
                key=lambda x: x["avg_improvement_pct"],
                reverse=True,
            )

    total = len(ranked)
    sliced = ranked[(page - 1) * page_size: page * page_size]

    summary = ""
    error_msg = None
    try:
        if ranked:
            prompt = (
                f"Theme: {theme}. Leaderboard: {ranked[:5]}. Write ≤80 words "
                "naming the top paper, citing its arXiv id, and one sentence on why."
                " STRICT JSON {\"summary\": \"...\"}."
            )
            raw = await openrouter.chat(
                messages=[
                    {"role": "system", "content": "You are an AI systems analyst."},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                max_tokens=300,
            )
            summary = parse_ai_json(raw).get("summary", "")
    except Exception as e:
        error_msg = str(e)

    return {
        "theme": theme, "summary": summary, "summary_error": error_msg,
        "data": sliced,
        "pagination": {
            "page": page, "page_size": page_size,
            "total_items": total, "total_pages": (total + page_size - 1) // page_size,
        },
    }


# --- NEW FEATURE 4 — Last-refresh status for the price worker ---
@app.get("/api/portfolio/value/last-refresh", tags=["new-features"])
async def price_last_refresh(user: Annotated[dict, Depends(require_auth)]):
    """Return the most recent price-cache refresh per ticker so the UI can
    surface freshness rather than silently falling back to mock prices.
    """
    from .db import PriceCacheEntry
    async with SessionLocal() as s:
        rows = (
            await s.execute(
                select(PriceCacheEntry).order_by(PriceCacheEntry.fetched_at.desc())
            )
        ).scalars().all()
    return {
        "data": [
            {
                "ticker": r.ticker, "price": r.price, "source": r.source,
                "fetched_at": r.fetched_at,
            } for r in rows
        ],
        "alpha_vantage_configured": bool(settings.alpha_vantage_api_key),
    }


# --- NEW FEATURE 5 — Reproducibility seed export ---
@app.get("/api/reproducibility/{theme}/{paper}", tags=["new-features"])
async def reproducibility_export(
    theme: str,
    paper: str,
    user: Annotated[dict, Depends(require_auth)],
):
    """Export a tar.gz containing source.py, inputs.json, source_hash, and a README.

    Lets a researcher cite an exact deterministic run by hash + seed.
    """
    try:
        src = paper_sources.get_source(theme, paper)
        inputs = paper_sources.get_inputs(theme, paper)
    except KeyError:
        raise HTTPException(404, f"unknown theme/paper: {theme}/{paper}")

    source_hash = hashlib.sha256(src["source"].encode()).hexdigest()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        def add_text(name: str, content: str) -> None:
            data = content.encode()
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        add_text("source.py", src["source"])
        add_text("inputs.json", str(inputs))
        add_text("source_hash.txt", source_hash)
        add_text(
            "README.md",
            f"# Reproducibility bundle — {theme}/{paper}\n\n"
            f"Source SHA-256: `{source_hash}`\n"
            f"Default inputs: see inputs.json\n\n"
            f"Recreate locally with `python -c 'exec(open(\"source.py\").read())'`.",
        )
    buf.seek(0)
    fname = f"{theme}-{paper}-{source_hash[:8]}.tar.gz"
    return Response(
        content=buf.getvalue(),
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
