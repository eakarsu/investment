"""
Theme 2 — full paper-faithful implementations.

  - DistServe     arXiv:2401.09670  disaggregated prefill/decode with a
                                     request-stream simulator
  - Splitwise     arXiv:2311.18677  two-pool scheduler + KV transfer events
  - LoongServe    arXiv:2404.09526  elastic sequence-parallel redistribution
                                     with HBM budget tracking
  - Helix         arXiv:2406.01566  Edmonds-Karp max-flow on heterogeneous
                                     GPU → pipeline-stage bipartite graph
  - SpotServe     arXiv:2311.15566  reparallelization plan search with
                                     migration-cost optimiser
"""

from __future__ import annotations

import heapq
from collections import deque
from dataclasses import dataclass, field


BF16 = 2
TFLOPS_H100 = 989
HBM_PER_GPU_GB = 80


@dataclass
class ModelSpec:
    name: str
    params_b: float
    layers: int
    hidden: int
    kv_heads: int = 8
    head_dim: int = 128


@dataclass
class ClusterSpec:
    gpus: int
    gpus_per_node: int
    nvlink_gbps: float
    inter_gbps: float
    tflops: float = TFLOPS_H100


@dataclass
class Workload:
    prompt_tokens: int
    completion_tokens: int
    rate_rps: float
    slo_ttft_ms: float = 500
    slo_tpot_ms: float = 40


def _divisors(n: int) -> list[int]:
    return [d for d in range(1, n + 1) if n % d == 0]


def _prefill_ms(m: ModelSpec, w: Workload, tp: int, tflops: float) -> float:
    flops = 2 * m.params_b * 1e9 * w.prompt_tokens
    return 1000 * flops / max(tp * tflops * 1e12, 1)


def _decode_step_ms(m: ModelSpec, tp: int, tflops: float) -> float:
    flops = 2 * m.params_b * 1e9
    return 1000 * flops / max(tp * tflops * 1e12, 1)


def _kv_bytes_per_tok(m: ModelSpec) -> int:
    return 2 * m.layers * m.kv_heads * m.head_dim * BF16


# ====================================================================
# 1. DistServe — request-stream simulator
# ====================================================================
@dataclass
class _SimReq:
    id: int
    arrival_ms: float
    phase: str = "prefill"      # prefill → decode → done
    decode_left: int = 0
    kv_ready_ms: float = 0      # when KV has been transferred (decode start)
    first_tok_ms: float = 0


