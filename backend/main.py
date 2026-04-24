"""FastAPI app wiring all 5 themes + a small overview endpoint."""

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from .db import (
    HBMDecision, NetworkPlan, EnergyDecision, EnergyRegion,
    InferenceRun, PhotonicScenario, SessionLocal, init_db,
)
from .themes import hbm, networking, energy, inference, photonics
from .themes import sources as paper_sources
from . import openrouter


app = FastAPI(title="investment — 5-theme solutions")
app.include_router(hbm.router)
app.include_router(networking.router)
app.include_router(energy.router)
app.include_router(inference.router)
app.include_router(photonics.router)


@app.on_event("startup")
async def _startup() -> None:
    await init_db()


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/api/source/{theme}/{paper}")
async def paper_source(theme: str, paper: str):
    """Return the actual Python source backing a paper's implementation."""
    try:
        return paper_sources.get_source(theme, paper)
    except KeyError:
        raise HTTPException(404, f"unknown theme/paper: {theme}/{paper}")


@app.post("/api/run/{theme}/{paper}")
async def paper_run(theme: str, paper: str):
    """Run the paper's algorithm through OpenRouter: the LLM simulates the
    execution given the source + inputs and returns the result dict."""
    import time
    from .config import settings
    try:
        src = paper_sources.get_source(theme, paper)
        inputs = paper_sources.get_inputs(theme, paper)

        t0 = time.perf_counter()
        result = await openrouter.execute_algorithm(
            theme=theme, paper=paper,
            source=src["source"], inputs=inputs,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return {"theme": theme, "paper": paper,
                "elapsed_ms": round(elapsed_ms, 2), "result": result,
                "model": settings.openrouter_model,
                "executed_by": "openrouter"}
    except KeyError:
        raise HTTPException(404, f"unknown theme/paper: {theme}/{paper}")
    except openrouter.OpenRouterError as e:
        raise HTTPException(502, f"OpenRouter: {e}")
    except Exception as e:
        raise HTTPException(500, f"run failed: {type(e).__name__}: {e}")


@app.post("/api/narrate/{theme}/{paper}")
async def paper_narrate(theme: str, paper: str):
    """Run the paper's algorithm through OpenRouter, then ask the LLM to
    explain + chart it. Returns {run, narration_md, chart, model}."""
    import time
    from .config import settings
    try:
        src = paper_sources.get_source(theme, paper)
        inputs = paper_sources.get_inputs(theme, paper)

        t0 = time.perf_counter()
        result = await openrouter.execute_algorithm(
            theme=theme, paper=paper,
            source=src["source"], inputs=inputs,
        )
        run_ms = (time.perf_counter() - t0) * 1000

        t1 = time.perf_counter()
        llm_out = await openrouter.narrate_algorithm(
            theme=theme, paper=paper,
            source=src["source"], inputs=inputs, result=result,
        )
        llm_ms = (time.perf_counter() - t1) * 1000

        return {
            "theme": theme, "paper": paper,
            "run": {"elapsed_ms": round(run_ms, 2), "result": result},
            "narration_md": llm_out.get("narration_md", ""),
            "chart": llm_out.get("chart"),
            "model": settings.openrouter_model,
            "llm_elapsed_ms": round(llm_ms, 2),
            "executed_by": "openrouter",
        }
    except KeyError:
        raise HTTPException(404, f"unknown theme/paper: {theme}/{paper}")
    except openrouter.OpenRouterError as e:
        raise HTTPException(502, f"OpenRouter: {e}")
    except Exception as e:
        raise HTTPException(500, f"narrate failed: {type(e).__name__}: {e}")


@app.get("/api/overview")
async def overview():
    async with SessionLocal() as s:
        hbm_total = (await s.execute(select(func.count(HBMDecision.id)))).scalar_one()
        net_total = (await s.execute(select(func.count(NetworkPlan.id)))).scalar_one()
        en_total  = (await s.execute(select(func.count(EnergyDecision.id)))).scalar_one()
        en_reg    = (await s.execute(select(func.count(EnergyRegion.id)))).scalar_one()
        inf_total = (await s.execute(select(func.count(InferenceRun.id)))).scalar_one()
        ph_total  = (await s.execute(select(func.count(PhotonicScenario.id)))).scalar_one()

        # KPI row
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
