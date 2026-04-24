"""
Theme 5 — Photonic collective scheduler.

Problem: CPO / silicon-photonics transceivers give ~5× bandwidth at lower
pJ/bit than copper, but clusters are mixed — optical links between some
GPUs, copper between others. A collective (all-reduce / all-gather) that
ignores this topology runs at copper speed.

Solution: topology-aware critical-path scheduling. Given a DAG of
collectives (source set, destination set, byte volume) and a topology
where a fraction of links are optical, greedily schedule on the
fastest-available path per op. Compare to a "copper everywhere" baseline.

Usage:
  POST /api/themes/photonics/schedule { name, num_gpus, optical_fraction,
                                        optical_gbps, copper_gbps, dag_size }
  GET  /api/themes/photonics/scenarios
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import PhotonicScenario, PhotonicsAlgoRun, SessionLocal
from . import photonics_algorithms as algos


@dataclass
class CollOp:
    src: int
    dst: int
    bytes_: int


def _build_dag(num_gpus: int, dag_size: int, rng: random.Random) -> list[CollOp]:
    return [
        CollOp(
            src=rng.randrange(num_gpus),
            dst=rng.randrange(num_gpus),
            bytes_=rng.randint(1, 64) * 1024 * 1024,  # 1–64 MiB
        )
        for _ in range(dag_size)
    ]


def _link_is_optical(src: int, dst: int, optical_fraction: float, rng: random.Random) -> bool:
    # Deterministic hash-based assignment so a link's optical-ness is stable.
    h = hash((min(src, dst), max(src, dst))) & 0xFFFFFFFF
    return (h / 2**32) < optical_fraction


def schedule(num_gpus: int, optical_fraction: float,
             optical_gbps: float, copper_gbps: float,
             dag_size: int, seed: int = 42) -> dict:
    rng = random.Random(seed)
    ops = _build_dag(num_gpus, dag_size, rng)

    # Track per-link available_at timestamps (contention model).
    baseline_avail: dict[tuple[int, int], float] = {}
    optim_avail: dict[tuple[int, int], float] = {}

    baseline_times: list[float] = []
    optim_times: list[float] = []

    for op in ops:
        if op.src == op.dst:
            baseline_times.append(0)
            optim_times.append(0)
            continue
        key = (min(op.src, op.dst), max(op.src, op.dst))

        # Baseline: copper everywhere.
        bw_gbps_base = copper_gbps
        dur_base = op.bytes_ * 8 / (bw_gbps_base * 1e9)   # sec
        start_base = baseline_avail.get(key, 0)
        end_base = start_base + dur_base
        baseline_avail[key] = end_base
        baseline_times.append(end_base)

        # Optimized: optical where available, else copper; also prefer
        # routing through an optical intermediate if that's faster.
        link_optical = _link_is_optical(op.src, op.dst, optical_fraction, rng)
        bw_gbps_opt = optical_gbps if link_optical else copper_gbps
        dur_opt = op.bytes_ * 8 / (bw_gbps_opt * 1e9)
        start_opt = optim_avail.get(key, 0)
        end_opt = start_opt + dur_opt

        # Try a 2-hop path through an optical intermediate (if available)
        if not link_optical:
            best_2hop = None
            for mid in range(num_gpus):
                if mid in (op.src, op.dst):
                    continue
                k1 = (min(op.src, mid), max(op.src, mid))
                k2 = (min(mid, op.dst), max(mid, op.dst))
                ok1 = _link_is_optical(op.src, mid, optical_fraction, rng)
                ok2 = _link_is_optical(mid, op.dst, optical_fraction, rng)
                if ok1 and ok2:
                    bw = optical_gbps
                    dur = 2 * op.bytes_ * 8 / (bw * 1e9)
                    start = max(optim_avail.get(k1, 0), optim_avail.get(k2, 0))
                    end = start + dur
                    if best_2hop is None or end < best_2hop[0]:
                        best_2hop = (end, k1, k2, start + dur)
            if best_2hop and best_2hop[0] < end_opt:
                end_opt, k1, k2, avail = best_2hop
                optim_avail[k1] = avail
                optim_avail[k2] = avail
        else:
            optim_avail[key] = end_opt
        optim_times.append(end_opt)

    baseline_ms = (max(baseline_times) if baseline_times else 0) * 1000
    optimized_ms = (max(optim_times) if optim_times else 0) * 1000
    speedup_pct = 100 * (baseline_ms - optimized_ms) / max(baseline_ms, 1e-9)
    return {
        "baseline_ms": baseline_ms,
        "optimized_ms": optimized_ms,
        "speedup_pct": speedup_pct,
    }


# ---- FastAPI ----
router = APIRouter(prefix="/api/themes/photonics", tags=["photonics"])


async def _session() -> AsyncSession:
    async with SessionLocal() as s:
        yield s


class ScheduleBody(BaseModel):
    name: str
    num_gpus: int = 64
    optical_fraction: float = 0.3
    optical_gbps: float = 1600   # per-link CPO ballpark
    copper_gbps: float = 400     # NVLink-ish
    dag_size: int = 128
    seed: int = 42


@router.post("/schedule")
async def run_schedule(body: ScheduleBody, s: AsyncSession = Depends(_session)):
    r = schedule(body.num_gpus, body.optical_fraction,
                 body.optical_gbps, body.copper_gbps,
                 body.dag_size, body.seed)
    row = PhotonicScenario(
        name=body.name, num_gpus=body.num_gpus,
        optical_fraction=body.optical_fraction,
        optical_gbps=body.optical_gbps, copper_gbps=body.copper_gbps,
        dag_size=body.dag_size,
        baseline_ms=r["baseline_ms"], optimized_ms=r["optimized_ms"],
        speedup_pct=r["speedup_pct"],
    )
    s.add(row)
    await s.commit()
    return r


@router.get("/algorithms")
async def list_photonics_algos(s: AsyncSession = Depends(_session), limit: int = 200):
    rows = (await s.execute(
        select(PhotonicsAlgoRun).order_by(PhotonicsAlgoRun.created_at.desc()).limit(limit)
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


@router.get("/scenarios")
async def list_scenarios(s: AsyncSession = Depends(_session), limit: int = 100):
    rows = (await s.execute(
        select(PhotonicScenario).order_by(PhotonicScenario.created_at.desc()).limit(limit)
    )).scalars().all()
    return {
        "scenarios": [
            {
                "id": r.id, "name": r.name,
                "num_gpus": r.num_gpus,
                "optical_fraction": r.optical_fraction,
                "optical_gbps": r.optical_gbps,
                "copper_gbps": r.copper_gbps,
                "dag_size": r.dag_size,
                "baseline_ms": round(r.baseline_ms, 2),
                "optimized_ms": round(r.optimized_ms, 2),
                "speedup_pct": round(r.speedup_pct, 1),
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    }
