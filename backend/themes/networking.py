"""
Theme 2 — AI-networking / placement planner.

Problem: Rubin / NVL72 class clusters change inference from a compute problem
to a bandwidth problem. The choice of (TP, PP, DP) parallelism dominates
step time, and the sweet spot depends on model size, batch, and the
NVLink-vs-inter-node bandwidth gap.

Solution: closed-form Megatron-style perf model. For every valid
(tp, pp, dp) factorization of the cluster:
  - TP all-reduce per layer     : 2*(tp-1)/tp * act_bytes / nvlink_bw
  - PP p2p send/recv per stage  : act_bytes / inter_bw
  - Pipeline bubble             : (pp-1)/num_microbatches * per-stage-fwd+bwd
Pick min step time. Report comm_frac = comm / step.

Usage:
  POST /api/themes/networking/plan { model_name, params_b, layers, hidden,
                                     cluster_name, gpus_total, gpus_per_node,
                                     nvlink_gbps, inter_gbps, batch, seq_len }
  GET  /api/themes/networking/plans
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import NetworkPlan, NetworkAlgoRun, SessionLocal
from . import networking_algorithms as algos


BF16 = 2
TFLOPS_H100 = 989   # bf16 dense TFLOPS
TFLOPS_B200 = 2250
TFLOPS_R200 = 4000  # rough forward projection


def _divisors(n: int) -> list[int]:
    return [d for d in range(1, n + 1) if n % d == 0]


def plan(model_name: str, params_b: float, layers: int, hidden: int,
         gpus_total: int, gpus_per_node: int,
         nvlink_gbps: float, inter_gbps: float,
         batch: int = 16, seq_len: int = 4096, microbatches: int = 8,
         tflops_per_gpu: float = TFLOPS_H100) -> dict:
    """
    Search (tp, pp, dp) with tp*pp*dp == gpus_total, tp | gpus_per_node,
    pick the split with min predicted step time.
    """
    param_bytes = params_b * 1e9 * BF16
    # per-token activation bytes (rough: layers * hidden * bf16 for grad-sync)
    act_bytes_per_tok = layers * hidden * BF16

    best = None
    for tp in _divisors(gpus_per_node):
        for pp in _divisors(gpus_total // tp):
            if (gpus_total // tp) % pp != 0:
                continue
            dp = gpus_total // (tp * pp)
            if dp < 1:
                continue

            # per-GPU params
            p_bytes = param_bytes / (tp * pp)
            # compute (forward+backward ~ 6*params*tokens FLOPs)
            tokens = batch * seq_len
            compute_s = 6 * params_b * 1e9 * tokens / (dp * pp * tp * tflops_per_gpu * 1e12)

            # TP comm: per-layer all-reduce of activations (2x per layer fwd+bwd)
            tp_comm_bytes_per_layer = 2 * (tp - 1) / max(tp, 1) * batch * seq_len * hidden * BF16
            tp_comm_s = layers * tp_comm_bytes_per_layer / max(nvlink_gbps * 1e9, 1)

            # PP comm: p2p activations at stage boundaries
            pp_comm_bytes = 2 * batch * seq_len * hidden * BF16  # fwd + bwd activations
            pp_comm_s = (pp - 1) * pp_comm_bytes / max(inter_gbps * 1e9, 1)

            # Pipeline bubble
            bubble_s = (pp - 1) / max(microbatches, 1) * compute_s

            step_s = compute_s + tp_comm_s + pp_comm_s + bubble_s
            comm_s = tp_comm_s + pp_comm_s + bubble_s
            rec = {
                "tp": tp, "pp": pp, "dp": dp,
                "step_ms": step_s * 1000,
                "comm_ms": comm_s * 1000,
                "compute_ms": compute_s * 1000,
                "comm_frac": comm_s / max(step_s, 1e-9),
            }
            if best is None or rec["step_ms"] < best["step_ms"]:
                best = rec

    return best or {"tp": 1, "pp": 1, "dp": gpus_total,
                    "step_ms": 0, "comm_ms": 0, "compute_ms": 0, "comm_frac": 0}


# ---- FastAPI ----
router = APIRouter(prefix="/api/themes/networking", tags=["networking"])


async def _session() -> AsyncSession:
    async with SessionLocal() as s:
        yield s


class PlanBody(BaseModel):
    model_name: str
    params_b: float
    layers: int
    hidden: int
    cluster_name: str
    gpus_total: int
    gpus_per_node: int = 8
    nvlink_gbps: float = 900
    inter_gbps: float = 400
    batch: int = 16
    seq_len: int = 4096


@router.post("/plan")
async def compute_plan(body: PlanBody, s: AsyncSession = Depends(_session)):
    best = plan(
        body.model_name, body.params_b, body.layers, body.hidden,
        body.gpus_total, body.gpus_per_node, body.nvlink_gbps, body.inter_gbps,
        body.batch, body.seq_len,
    )
    row = NetworkPlan(
        model_name=body.model_name, params_b=body.params_b,
        layers=body.layers, hidden=body.hidden,
        cluster_name=body.cluster_name, gpus_total=body.gpus_total,
        gpus_per_node=body.gpus_per_node,
        nvlink_gbps=body.nvlink_gbps, inter_gbps=body.inter_gbps,
        tp=best["tp"], pp=best["pp"], dp=best["dp"],
        step_ms=best["step_ms"], comm_ms=best["comm_ms"],
        comm_frac=best["comm_frac"],
    )
    s.add(row)
    await s.commit()
    return best


@router.get("/algorithms")
async def list_network_algos(s: AsyncSession = Depends(_session), limit: int = 200):
    rows = (await s.execute(
        select(NetworkAlgoRun).order_by(NetworkAlgoRun.created_at.desc()).limit(limit)
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


@router.get("/plans")
async def list_plans(s: AsyncSession = Depends(_session), limit: int = 100):
    rows = (await s.execute(
        select(NetworkPlan).order_by(NetworkPlan.created_at.desc()).limit(limit)
    )).scalars().all()
    return {
        "plans": [
            {
                "id": r.id, "model": r.model_name, "params_b": r.params_b,
                "cluster": r.cluster_name, "gpus": r.gpus_total,
                "nvlink_gbps": r.nvlink_gbps, "inter_gbps": r.inter_gbps,
                "tp": r.tp, "pp": r.pp, "dp": r.dp,
                "step_ms": round(r.step_ms, 1),
                "comm_ms": round(r.comm_ms, 1),
                "comm_frac": round(r.comm_frac, 3),
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    }
