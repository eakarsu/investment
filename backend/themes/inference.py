"""
Theme 4 — Inference auto-tuner.

Problem: inference is now the money-maker, and paper-derived tricks
(staggered batching, length bucketing, spec decoding, KV reuse, model
routing) can each unlock 20-50% at equal SLA. Teams rarely know which
to turn on.

Solution: ingest a stream of inference telemetry (prompt/completion
tokens, latency, TTFT, cost, KV reuse), compute summary stats + a set
of rule-based recommendations with paper citations.

Usage:
  GET /api/themes/inference/stats
  GET /api/themes/inference/recs
"""

from __future__ import annotations

from statistics import mean, median, pstdev
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import InferenceRun, InferenceAlgoRun, SessionLocal
from . import inference_algorithms as algos


router = APIRouter(prefix="/api/themes/inference", tags=["inference"])


async def _session() -> AsyncSession:
    async with SessionLocal() as s:
        yield s


def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0
    xs = sorted(xs)
    i = int(max(0, min(len(xs) - 1, round(p / 100 * (len(xs) - 1)))))
    return xs[i]


@router.get("/stats")
async def stats(s: AsyncSession = Depends(_session)):
    rows = (await s.execute(select(InferenceRun))).scalars().all()
    if not rows:
        return {
            "requests": 0, "prompt_tokens": 0, "completion_tokens": 0,
            "total_cost_usd": 0.0,
            "latency_ms_p50": 0, "latency_ms_p95": 0,
            "ttft_ms_p50": 0, "ttft_ms_p95": 0,
            "by_model": {},
        }
    lat = [r.latency_ms for r in rows]
    ttft = [r.ttft_ms for r in rows]
    by_model: dict[str, dict[str, Any]] = {}
    for r in rows:
        b = by_model.setdefault(r.model, {"count": 0, "prompt_tokens": 0, "cost": 0.0})
        b["count"] += 1
        b["prompt_tokens"] += r.prompt_tokens
        b["cost"] += r.cost_usd
    return {
        "requests": len(rows),
        "prompt_tokens": sum(r.prompt_tokens for r in rows),
        "completion_tokens": sum(r.completion_tokens for r in rows),
        "total_cost_usd": round(sum(r.cost_usd for r in rows), 4),
        "latency_ms_p50": round(_pct(lat, 50), 1),
        "latency_ms_p95": round(_pct(lat, 95), 1),
        "ttft_ms_p50": round(_pct(ttft, 50), 1),
        "ttft_ms_p95": round(_pct(ttft, 95), 1),
        "by_model": {m: {k: (round(v, 4) if isinstance(v, float) else v) for k, v in b.items()}
                     for m, b in by_model.items()},
    }


@router.get("/algorithms")
async def list_inference_algos(s: AsyncSession = Depends(_session), limit: int = 200):
    rows = (await s.execute(
        select(InferenceAlgoRun).order_by(InferenceAlgoRun.created_at.desc()).limit(limit)
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


@router.get("/recs")
async def recs(s: AsyncSession = Depends(_session)):
    rows = (await s.execute(select(InferenceRun))).scalars().all()
    out = []
    if not rows:
        return {"recommendations": out}

    prompts = [r.prompt_tokens for r in rows]
    completions = [r.completion_tokens for r in rows]
    kv_reuse = [r.kv_reuse_pct for r in rows]

    if len(prompts) >= 5:
        cv = pstdev(prompts) / max(mean(prompts), 1)
        if cv > 0.6:
            out.append({
                "module": "batching",
                "title": "Enable Staggered Batch Scheduling",
                "detail": f"Prompt-length CV={cv:.2f}. Staggered batching buffers briefly to form optimal batches without hurting TTFT.",
                "paper": "Staggered Batch Scheduling, arXiv:2512.16134 (Dec 2025)",
                "expected_gain": "+20–40% throughput at equal p99 TTFT",
            })

    p95 = _pct(prompts, 95)
    p50 = max(_pct(prompts, 50), 1)
    if p95 / p50 > 4:
        out.append({
            "module": "batching",
            "title": "Enable length-bucket batching (BucketServe)",
            "detail": f"Prompt p95/p50 ratio is {p95/p50:.1f}× — bimodal traffic wastes pad tokens in naive batches.",
            "paper": "BucketServe, arXiv:2411.03594",
            "expected_gain": "+15–30% throughput, -20% tail latency",
        })

    long_ctx = sum(1 for p in prompts if p > 4000) / len(prompts)
    if long_ctx > 0.2:
        out.append({
            "module": "spec-decode",
            "title": "Enable QuantSpec speculative decoding",
            "detail": f"{long_ctx*100:.0f}% of prompts exceed 4k tokens; quantized draft models keep KV under control at long ctx.",
            "paper": "QuantSpec, arXiv:2503.10069",
            "expected_gain": "+1.5–2× tokens/sec at equal quality",
        })

    avg_reuse = mean(kv_reuse) if kv_reuse else 0
    if avg_reuse < 30 and any(p > 1024 for p in prompts):
        out.append({
            "module": "cache",
            "title": "Enable prefix-cache reuse (LMCache / RadixAttention)",
            "detail": f"Average KV reuse is {avg_reuse:.1f}% — room to hash & share prefixes across calls.",
            "paper": "LMCache, arXiv:2407.00079 + SGLang RadixAttention",
            "expected_gain": "-30-60% TTFT on repeated prefixes",
        })

    models = {r.model for r in rows}
    if len(models) == 1:
        out.append({
            "module": "routing",
            "title": "Add RouteLLM-style tier routing",
            "detail": "All traffic hits one model. Cheap queries could go to a smaller tier and save cost.",
            "paper": "RouteLLM, arXiv:2406.18665",
            "expected_gain": "-40–85% cost at ≤1% quality drop",
        })

    return {"recommendations": out}
