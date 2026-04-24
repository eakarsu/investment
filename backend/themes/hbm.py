"""
Theme 1 — HBM-scarcity optimizer.

Problem: HBM is the scarcest resource in modern GPUs. A single H100 has 80 GB.
KV-cache dominates serving memory at long context. Running hot → OOM + request kills.

Solution: admission controller that:
  1. Projects KV-cache footprint of an incoming request
       kv_bytes = 2 * layers * kv_heads * head_dim * seq_len * bf16_bytes
  2. Simulates HBM pressure against a live cache budget.
  3. Picks admit / evict-LRU-and-admit / reject with a projected-pressure threshold.

Usage:
  POST /api/themes/hbm/admit  { model, prompt_tokens, max_completion }
  GET  /api/themes/hbm/decisions
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import HBMDecision, HBMAlgoRun, SessionLocal
from . import hbm_algorithms as algos


# ---- model specs (layers, kv_heads, head_dim) ----
MODEL_SPECS: dict[str, tuple[int, int, int]] = {
    "llama-7b":       (32, 32, 128),
    "llama-13b":      (40, 40, 128),
    "llama-70b":      (80,  8, 128),   # GQA
    "llama-405b":     (126, 8, 128),   # GQA
    "mistral-7b":     (32,  8, 128),
    "mixtral-8x7b":   (32,  8, 128),
    "qwen-72b":       (80, 64, 128),
    "gpt-oss":        (96,  8, 128),
}

BF16 = 2  # bytes per element
HBM_PER_GPU_GB = 80
TENSOR_PARALLEL = 1  # single-GPU view for the simulator


@dataclass
class Cluster:
    total_hbm_gb: float
    weights_gb: float
    used_kv_mb: float = 0.0   # mutable state across admissions
    admitted: list[tuple[str, float]] = None  # (request_id, kv_mb) in LRU order

    def __post_init__(self):
        if self.admitted is None:
            self.admitted = []

    @property
    def free_mb(self) -> float:
        return (self.total_hbm_gb - self.weights_gb) * 1024 - self.used_kv_mb

    @property
    def pressure_pct(self) -> float:
        cap = (self.total_hbm_gb - self.weights_gb) * 1024
        return 100.0 * self.used_kv_mb / cap if cap > 0 else 100.0


def kv_mb(model: str, seq_tokens: int) -> float:
    """KV-cache size in MiB for a given model and sequence length."""
    layers, kv_heads, head_dim = MODEL_SPECS[model]
    bytes_ = 2 * layers * kv_heads * head_dim * seq_tokens * BF16  # 2 = K+V
    return bytes_ / (1024 * 1024)


def weights_gb(model: str) -> float:
    """Rough bf16 weight size for the public models in MODEL_SPECS."""
    return {
        "llama-7b": 13, "llama-13b": 26, "llama-70b": 140, "llama-405b": 810,
        "mistral-7b": 14, "mixtral-8x7b": 90, "qwen-72b": 144, "gpt-oss": 160,
    }.get(model, 40)


# ---- core algorithm ----
Decision = Literal["admit", "evict", "reject"]


def admission(cluster: Cluster, model: str, prompt: int, max_completion: int,
              pressure_threshold_pct: float = 85.0) -> tuple[Decision, float, int, str]:
    """
    Returns (decision, kv_mb_needed, evicted_count, reason).
    Mutates cluster state on admit/evict.
    """
    need_mb = kv_mb(model, prompt + max_completion)
    cap_mb = (cluster.total_hbm_gb - cluster.weights_gb) * 1024

    if need_mb > cap_mb:
        return "reject", need_mb, 0, f"need {need_mb:.0f} MiB > total KV budget {cap_mb:.0f} MiB"

    projected = cluster.used_kv_mb + need_mb
    projected_pct = 100.0 * projected / cap_mb

    if projected_pct <= pressure_threshold_pct:
        rid = uuid.uuid4().hex[:8]
        cluster.used_kv_mb = projected
        cluster.admitted.append((rid, need_mb))
        return "admit", need_mb, 0, f"fits at {projected_pct:.1f}% pressure"

    # Need to evict LRU entries until pressure returns below threshold.
    target_mb = pressure_threshold_pct / 100.0 * cap_mb - need_mb
    evicted = 0
    while cluster.used_kv_mb > target_mb and cluster.admitted:
        _, mb = cluster.admitted.pop(0)
        cluster.used_kv_mb -= mb
        evicted += 1
    if cluster.used_kv_mb + need_mb > cap_mb:
        return "reject", need_mb, evicted, f"cannot free enough ({evicted} evicted, still over)"
    rid = uuid.uuid4().hex[:8]
    cluster.used_kv_mb += need_mb
    cluster.admitted.append((rid, need_mb))
    projected_pct = 100.0 * cluster.used_kv_mb / cap_mb
    return "evict", need_mb, evicted, f"evicted {evicted} LRU, landed at {projected_pct:.1f}%"


# ---- FastAPI ----
router = APIRouter(prefix="/api/themes/hbm", tags=["hbm"])


async def _session() -> AsyncSession:
    async with SessionLocal() as s:
        yield s


class AdmitBody(BaseModel):
    model: str
    prompt_tokens: int
    max_completion: int = 256
    num_gpus: int = 1


@router.post("/admit")
async def admit_req(body: AdmitBody, s: AsyncSession = Depends(_session)):
    if body.model not in MODEL_SPECS:
        return {"error": f"unknown model {body.model}", "known": list(MODEL_SPECS)}
    cluster = Cluster(
        total_hbm_gb=HBM_PER_GPU_GB * body.num_gpus,
        weights_gb=weights_gb(body.model),
    )
    # Pre-load cluster with ~60% pressure to make decisions interesting.
    cap = (cluster.total_hbm_gb - cluster.weights_gb) * 1024
    cluster.used_kv_mb = 0.6 * cap
    cluster.admitted = [(uuid.uuid4().hex[:8], cap * 0.6 / 5)] * 5

    decision, kv, evicted, reason = admission(
        cluster, body.model, body.prompt_tokens, body.max_completion,
    )
    row = HBMDecision(
        request_id=uuid.uuid4().hex[:12],
        model=body.model,
        prompt_tokens=body.prompt_tokens,
        max_completion=body.max_completion,
        kv_mb_projected=kv,
        hbm_pressure_pct=cluster.pressure_pct,
        decision=decision,
        evicted_count=evicted,
        reason=reason,
    )
    s.add(row)
    await s.commit()
    return {
        "decision": decision,
        "kv_mb_projected": round(kv, 1),
        "hbm_pressure_pct": round(cluster.pressure_pct, 1),
        "evicted_count": evicted,
        "reason": reason,
    }


@router.get("/decisions")
async def list_decisions(s: AsyncSession = Depends(_session), limit: int = 100):
    rows = (await s.execute(
        select(HBMDecision).order_by(HBMDecision.created_at.desc()).limit(limit)
    )).scalars().all()
    return {
        "decisions": [
            {
                "id": r.id,
                "request_id": r.request_id,
                "model": r.model,
                "prompt_tokens": r.prompt_tokens,
                "kv_mb": round(r.kv_mb_projected, 1),
                "pressure_pct": round(r.hbm_pressure_pct, 1),
                "decision": r.decision,
                "evicted": r.evicted_count,
                "reason": r.reason,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
        "summary": _summary(rows),
    }


@router.get("/algorithms")
async def list_algorithm_runs(s: AsyncSession = Depends(_session), limit: int = 200):
    rows = (await s.execute(
        select(HBMAlgoRun).order_by(HBMAlgoRun.created_at.desc()).limit(limit)
    )).scalars().all()
    return {
        "papers": algos.PAPERS,
        "runs": [
            {
                "id": r.id, "paper": r.paper, "arxiv": r.arxiv,
                "scenario": r.scenario,
                "prompt_tokens": r.prompt_tokens, "budget_tokens": r.budget_tokens,
                "recall": round(r.recall, 4),
                "compression_ratio": round(r.compression_ratio, 2),
                "notes": r.notes,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


def _summary(rows: list[HBMDecision]) -> dict:
    n = len(rows)
    if not n:
        return {"total": 0}
    admit = sum(1 for r in rows if r.decision == "admit")
    evict = sum(1 for r in rows if r.decision == "evict")
    reject = sum(1 for r in rows if r.decision == "reject")
    return {
        "total": n,
        "admit_pct": round(100 * admit / n, 1),
        "evict_pct": round(100 * evict / n, 1),
        "reject_pct": round(100 * reject / n, 1),
        "avg_kv_mb": round(sum(r.kv_mb_projected for r in rows) / n, 1),
        "avg_pressure_pct": round(sum(r.hbm_pressure_pct for r in rows) / n, 1),
    }
