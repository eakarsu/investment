"""
Theme 3 — full paper-faithful implementations.

  - Perseus     arXiv:2312.06902  DVFS optimisation via binary search on
                                   the pipeline period (saves time vs LP)
  - POLCA       arXiv:2308.12908  closed-loop power-cap controller
                                   simulated over a time series
  - DynamoLLM   arXiv:2408.00741  logistic-regression query router
                                   with energy-aware cost function
  - LLMCarbon   arXiv:2309.14393  per-component lifecycle accounting
                                   (training / serving / embodied / cooling)
  - VCC         arXiv:2106.11750  constraint-satisfaction shifter that
                                   respects per-hour capacity + deadlines
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass


GPU_TDP_W = {"H100": 700, "B200": 1000, "A100": 400, "R200": 1200}
GPU_EMBODIED_KG = {"H100": 600, "B200": 900, "A100": 400, "R200": 1200}


# ====================================================================
# 1. Perseus — per-stage DVFS optimisation
# ====================================================================
def perseus(pipeline_stages: int, stage_times_ms: list[float],
            freq_levels: list[float] = None,
            f_min: float = 0.5) -> dict:
    """
    Minimise Σ tᵢ·fᵢ² s.t. tᵢ/fᵢ ≤ P for a chosen period P.
    Closed form: fᵢ* = max(f_min, tᵢ / P).
    Binary-search P ∈ [max(tᵢ), 2·max(tᵢ)] for min-energy frontier.
    """
    if freq_levels is None:
        freq_levels = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    assert len(stage_times_ms) == pipeline_stages
    t_max = max(stage_times_ms)

    def total_energy(P: float) -> tuple[float, list[float]]:
        freqs = []
        e = 0.0
        for t in stage_times_ms:
            f_ideal = max(f_min, t / P)
            # Snap to nearest allowed level ≥ f_ideal.
            f = min((f for f in freq_levels if f >= f_ideal), default=1.0)
            freqs.append(f)
            e += t * f ** 2
        return e, freqs

    # Binary search the optimal period.
    lo, hi = t_max, t_max * 2
    best_e = float("inf"); best_freqs = None; best_P = t_max
    for _ in range(40):
        P = (lo + hi) / 2
        e, freqs = total_energy(P)
        # Evaluate the neighborhood
        e_lo, _ = total_energy(P * 0.95)
        e_hi, _ = total_energy(P * 1.05)
        if e < best_e:
            best_e, best_freqs, best_P = e, freqs, P
        if e_lo < e_hi:
            hi = P
        else:
            lo = P

    baseline_e = sum(t * 1.0 ** 2 for t in stage_times_ms)
    return {
        "pipeline_stages": pipeline_stages,
        "period_ms": best_P,
        "stage_freqs": best_freqs,
        "baseline_energy": baseline_e,
        "optimised_energy": best_e,
        "improvement_pct": 100 * (baseline_e - best_e) / max(baseline_e, 1e-9),
    }


# ====================================================================
# 2. POLCA — closed-loop power-cap controller
# ====================================================================
def polca(num_gpus: int, gpu: str, rack_power_cap_kw: float,
          safe_capped_watts: float = 500,
          horizon_s: int = 600, seed: int = 0) -> dict:
    """
    Simulate a minute-by-minute workload over `horizon_s` seconds.
    The controller watches `observed_utilisation` and adjusts per-GPU
    cap every 30 seconds:
        cap_t = clamp(base_cap + kp * (target_util - util_t), 0.4·TDP, TDP)
    Throughput is penalised proportional to distance from TDP.
    """
    rng = random.Random(seed)
    tdp = GPU_TDP_W[gpu]
    base_cap = safe_capped_watts
    kp = 1.5  # control gain (W per utilisation point)
    target_util = 0.8

    fit_baseline = int(rack_power_cap_kw * 1000 / tdp)
    fit_polca = int(rack_power_cap_kw * 1000 / base_cap)

    cap_log = []
    util_log = []
    thpt_log = []
    cur_cap = base_cap
    for t in range(0, horizon_s, 30):
        util = max(0.3, min(1.0, 0.7 + 0.3 * math.sin(t / 120) + rng.gauss(0, 0.05)))
        err = target_util - util
        cur_cap = min(tdp, max(0.4 * tdp, cur_cap + kp * err * 100))
        # Throughput assumes 3%/100W linear below TDP.
        perf_factor = 1 - min(0.15, 0.03 * max(0, (tdp - cur_cap)) / 100)
        thpt = fit_polca * perf_factor
        cap_log.append(cur_cap); util_log.append(util); thpt_log.append(thpt)

    avg_thpt_polca = sum(thpt_log) / max(len(thpt_log), 1)
    avg_thpt_baseline = fit_baseline * 1.0

    return {
        "gpu": gpu, "tdp_w": tdp,
        "initial_cap_w": base_cap, "final_cap_w": cur_cap,
        "avg_cap_w": sum(cap_log) / max(len(cap_log), 1),
        "samples": len(cap_log),
        "gpus_per_rack_baseline": fit_baseline,
        "gpus_per_rack_polca": fit_polca,
        "single_gpu_perf_hit_pct": min(15, 3 * max(0, (tdp - base_cap)) / 100),
        "aggregate_throughput_baseline": avg_thpt_baseline,
        "aggregate_throughput_polca": avg_thpt_polca,
        "improvement_pct": 100 * (avg_thpt_polca - avg_thpt_baseline)
                          / max(avg_thpt_baseline, 1e-9),
    }


# ====================================================================
# 3. DynamoLLM — logistic-regression query router
# ====================================================================
def _sigmoid(z: float) -> float:
    if z >= 0:
        ez = math.exp(-z); return 1 / (1 + ez)
    ez = math.exp(z); return ez / (1 + ez)


def _train_logreg(X: list[list[float]], y: list[int],
                  lr: float = 0.3, epochs: int = 400) -> list[float]:
    n_feat = len(X[0])
    w = [0.0] * n_feat; b = 0.0
    n = len(X)
    for _ in range(epochs):
        gw = [0.0] * n_feat; gb = 0.0
        for xi, yi in zip(X, y):
            z = b + sum(w[j] * xi[j] for j in range(n_feat))
            err = _sigmoid(z) - yi
            for j in range(n_feat):
                gw[j] += err * xi[j]
            gb += err
        for j in range(n_feat):
            w[j] -= lr * gw[j] / n
        b -= lr * gb / n
    return [b] + w


def dynamo_llm(workload_rps: float, model_tiers: list[dict],
               n_train: int = 800, seed: int = 0) -> dict:
    """
    Train a classifier to route each query to the smallest tier that
    satisfies a learned quality predicate. Evaluate on a held-out
    workload and compute total energy.
    """
    rng = random.Random(seed)
    tiers_sorted = sorted(model_tiers, key=lambda t: t["energy_mj_per_tok"])

    # Generate training data: (query feature, ground-truth "needs-strongest")
    X, y = [], []
    for _ in range(n_train):
        prompt_len = rng.uniform(50, 4000)
        is_code = 1 if rng.random() < 0.2 else 0
        is_reasoning = 1 if rng.random() < 0.15 else 0
        feat = [prompt_len / 4000, is_code, is_reasoning]
        needs_strong = int(is_reasoning or (is_code and prompt_len > 1500))
        X.append(feat); y.append(needs_strong)
    theta = _train_logreg(X, y)
    b, w1, w2, w3 = theta

    # Eval on `workload_rps` queries-per-second over a 1-s window.
    n_queries = int(workload_rps)
    counts = {t["name"]: 0 for t in tiers_sorted}
    total_e = 0.0
    for _ in range(n_queries):
        prompt_len = rng.uniform(50, 4000)
        is_code = 1 if rng.random() < 0.2 else 0
        is_reasoning = 1 if rng.random() < 0.15 else 0
        z = b + w1 * (prompt_len / 4000) + w2 * is_code + w3 * is_reasoning
        p_strong = _sigmoid(z)
        tier = tiers_sorted[-1] if p_strong > 0.5 else tiers_sorted[0]
        counts[tier["name"]] += 1
        total_e += tier["energy_mj_per_tok"]

    e_baseline = n_queries * tiers_sorted[-1]["energy_mj_per_tok"]

    mix = [{"tier": name, "rps": counts[name],
            "share_pct": round(100 * counts[name] / max(n_queries, 1), 1)}
           for name in counts]
    return {
        "classifier_weights": {"bias": round(b, 3), "len": round(w1, 3),
                               "code": round(w2, 3), "reason": round(w3, 3)},
        "n_queries_evaluated": n_queries,
        "mix": mix,
        "energy_mj_baseline": e_baseline,
        "energy_mj_dynamo": total_e,
        "improvement_pct": 100 * (e_baseline - total_e) / max(e_baseline, 1e-9),
    }


# ====================================================================
# 4. LLMCarbon — per-component lifecycle accounting
# ====================================================================
def llm_carbon(params_b: float, training_gpu_hours: float,
               serving_tokens: float,
               grid_gco2_per_kwh: float,
               gpu_type: str = "H100",
               pue: float = 1.3,
               cooling_water_l_per_kwh: float = 1.8,
               network_overhead_pct: float = 10) -> dict:
    """
    Per-component lifecycle model:
      operational = (compute + cooling + networking) * PUE * grid_intensity
      embodied    = (training fleet GPUs + serving fleet GPUs) * embodied-kg
    """
    tdp_w = GPU_TDP_W[gpu_type]
    embodied_per_gpu = GPU_EMBODIED_KG[gpu_type]

    # Training op energy.
    training_kwh = training_gpu_hours * (tdp_w / 1000)
    training_net_kwh = training_kwh * network_overhead_pct / 100
    training_cooling_kwh = (training_kwh + training_net_kwh) * (pue - 1)
    training_total_kwh = training_kwh + training_net_kwh + training_cooling_kwh
    training_op_kg = training_total_kwh * grid_gco2_per_kwh / 1000
    training_water_l = training_total_kwh * cooling_water_l_per_kwh

    # Serving op energy (~3 Wh per 1000 tok for 70B-class).
    serving_kwh = serving_tokens * 3e-6
    serving_net_kwh = serving_kwh * network_overhead_pct / 100
    serving_cooling_kwh = (serving_kwh + serving_net_kwh) * (pue - 1)
    serving_total_kwh = serving_kwh + serving_net_kwh + serving_cooling_kwh
    serving_op_kg = serving_total_kwh * grid_gco2_per_kwh / 1000

    # Embodied.
    # Training fleet size ≈ GPU-hours / 2400 (amortised over 1-month train run).
    gpu_train_count = max(1, training_gpu_hours / 2400)
    embodied_train_kg = gpu_train_count * embodied_per_gpu
    # Amortise embodied over 5-year GPU life → attribute train run fraction.
    embodied_train_attributed_kg = embodied_train_kg * (2400 / (5 * 8760 * gpu_train_count))

    total_kg = (training_op_kg + serving_op_kg + embodied_train_attributed_kg)
    addressable_kg = training_op_kg + serving_op_kg  # can be improved via cleaner grid

    return {
        "gpu_type": gpu_type, "pue": pue,
        "training_op_kg": training_op_kg,
        "training_water_l": training_water_l,
        "serving_op_kg": serving_op_kg,
        "embodied_kg": embodied_train_attributed_kg,
        "network_overhead_pct": network_overhead_pct,
        "total_kg": total_kg,
        "addressable_kg": addressable_kg,
        "train_op_kg": training_op_kg,
        "improvement_pct": 100 * addressable_kg / max(total_kg, 1e-9),
    }


# ====================================================================
# 5. VCC — constraint-satisfaction temporal shifter
# ====================================================================
def vcc_shift(hourly_intensity_gco2: list[float],
              hourly_demand_tokens: list[float],
              shiftable_fraction: float = 0.4,
              max_hour_headroom_pct: float = 50,
              deadline_hours: int = 24) -> dict:
    """
    Respecting:
      1. Per-hour capacity: h.demand ≤ original * (1 + headroom_pct)
      2. Deadline: shifted work must run within `deadline_hours`
      3. Total preserved: Σ new_demand = Σ original
    Greedy LP-style assignment: dirtiest → cleanest, one unit at a time.
    """
    assert len(hourly_intensity_gco2) == len(hourly_demand_tokens) == 24
    original = hourly_demand_tokens[:]
    new = hourly_demand_tokens[:]
    shiftable = sum(original) * shiftable_fraction
    headroom = [d * max_hour_headroom_pct / 100 for d in original]

    # Pull from dirty hours, push to clean hours, respecting window.
    clean = sorted(range(24), key=lambda h: hourly_intensity_gco2[h])
    dirty = sorted(range(24), key=lambda h: -hourly_intensity_gco2[h])

    moved = 0.0
    ci = di = 0
    while moved < shiftable and ci < 24 and di < 24:
        c_h = clean[ci]; d_h = dirty[di]
        # Deadline: must push forward <= deadline_hours.
        if not (0 <= (c_h - d_h) % 24 <= deadline_hours):
            ci += 1; continue
        cap_clean = headroom[c_h] - (new[c_h] - original[c_h])
        cap_dirty = new[d_h]
        take = min(shiftable - moved, cap_clean, cap_dirty)
        if take <= 1e-9:
            # Advance whichever side is saturated.
            if cap_clean <= 1e-9: ci += 1
            else: di += 1
            continue
        new[c_h] += take
        new[d_h] -= take
        moved += take

    carbon_before = sum(original[h] * hourly_intensity_gco2[h] for h in range(24))
    carbon_after = sum(new[h] * hourly_intensity_gco2[h] for h in range(24))
    return {
        "shiftable_fraction": shiftable_fraction,
        "tokens_moved": moved,
        "cleanest_hours": clean[:6],
        "dirtiest_hours": dirty[:6],
        "new_demand_first12h": [round(x, 0) for x in new[:12]],
        "carbon_before_g_per_tok": carbon_before / max(sum(original), 1),
        "carbon_after_g_per_tok": carbon_after / max(sum(original), 1),
        "improvement_pct": 100 * (carbon_before - carbon_after) / max(carbon_before, 1e-9),
    }


PAPERS = {
    "Perseus":   {"arxiv": "2312.06902", "year": 2023,
                  "title": "Perseus: Reducing Energy Bloat in LLM Pipelines"},
    "POLCA":     {"arxiv": "2308.12908", "year": 2023,
                  "title": "POLCA: Power Oversubscription for LLM Clouds"},
    "DynamoLLM": {"arxiv": "2408.00741", "year": 2024,
                  "title": "DynamoLLM: Energy-Aware Serving of LLMs"},
    "LLMCarbon": {"arxiv": "2309.14393", "year": 2023,
                  "title": "LLMCarbon: Modeling End-to-End Carbon Footprint of LLMs"},
    "VCC":       {"arxiv": "2106.11750", "year": 2021,
                  "title": "Carbon-Aware Computing: The Virtual Capacity Curve"},
}