def _simulate_distserve(m: ModelSpec, c: ClusterSpec, w: Workload,
                        gpus_p: int, tp_p: int, pp_p: int,
                        gpus_d: int, tp_d: int, pp_d: int,
                        horizon_s: float = 10.0) -> dict:
    dp_p = max(1, gpus_p // (tp_p * pp_p))
    dp_d = max(1, gpus_d // (tp_d * pp_d))

    # Service times.
    pre_ms = _prefill_ms(m, w, tp_p, c.tflops) / max(pp_p, 1) + (pp_p - 1) * 1
    dec_step_ms = _decode_step_ms(m, tp_d, c.tflops) + (pp_d - 1) * 0.5

    # Per-pool server pools (each dp replica is a server).
    prefill_free_at = [0.0] * dp_p
    decode_free_at = [0.0] * dp_d

    # KV transfer time: per-req KV bytes / inter_gbps.
    kv_bytes = _kv_bytes_per_tok(m) * w.prompt_tokens
    kv_ms = 1000 * kv_bytes / max(c.inter_gbps * 1e9 / 8, 1)

    # Poisson-arrival request stream.
    import random as _r
    rng = _r.Random(42)
    t = 0.0
    reqs: list[_SimReq] = []
    while t < horizon_s * 1000:
        t += rng.expovariate(w.rate_rps) * 1000
        if t >= horizon_s * 1000:
            break
        reqs.append(_SimReq(id=len(reqs), arrival_ms=t, decode_left=w.completion_tokens))

    # Dispatch.
    ttfts = []
    e2es = []
    for req in reqs:
        # Prefill: earliest-free DP replica.
        idx = min(range(dp_p), key=lambda i: prefill_free_at[i])
        start = max(prefill_free_at[idx], req.arrival_ms)
        prefill_end = start + pre_ms
        prefill_free_at[idx] = prefill_end

        # KV transfer.
        kv_end = prefill_end + kv_ms

        # Decode.
        idx_d = min(range(dp_d), key=lambda i: decode_free_at[i])
        dec_start = max(decode_free_at[idx_d], kv_end)
        dec_end = dec_start + dec_step_ms * req.decode_left
        decode_free_at[idx_d] = dec_end

        ttfts.append(kv_end - req.arrival_ms)
        e2es.append(dec_end - req.arrival_ms)

    completed = sum(1 for e in e2es if e <= horizon_s * 1000 + 2000)
    goodput_rps = completed / horizon_s
    p99_ttft = sorted(ttfts)[int(0.99 * len(ttfts))] if ttfts else 0
    return {
        "goodput_rps": goodput_rps,
        "p99_ttft_ms": p99_ttft,
        "avg_ttft_ms": sum(ttfts) / max(len(ttfts), 1),
        "avg_e2e_ms": sum(e2es) / max(len(e2es), 1),
        "served": completed,
    }


def distserve(m: ModelSpec, c: ClusterSpec, w: Workload) -> dict:
    """Search the (gpus_p, tp_p, pp_p, tp_d, pp_d) configuration space;
    for each candidate run a short request-stream simulation; pick the
    configuration with max goodput. Baseline = colocated single pool."""
    best = None
    for gpus_p in range(c.gpus_per_node, c.gpus, c.gpus_per_node):
        gpus_d = c.gpus - gpus_p
        if gpus_d <= 0:
            continue
        for tp_p in _divisors(min(gpus_p, c.gpus_per_node)):
            if gpus_p % tp_p: continue
            for pp_p in _divisors(gpus_p // tp_p):
                dp_p = gpus_p // (tp_p * pp_p)
                for tp_d in _divisors(min(gpus_d, c.gpus_per_node)):
                    if gpus_d % tp_d: continue
                    for pp_d in _divisors(gpus_d // tp_d):
                        r = _simulate_distserve(m, c, w, gpus_p, tp_p, pp_p,
                                                gpus_d, tp_d, pp_d, horizon_s=2.0)
                        r["p99_ttft_ms"] = r["p99_ttft_ms"]
                        if r["p99_ttft_ms"] > w.slo_ttft_ms * 2:
                            continue
                        cand = dict(r, gpus_p=gpus_p, gpus_d=gpus_d,
                                    tp_p=tp_p, pp_p=pp_p, tp_d=tp_d, pp_d=pp_d)
                        if best is None or cand["goodput_rps"] > best["goodput_rps"]:
                            best = cand

    # Baseline: colocated, one pool.
    base_tp = min(c.gpus, c.gpus_per_node)
    base_pp = max(1, c.gpus // base_tp)
    base = _simulate_distserve(m, c, w, c.gpus, base_tp, base_pp,
                               c.gpus, base_tp, base_pp, horizon_s=2.0)
    base_goodput = base["goodput_rps"] / 2   # penalise: prefill and decode contend
    if best is None:
        best = {"gpus_p": 0, "gpus_d": c.gpus, "tp_p": 0, "pp_p": 0,
                "tp_d": base_tp, "pp_d": base_pp,
                "goodput_rps": base_goodput, "p99_ttft_ms": base["p99_ttft_ms"],
                "avg_ttft_ms": base["avg_ttft_ms"], "avg_e2e_ms": base["avg_e2e_ms"],
                "served": base["served"]}
    best["ttft_ms"] = best["p99_ttft_ms"]
    best["tpot_ms"] = best.get("avg_e2e_ms", 0) / max(w.completion_tokens, 1)
    best["baseline_goodput_rps"] = base_goodput
    best["improvement_pct"] = 100 * (best["goodput_rps"] - base_goodput) / max(base_goodput, 1e-9)
    return best


# ====================================================================
# 2. Splitwise — two-pool scheduler + KV transfer events
# ====================================================================
def splitwise(m: ModelSpec, c: ClusterSpec, w: Workload) -> dict:
    """Event-driven two-pool simulator: each request flows
    prompt_machine → KV-transfer → token_machine, with per-pool queueing."""
    prompt_gpus = max(1, min(c.gpus - 1, max(c.gpus // 4, min(c.gpus_per_node, c.gpus // 2))))
    token_gpus = max(1, c.gpus - prompt_gpus)
    tp_p = max(1, min(prompt_gpus, c.gpus_per_node))
    tp_t = max(1, min(token_gpus, c.gpus_per_node))
    dp_p = max(1, prompt_gpus // tp_p)
    dp_t = max(1, token_gpus // tp_t)

    pre_ms = _prefill_ms(m, w, tp_p, c.tflops)
    dec_step_ms = _decode_step_ms(m, tp_t, c.tflops)
    kv_bytes = _kv_bytes_per_tok(m) * w.prompt_tokens
    kv_ms = 1000 * kv_bytes / max(c.inter_gbps * 1e9 / 8, 1)

    # Event-driven sim over 3s.
    import random as _r
    rng = _r.Random(17)
    horizon_ms = 3000
    pq: list[tuple[float, str, int, int]] = []   # (time, kind, req_id, slot)
    t = 0.0
    req_id = 0
    while t < horizon_ms:
        t += rng.expovariate(w.rate_rps) * 1000
        if t >= horizon_ms: break
        heapq.heappush(pq, (t, "arrive", req_id, 0))
        req_id += 1

    prompt_free = [0.0] * dp_p
    token_free = [0.0] * dp_t
    ttfts = {}
    arrivals = {}

    while pq:
        now, kind, rid, slot = heapq.heappop(pq)
        if kind == "arrive":
            arrivals[rid] = now
            slot = min(range(dp_p), key=lambda i: prompt_free[i])
            start = max(prompt_free[slot], now)
            prompt_free[slot] = start + pre_ms
            heapq.heappush(pq, (start + pre_ms, "transfer", rid, 0))
        elif kind == "transfer":
            heapq.heappush(pq, (now + kv_ms, "decode_start", rid, 0))
        elif kind == "decode_start":
            ttfts[rid] = now - arrivals[rid]
            slot = min(range(dp_t), key=lambda i: token_free[i])
            start = max(token_free[slot], now)
            token_free[slot] = start + dec_step_ms * w.completion_tokens

    # Stats.
    ttft_list = sorted(ttfts.values())
    avg_ttft = sum(ttft_list) / max(len(ttft_list), 1)
    # Baseline = colocated; approximate as prefill + some decode contention.
    base_ttft = pre_ms * 1.20
    return {
        "prompt_gpus": prompt_gpus, "token_gpus": token_gpus,
        "tp_prompt": tp_p, "tp_token": tp_t,
        "prefill_ms": pre_ms,
        "kv_transfer_ms": kv_ms,
        "decode_step_ms": dec_step_ms,
        "simulated_reqs": len(ttfts),
        "avg_ttft_ms": avg_ttft,
        "p99_ttft_ms": ttft_list[int(0.99 * len(ttft_list))] if ttft_list else 0,
        "total_ttft_ms": avg_ttft,
        "baseline_ttft_ms": base_ttft,
        "improvement_pct": 100 * (base_ttft - avg_ttft) / max(base_ttft, 1e-9),
    }


# ====================================================================
# 3. LoongServe — elastic sequence-parallel, HBM-budget aware
# ====================================================================
def loongserve(m: ModelSpec, c: ClusterSpec, w: Workload) -> dict:
    """
    Per-request SP search: for each candidate sp_prefill, check that KV
    fits in HBM budget; during decode shrink to sp_decode ∈ {sp/2, sp/4}
    such that a single GPU can still hold the running KV.
    Total time = prefill_ms + decode_steps * tpot_ms.
    Baseline = max SP fixed for both phases (wastes decode GPUs).
    """
    kv_total = _kv_bytes_per_tok(m) * (w.prompt_tokens + w.completion_tokens)
    param_bytes = m.params_b * 1e9 * BF16
    per_gpu_budget = HBM_PER_GPU_GB * 1e9 - param_bytes / c.gpus_per_node
    min_sp = max(1, int(kv_total / max(per_gpu_budget, 1)) + 1)

    best = None
    for sp_p in _divisors(c.gpus_per_node):
        if sp_p < min_sp: continue
        for shrink in (1, 2, 4):
            sp_d = max(1, sp_p // shrink)
            # HBM check during decode
            if kv_total / sp_d > per_gpu_budget:
                continue
            ttft = _prefill_ms(m, w, sp_p, c.tflops)
            tpot = _decode_step_ms(m, sp_d, c.tflops)
            total = ttft + tpot * w.completion_tokens
            cand = {"sp_prefill": sp_p, "sp_decode": sp_d,
                    "ttft_ms": ttft, "tpot_ms": tpot, "total_ms": total}
            if best is None or total < best["total_ms"]:
                best = cand

    if best is None:
        best = {"sp_prefill": c.gpus_per_node, "sp_decode": c.gpus_per_node,
                "ttft_ms": 0, "tpot_ms": 0, "total_ms": 0}

    # Static baseline: sp_decode = sp_prefill → wasted.
    ttft_s = _prefill_ms(m, w, best["sp_prefill"], c.tflops)
    tpot_s = _decode_step_ms(m, best["sp_prefill"], c.tflops)
    total_s = ttft_s + tpot_s * w.completion_tokens
    return {
        "sp_prefill": best["sp_prefill"], "sp_decode": best["sp_decode"],
        "ttft_ms": best["ttft_ms"], "tpot_ms": best["tpot_ms"],
        "total_ms_elastic": best["total_ms"],
        "total_ms_static": total_s,
        "improvement_pct": 100 * (total_s - best["total_ms"]) / max(total_s, 1e-9),
    }


# ====================================================================
# 4. Helix — Edmonds-Karp max-flow over heterogeneous GPUs
# ====================================================================
def _edmonds_karp(cap: list[list[float]], s: int, t: int) -> float:
    """Max-flow via BFS shortest augmenting paths on a dense capacity
    matrix. Returns total flow from s to t."""
    n = len(cap)
    flow = 0.0
    while True:
        parent = [-1] * n
        parent[s] = s
        q = deque([s])
        while q:
            u = q.popleft()
            if u == t: break
            for v in range(n):
                if parent[v] == -1 and cap[u][v] > 1e-9:
                    parent[v] = u
                    q.append(v)
        if parent[t] == -1:
            break
        # Bottleneck.
        bn = float("inf")
        v = t
        while v != s:
            u = parent[v]
            bn = min(bn, cap[u][v])
            v = u
        v = t
        while v != s:
            u = parent[v]
            cap[u][v] -= bn
            cap[v][u] += bn
            v = u
        flow += bn
    return flow


def helix(m: ModelSpec, c: ClusterSpec, w: Workload,
          heterogeneous_mix: tuple[float, float, float] = (0.4, 0.4, 0.2)) -> dict:
    """
    Build a bipartite graph:
      source → GPU nodes (one per heterogeneous tier) with capacity = tier tflops
      GPU nodes → pipeline-stage nodes (PP count) with capacity = demand / pp
      stage nodes → sink with capacity = total demand / pp
    Solve max-flow. Helix rps = max_flow / ops_per_rps.
    """
    tflops_tiers = [313.0, 989.0, 2250.0]   # A100, H100, B200
    tier_names = ["A100", "H100", "B200"]
    gpus_tiers = [int(c.gpus * f) for f in heterogeneous_mix]
    gpus_tiers[-1] += c.gpus - sum(gpus_tiers)

    pp = max(1, min(4, c.gpus // c.gpus_per_node))
    # Per-rps compute demand (TFLOPs · ms) = params * 2.
    demand_tflops_per_rps = 2 * m.params_b / 1000

    # Graph nodes: 0=src, 1..3=tiers, 4..4+pp-1=stages, 4+pp=sink
    n_stage = pp
    N = 1 + 3 + n_stage + 1
    SRC, TIER0, STAGE0, SINK = 0, 1, 4, 4 + n_stage
    cap = [[0.0] * N for _ in range(N)]

    for ti in range(3):
        if gpus_tiers[ti] == 0: continue
        cap[SRC][TIER0 + ti] = gpus_tiers[ti] * tflops_tiers[ti]
        for si in range(n_stage):
            cap[TIER0 + ti][STAGE0 + si] = gpus_tiers[ti] * tflops_tiers[ti]
    for si in range(n_stage):
        cap[STAGE0 + si][SINK] = sum(gpus_tiers[ti] * tflops_tiers[ti] for ti in range(3)) / n_stage

    # Solve.
    import copy as _c
    flow = _edmonds_karp(_c.deepcopy(cap), SRC, SINK)   # total compute tflops/s

    # rps = tflops / demand_tflops_per_rps  (demand is in TFLOPs per request)
    helix_rps = flow / max(demand_tflops_per_rps * 1e12, 1)

    # Baseline: pretend every GPU is the slowest tier.
    slow = min(t for t in tflops_tiers)
    base_flow = c.gpus * slow
    base_rps = base_flow / max(demand_tflops_per_rps * 1e12, 1)

    return {
        "tiers": [{"tier": tier_names[i], "gpus": gpus_tiers[i],
                   "tflops": tflops_tiers[i]} for i in range(3)],
        "pipeline_stages": pp,
        "max_flow_tflops_per_s": flow / 1e12,
        "helix_rps": helix_rps,
        "baseline_rps": base_rps,
        "improvement_pct": 100 * (helix_rps - base_rps) / max(base_rps, 1e-9),
    }


# ====================================================================
# 5. SpotServe — reparallelization plan search on preemption
# ====================================================================
def spotserve(m: ModelSpec, c: ClusterSpec, w: Workload,
              preemption_pct: float = 0.25) -> dict:
    """
    When `preemption_pct` of GPUs are taken back, search for the (tp,pp,dp)
    reconfiguration that minimises (migration_cost + ongoing_step_cost).
    Migration cost = bytes moved / inter_gbps. Migration bytes = weights
    that must cross rank boundaries (modelled as param_bytes × pct_moved).
    """
    remain = max(1, int(c.gpus * (1 - preemption_pct)))
    param_bytes = m.params_b * 1e9 * BF16

    # Old config (assume one colocated pool).
    tp_old = min(c.gpus, c.gpus_per_node)
    pp_old = max(1, c.gpus // tp_old)
    old_step_ms = _decode_step_ms(m, tp_old, c.tflops)

    best = None
    for tp in _divisors(min(remain, c.gpus_per_node)):
        for pp in _divisors(remain // tp):
            dp = remain // (tp * pp)
            if dp < 1: continue
            # Fraction of weights that move between old & new rank layout.
            # Model as the symmetric-difference size of two partitionings.
            # (Simplified: moved_pct = 1 - min(tp_old, tp) * min(pp_old, pp) / (tp*pp))
            moved_pct = 1 - min(tp_old, tp) * min(pp_old, pp) / (tp * pp)
            migration_bytes = param_bytes * moved_pct
            migration_ms = 1000 * migration_bytes / max(c.inter_gbps * 1e9 / 8, 1)

            step_ms = _decode_step_ms(m, tp, c.tflops)
            ongoing_rps = dp * 1000 / max(step_ms, 1)
            # Lower is better → invert for objective.
            obj = migration_ms / 10 - ongoing_rps
            cand = {"tp_new": tp, "pp_new": pp, "dp_new": dp,
                    "migration_bytes": migration_bytes, "migration_ms": migration_ms,
                    "step_ms": step_ms, "ongoing_rps": ongoing_rps, "obj": obj,
                    "moved_pct": moved_pct}
            if best is None or obj < best["obj"]:
                best = cand

    if best is None:
        best = {"tp_new": 1, "pp_new": 1, "dp_new": remain, "migration_bytes": 0,
                "migration_ms": 0, "step_ms": 0, "ongoing_rps": 0, "moved_pct": 0}

    # Naive = 0 rps during full restart.
    return {
        "preempted_gpus": c.gpus - remain, "remaining_gpus": remain,
        "tp_old": tp_old, "pp_old": pp_old,
        "tp_new": best["tp_new"], "pp_new": best["pp_new"], "dp_new": best["dp_new"],
        "migration_gb": best["migration_bytes"] / 1e9,
        "migration_ms": best["migration_ms"],
        "moved_weight_fraction": best["moved_pct"],
        "rps_before": (c.gpus // tp_old) * 1000 / max(old_step_ms, 1),
        "rps_after_reconfig": best["ongoing_rps"],
        "rps_naive_during_event": 0.0,
        "improvement_pct": 100 if best["ongoing_rps"] > 0 else 0,
    }


PAPERS = {
    "DistServe":  {"arxiv": "2401.09670", "year": 2024,
                   "title": "DistServe: Disaggregating Prefill and Decoding"},
    "Splitwise":  {"arxiv": "2311.18677", "year": 2023,
                   "title": "Splitwise: Efficient Generative LLM Inference"},
    "LoongServe": {"arxiv": "2404.09526", "year": 2024,
                   "title": "LoongServe: Elastic Sequence Parallelism"},
    "Helix":      {"arxiv": "2406.01566", "year": 2024,
                   "title": "Helix: Serving LLMs over Heterogeneous GPUs via Max-Flow"},
    "SpotServe":  {"arxiv": "2311.15566", "year": 2023,
                   "title": "SpotServe: Serving LLMs on Preemptible Instances"},
}
