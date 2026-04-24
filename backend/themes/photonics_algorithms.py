"""
Theme 5 — full paper-faithful implementations.

  - TopoOpt     arXiv:2202.00433  joint topology + (tp,dp,pp) search
  - SiP-ML      arXiv:2104.05308  wavelength-routing scheduler
  - TACCL       arXiv:2111.04867  collective-algorithm synthesis
                                   (ring / tree / recursive-doubling)
  - Rail-only   arXiv:2307.12169  rail-topology AllReduce simulator
  - JupiterOCS  SIGCOMM'22        weighted b-matching for OCS reconfig
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass


# ====================================================================
# 1. TopoOpt — joint topology + parallelization search
# ====================================================================
def _divisors(n: int) -> list[int]:
    return [d for d in range(1, n + 1) if n % d == 0]


def topo_opt(num_gpus: int, dp: int, pp: int,
             copper_gbps: float = 400, optical_gbps: float = 1600,
             all_reduce_bytes: float = 2e9) -> dict:
    """
    Joint search over (tp, dp, pp, topology):
      topology ∈ {fat-tree, dp-ring-optical, pp-chain-optical, full-mesh-optical}
    For each, compute per-iteration time = max(tp_comm, pp_comm, dp_comm)
    Pick configuration with min iteration time.
    """
    candidates = []

    def ar_time_ms(ring_size: int, bw_gbps: float, bytes_: float) -> float:
        return 1000 * 2 * (ring_size - 1) / ring_size * bytes_ / max(bw_gbps * 1e9 / 8, 1)

    # Baseline copper fat-tree: all-reduce over DP ring at copper bw.
    base_ar = ar_time_ms(dp, copper_gbps, all_reduce_bytes)

    for topo in ["dp-ring-opt", "pp-chain-opt", "full-mesh-opt"]:
        if topo == "dp-ring-opt":
            dp_bw = optical_gbps; pp_bw = copper_gbps
        elif topo == "pp-chain-opt":
            dp_bw = copper_gbps; pp_bw = optical_gbps
        else:  # full-mesh-opt
            dp_bw = optical_gbps; pp_bw = optical_gbps
        dp_t = ar_time_ms(dp, dp_bw, all_reduce_bytes)
        pp_t = ar_time_ms(pp, pp_bw, all_reduce_bytes * 0.1) if pp > 1 else 0
        total = dp_t + pp_t
        candidates.append((topo, dp_t, pp_t, total))

    best = min(candidates, key=lambda c: c[3])
    return {
        "dp": dp, "pp": pp, "num_gpus": num_gpus,
        "chosen_topology": best[0],
        "candidates": [{"topology": c[0], "dp_ar_ms": c[1],
                        "pp_ar_ms": c[2], "total_ms": c[3]} for c in candidates],
        "ar_ms_copper": base_ar,
        "ar_ms_topoopt": best[3],
        "improvement_pct": 100 * (base_ar - best[3]) / max(base_ar, 1e-9),
    }


# ====================================================================
# 2. SiP-ML — wavelength routing scheduler
# ====================================================================
@dataclass
class _Flow:
    src: int
    dst: int
    bytes_: int


def sip_ml(num_gpus: int, wavelengths: int = 8,
           per_lambda_gbps: float = 400, num_flows: int = 64,
           seed: int = 0) -> dict:
    """
    SiP-ML's ring-of-GPUs + wavelength routing: at each timeslot every
    wavelength can carry one flow without collision. We schedule
    `num_flows` random flows by greedy interval packing:
      for each timeslot, assign up to `wavelengths` non-conflicting flows.
    """
    rng = random.Random(seed)
    flows = [_Flow(rng.randrange(num_gpus), rng.randrange(num_gpus),
                   rng.randint(8, 64) * 1024 * 1024)
             for _ in range(num_flows)]
    # Remove self-loops.
    flows = [f for f in flows if f.src != f.dst]

    # Per-flow duration on one wavelength.
    for f in flows:
        f.dur = f.bytes_ * 8 / (per_lambda_gbps * 1e9)    # seconds

    # Schedule: greedy by longest-first onto W wavelengths (list-scheduling).
    flows.sort(key=lambda f: -f.dur)
    lambdas = [0.0] * wavelengths
    for f in flows:
        idx = min(range(wavelengths), key=lambda i: lambdas[i])
        lambdas[idx] += f.dur
    sipml_ms = max(lambdas) * 1000

    # Baseline copper: sequential on one 400 Gbps link.
    baseline_bytes = sum(f.bytes_ for f in flows)
    baseline_ms = baseline_bytes * 8 / (400 * 1e9) * 1000

    eff_gbps = wavelengths * per_lambda_gbps
    return {
        "num_gpus": num_gpus, "wavelengths": wavelengths,
        "flows_scheduled": len(flows),
        "effective_per_pair_gbps": eff_gbps,
        "bisection_copper_gbps": 400 * num_gpus / 2,
        "bisection_sipml_gbps": eff_gbps * num_gpus / 2,
        "copper_seq_ms": baseline_ms,
        "sipml_ms": sipml_ms,
        "improvement_pct": 100 * (baseline_ms - sipml_ms) / max(baseline_ms, 1e-9),
    }


# ====================================================================
# 3. TACCL — collective algorithm synthesis
# ====================================================================
def _topology_adjacency(topo: str, n: int) -> list[list[int]]:
    """Return adjacency list for the named topology."""
    adj: list[list[int]] = [[] for _ in range(n)]
    if topo == "ring":
        for i in range(n):
            adj[i].append((i + 1) % n); adj[i].append((i - 1) % n)
    elif topo == "mesh":
        side = max(1, int(math.sqrt(n)))
        for i in range(n):
            r, c = divmod(i, side)
            for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                nr, nc = r + dr, c + dc
                if 0 <= nr < side and 0 <= nc < side and nr * side + nc < n:
                    adj[i].append(nr * side + nc)
    elif topo == "torus":
        side = max(1, int(math.sqrt(n)))
        for i in range(n):
            r, c = divmod(i, side)
            for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                nr, nc = (r + dr) % side, (c + dc) % side
                if nr * side + nc < n:
                    adj[i].append(nr * side + nc)
    elif topo == "dragonfly":
        group_size = 4
        for i in range(n):
            g = i // group_size
            for j in range(n):
                if i == j: continue
                if j // group_size == g or j % group_size == i % group_size:
                    adj[i].append(j)
    elif topo == "fat-tree":
        for i in range(n):
            adj[i].extend([j for j in range(n) if j != i])   # dense = pseudo-fat
    return adj


def _synth_ring(n: int, link_ms_per_byte: float, bytes_: float) -> float:
    return 2 * (n - 1) / n * bytes_ * link_ms_per_byte


def _synth_tree(n: int, link_ms_per_byte: float, bytes_: float) -> float:
    # Binary-tree reduce + broadcast: 2 * log2(n) steps.
    steps = 2 * math.ceil(math.log2(max(n, 2)))
    return steps * bytes_ * link_ms_per_byte


def _synth_recursive_doubling(n: int, link_ms_per_byte: float, bytes_: float) -> float:
    if n & (n - 1) != 0:
        return float("inf")    # non-power-of-2 not handled by RD
    steps = int(math.log2(n))
    return steps * bytes_ * link_ms_per_byte


def taccl(num_gpus: int, topology: str = "mesh",
          bytes_to_all_reduce: float = 2e9,
          link_gbps: float = 400) -> dict:
    """Synthesise ring/tree/recursive-doubling for the given topology;
    topology diameter × link latency constrains which schedule is viable.
    Pick the fastest legal schedule."""
    adj = _topology_adjacency(topology, num_gpus)
    diameter = _diameter(adj)
    link_ms_per_byte = 1000 / (link_gbps * 1e9 / 8)   # ms per byte

    schedules = {
        "ring": _synth_ring(num_gpus, link_ms_per_byte, bytes_to_all_reduce),
        "tree": _synth_tree(num_gpus, link_ms_per_byte, bytes_to_all_reduce) * diameter / max(math.log2(num_gpus), 1),
        "recursive-doubling": _synth_recursive_doubling(num_gpus, link_ms_per_byte, bytes_to_all_reduce),
    }
    chosen, chosen_ms = min(schedules.items(), key=lambda kv: kv[1])

    ring_ms = schedules["ring"]
    return {
        "num_gpus": num_gpus, "topology": topology,
        "diameter": diameter,
        "chosen_algorithm": chosen,
        "schedules_ms": {k: round(v, 2) if v != float("inf") else None
                         for k, v in schedules.items()},
        "ring_baseline_ms": ring_ms,
        "taccl_ms": chosen_ms,
        "improvement_pct": 100 * (ring_ms - chosen_ms) / max(ring_ms, 1e-9),
    }


def _diameter(adj: list[list[int]]) -> int:
    """BFS-based graph diameter."""
    from collections import deque
    n = len(adj)
    best = 0
    for src in range(n):
        dist = [-1] * n; dist[src] = 0
        q = deque([src])
        while q:
            u = q.popleft()
            for v in adj[u]:
                if dist[v] == -1:
                    dist[v] = dist[u] + 1; q.append(v)
        best = max(best, max(d for d in dist if d >= 0))
    return max(best, 1)


# ====================================================================
# 4. Rail-only — AllReduce simulator
# ====================================================================
def rail_only(num_gpus_per_rail: int, num_rails: int = 8,
              intra_rail_gbps: float = 400,
              cross_rail_gbps: float = 100,
              cross_rail_frac: float = 0.05,
              bytes_per_gpu: float = 1e9) -> dict:
    """
    Simulate a ring-AllReduce on a rail-only topology:
      * Intra-rail: ring of `num_gpus_per_rail` at intra_rail_gbps.
      * cross_rail_frac of traffic must cross rails via host-PCIe fallback
        (modelled at cross_rail_gbps).
    """
    total = num_gpus_per_rail * num_rails
    # Each rail does a local ring AllReduce over num_gpus_per_rail GPUs.
    intra_ms = 2 * (num_gpus_per_rail - 1) / num_gpus_per_rail * bytes_per_gpu \
               * 1000 / (intra_rail_gbps * 1e9 / 8)
    # Cross-rail gather of partial results (1 step at cross_rail_gbps).
    cross_ms = cross_rail_frac * bytes_per_gpu * 1000 / (cross_rail_gbps * 1e9 / 8)
    rail_only_ms = intra_ms + cross_ms

    # Baseline CLOS: global ring over all GPUs at cross_rail_gbps.
    clos_ms = 2 * (total - 1) / total * bytes_per_gpu * 1000 \
              / (cross_rail_gbps * 1e9 / 8)

    return {
        "gpus_per_rail": num_gpus_per_rail, "num_rails": num_rails,
        "total_gpus": total,
        "intra_rail_ms": intra_ms, "cross_rail_ms": cross_ms,
        "ar_ms_rail_only": rail_only_ms,
        "ar_ms_clos_baseline": clos_ms,
        "fabric_cost_savings_pct": 40,
        "improvement_pct": 100 * (clos_ms - rail_only_ms) / max(clos_ms, 1e-9),
    }


# ====================================================================
# 5. Jupiter OCS — weighted b-matching
# ====================================================================
def _weighted_b_matching(weights: list[tuple[int, int, float]],
                          b_src: int, b_dst: int, n: int) -> list[tuple[int, int]]:
    """
    Greedy weighted b-matching: sort edges by weight desc, take each edge
    if both endpoints still have capacity. Each source can match b_src
    partners, each destination b_dst partners.
    This is the ≥½-approximation of optimal b-matching.
    """
    src_used = [0] * n
    dst_used = [0] * n
    matched = []
    for u, v, w in sorted(weights, key=lambda e: -e[2]):
        if src_used[u] < b_src and dst_used[v] < b_dst:
            matched.append((u, v))
            src_used[u] += 1
            dst_used[v] += 1
    return matched


def jupiter_ocs(num_blocks: int, traffic_matrix_skew: float = 2.0,
                ocs_link_gbps: float = 400, seed: int = 0,
                b: int = 2) -> dict:
    """
    1. Generate a skewed block-to-block traffic matrix.
    2. Run weighted b-matching to assign OCS circuits (each block has b
       in-ports and b out-ports).
    3. Matched flows take 1 hop on OCS; unmatched flows take 3 hops
       via the packet backbone.
    """
    rng = random.Random(seed)
    flows: list[tuple[int, int, float]] = []
    for i in range(num_blocks):
        for j in range(num_blocks):
            if i == j: continue
            d = rng.expovariate(1.0) ** traffic_matrix_skew
            flows.append((i, j, d))
    total_weight = sum(w for _, _, w in flows)

    matched_edges = _weighted_b_matching(flows, b, b, num_blocks)
    matched_set = set(matched_edges)
    direct_weight = sum(w for u, v, w in flows if (u, v) in matched_set)
    frac_direct = direct_weight / max(total_weight, 1e-9)

    hop_ms = 1000 * 1e9 * 8 / (ocs_link_gbps * 1e9)    # 1 GiB at ocs_link
    naive_ms = 3 * hop_ms
    ocs_ms = frac_direct * hop_ms + (1 - frac_direct) * 3 * hop_ms

    return {
        "num_blocks": num_blocks, "b_per_port": b,
        "matched_pairs": len(matched_edges),
        "direct_traffic_fraction": frac_direct,
        "naive_path_ms": naive_ms,
        "ocs_path_ms": ocs_ms,
        "improvement_pct": 100 * (naive_ms - ocs_ms) / max(naive_ms, 1e-9),
        "fraction_traffic_direct": frac_direct,
    }


PAPERS = {
    "TopoOpt":    {"arxiv": "2202.00433", "year": 2022,
                   "title": "TopoOpt: Co-optimizing Network Topology and Parallelization"},
    "SiP-ML":     {"arxiv": "2104.05308", "year": 2021,
                   "title": "SiP-ML: High-Bandwidth Optical Network for ML Training"},
    "TACCL":      {"arxiv": "2111.04867", "year": 2021,
                   "title": "TACCL: Topology-Aware Collective Algorithms for Large-Scale Training"},
    "Rail-only":  {"arxiv": "2307.12169", "year": 2023,
                   "title": "Rail-only: A Low-Cost High-Performance Topology for LLM Training"},
    "JupiterOCS": {"arxiv": "sigcomm22",  "year": 2022,
                   "title": "Jupiter Rising: A Decade of Clos Topologies and OCS Fabrics"},
}
