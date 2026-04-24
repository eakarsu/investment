"""
Seed ≥15 realistic rows per feature into Postgres so the web app is
interesting on first load. Idempotent: drops tables and recreates.

Run:
  python -m backend.seed
"""

from __future__ import annotations

import asyncio
import random
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

from .db import (
    HBMDecision, HBMAlgoRun, NetworkPlan, NetworkAlgoRun,
    EnergyRegion, EnergyDecision, EnergyAlgoRun,
    InferenceRun, InferenceAlgoRun,
    PhotonicScenario, PhotonicsAlgoRun,
    SessionLocal, init_db, drop_all,
)
from .themes.hbm import Cluster, admission, weights_gb, MODEL_SPECS, HBM_PER_GPU_GB
from .themes import hbm_algorithms as hbm_algos
from .themes import networking_algorithms as net_algos
from .themes import energy_algorithms as en_algos
from .themes import inference_algorithms as inf_algos
from .themes import photonics_algorithms as ph_algos
from .themes.networking import plan, TFLOPS_H100, TFLOPS_B200, TFLOPS_R200
from .themes.photonics import schedule


PER_FEATURE = 15

MODELS_HBM = list(MODEL_SPECS.keys())

CLUSTERS = [
    ("H100-DGX-8",   8,   8,  900, 400,  TFLOPS_H100),
    ("H100-pod-16", 16,   8,  900, 400,  TFLOPS_H100),
    ("H100-pod-64", 64,   8,  900, 400,  TFLOPS_H100),
    ("B200-NVL8",    8,   8, 1800, 800,  TFLOPS_B200),
    ("B200-NVL36",  36,   8, 1800, 800,  TFLOPS_B200),
    ("GB200-NVL72", 72,  72, 1800, 800,  TFLOPS_B200),
    ("Rubin-NVL72", 72,  72, 3600, 1600, TFLOPS_R200),
]

MODELS_NET = [
    # (name, params_b, layers, hidden)
    ("llama-3-8b",       8,  32,  4096),
    ("llama-3-70b",     70,  80,  8192),
    ("llama-3-405b",   405, 126, 16384),
    ("mixtral-8x22b",  141,  56,  6144),
    ("qwen-2-72b",      72,  80,  8192),
    ("gpt-oss-120b",   120,  96, 12288),
]

REGIONS = [
    # region, $/kWh, gCO2/kWh, PUE, latency_ms, gpus, grid_queue_mo
    ("us-east-1",     0.085,  380, 1.35,  40,  4000,  48),
    ("us-west-2",     0.068,  220, 1.20,  85,  3200,  36),
    ("us-central-1",  0.045,  650, 1.40,  65,  6000,  60),
    ("us-north-1",    0.035,   90, 1.15, 120,  1200,  24),  # hydro, small footprint
    ("eu-west-1",     0.110,  280, 1.22,  35,  2500,  12),
    ("eu-north-1",    0.055,   45, 1.18,  65,  1800,   6),  # nordic + cold → low PUE
    ("ap-south-1",    0.075,  720, 1.55, 160,  2200,  18),
    ("ap-northeast-1",0.130,  440, 1.30, 145,  1500,  12),
    ("me-central-1",  0.040,  480, 1.45, 170,   900,   6),
    ("sa-east-1",     0.095,  110, 1.30, 190,   600,  24),
]

MODELS_INF = ["gpt-4o", "gpt-4o-mini", "claude-sonnet", "claude-haiku",
              "llama-3-70b", "llama-3-8b", "mixtral-8x7b"]
PRICING_IN = {"gpt-4o": 2.5e-6, "gpt-4o-mini": 0.15e-6, "claude-sonnet": 3e-6,
              "claude-haiku": 0.8e-6, "llama-3-70b": 0.9e-6, "llama-3-8b": 0.2e-6,
              "mixtral-8x7b": 0.6e-6}
PRICING_OUT = {k: v * 4 for k, v in PRICING_IN.items()}


async def seed_hbm(n: int = PER_FEATURE) -> int:
    rng = random.Random(1)
    async with SessionLocal() as s:
        for _ in range(n):
            model = rng.choice(MODELS_HBM)
            num_gpus = rng.choice([1, 2, 4, 8])
            cluster = Cluster(
                total_hbm_gb=HBM_PER_GPU_GB * num_gpus,
                weights_gb=weights_gb(model),
            )
            cap = (cluster.total_hbm_gb - cluster.weights_gb) * 1024
            cluster.used_kv_mb = rng.uniform(0.3, 0.9) * cap
            cluster.admitted = [(uuid.uuid4().hex[:8], cluster.used_kv_mb / 5)] * 5

            prompt = rng.choice([128, 512, 1024, 2048, 4096, 8192, 16384, 32768])
            max_comp = rng.choice([64, 128, 256, 512, 1024])
            decision, kv, evicted, reason = admission(cluster, model, prompt, max_comp)
            s.add(HBMDecision(
                request_id=uuid.uuid4().hex[:12],
                model=model, prompt_tokens=prompt, max_completion=max_comp,
                kv_mb_projected=kv, hbm_pressure_pct=cluster.pressure_pct,
                decision=decision, evicted_count=evicted, reason=reason,
            ))
        await s.commit()
    return n


async def seed_hbm_algorithms(n: int = PER_FEATURE + 5) -> int:
    """
    Run each of the 5 KV-cache compression papers across a handful of
    synthetic prompt/budget scenarios so the web app shows them side by side.
    """
    scenarios = [
        ("doc-QA-4k",       4096,  512),
        ("doc-QA-8k",       8192,  1024),
        ("needle-16k",     16384,  512),
        ("streaming-64k",  65536,  2048),
        ("code-4k",         4096,  256),
    ]
    async with SessionLocal() as s:
        rows = 0
        for name, L, budget in scenarios:
            attn = hbm_algos.synth_attention(L, seed=hash(name) & 0xFFFF)

            keep_h, rec_h = hbm_algos.h2o_evict(attn, budget, recent=budget // 4)
            ratio_h = L / max(len(keep_h), 1)
            s.add(HBMAlgoRun(paper="H2O", arxiv="2306.14048", scenario=name,
                             prompt_tokens=L, budget_tokens=budget,
                             recall=rec_h, compression_ratio=ratio_h,
                             notes=f"recent={budget//4}, heavy={budget - budget//4}"))
            rows += 1

            keep_s, rec_s = hbm_algos.streamingllm_evict(attn, sinks=4, window=budget - 4)
            ratio_s = L / max(len(keep_s), 1)
            s.add(HBMAlgoRun(paper="StreamingLLM", arxiv="2309.17453", scenario=name,
                             prompt_tokens=L, budget_tokens=budget,
                             recall=rec_s, compression_ratio=ratio_s,
                             notes=f"sinks=4, window={budget-4}"))
            rows += 1

            keep_sc, rec_sc = hbm_algos.scissorhands_evict(attn, budget, recent=budget // 4)
            ratio_sc = L / max(len(keep_sc), 1)
            s.add(HBMAlgoRun(paper="Scissorhands", arxiv="2305.17118", scenario=name,
                             prompt_tokens=L, budget_tokens=budget,
                             recall=rec_sc, compression_ratio=ratio_sc,
                             notes="persistence-of-importance top-B"))
            rows += 1

            keep_sn, rec_sn = hbm_algos.snapkv_compress(attn, budget, obs_window=64, pool_kernel=7)
            ratio_sn = L / max(len(keep_sn), 1)
            s.add(HBMAlgoRun(paper="SnapKV", arxiv="2404.14469", scenario=name,
                             prompt_tokens=L, budget_tokens=budget,
                             recall=rec_sn, compression_ratio=ratio_sn,
                             notes="observation-window voting + avg-pool"))
            rows += 1

            # KIVI is quantization-based — report memory compression not token recall.
            # Use llama-70b-style head dims.
            report = hbm_algos.kivi_memory(
                kv_tokens=L, d_head=128, num_heads=8, layers=80,
                bits=2, residual_tokens=32,
            )
            s.add(HBMAlgoRun(paper="KIVI", arxiv="2402.02750", scenario=name,
                             prompt_tokens=L, budget_tokens=L,
                             recall=1.0,  # KIVI keeps all tokens, just quantizes
                             compression_ratio=report.compression_ratio,
                             notes=f"2-bit + {report.residual_tokens}-tok fp16 residual, "
                                   f"{report.original_bytes/1e9:.2f}→{report.compressed_bytes/1e9:.2f} GB"))
            rows += 1
        await s.commit()
        return rows


async def seed_networking(n: int = PER_FEATURE) -> int:
    rng = random.Random(2)
    async with SessionLocal() as s:
        inserted = 0
        for _ in range(n):
            model_name, params_b, layers, hidden = rng.choice(MODELS_NET)
            cname, gpus, per_node, nvlink, inter, tflops = rng.choice(CLUSTERS)
            if params_b * 2 > gpus * 80:  # model wouldn't fit — skip
                continue
            best = plan(model_name, params_b, layers, hidden,
                        gpus, per_node, nvlink, inter,
                        batch=rng.choice([8, 16, 32]), seq_len=rng.choice([2048, 4096, 8192]),
                        tflops_per_gpu=tflops)
            s.add(NetworkPlan(
                model_name=model_name, params_b=params_b, layers=layers, hidden=hidden,
                cluster_name=cname, gpus_total=gpus, gpus_per_node=per_node,
                nvlink_gbps=nvlink, inter_gbps=inter,
                tp=best["tp"], pp=best["pp"], dp=best["dp"],
                step_ms=best["step_ms"], comm_ms=best["comm_ms"],
                comm_frac=best["comm_frac"],
            ))
            inserted += 1
        # top up to n if we skipped any
        while inserted < n:
            model_name, params_b, layers, hidden = MODELS_NET[inserted % len(MODELS_NET)]
            cname, gpus, per_node, nvlink, inter, tflops = CLUSTERS[-1]
            best = plan(model_name, params_b, layers, hidden,
                        gpus, per_node, nvlink, inter,
                        tflops_per_gpu=tflops)
            s.add(NetworkPlan(
                model_name=model_name, params_b=params_b, layers=layers, hidden=hidden,
                cluster_name=cname, gpus_total=gpus, gpus_per_node=per_node,
                nvlink_gbps=nvlink, inter_gbps=inter,
                tp=best["tp"], pp=best["pp"], dp=best["dp"],
                step_ms=best["step_ms"], comm_ms=best["comm_ms"],
                comm_frac=best["comm_frac"],
            ))
            inserted += 1
        await s.commit()
    return inserted


async def seed_energy(n: int = PER_FEATURE) -> int:
    async with SessionLocal() as s:
        # regions
        for region, price, co2, pue, lat, gpus, queue in REGIONS:
            s.add(EnergyRegion(
                region=region, price_usd_per_kwh=price, carbon_gco2_per_kwh=co2,
                pue=pue, latency_ms=lat, available_gpus=gpus, grid_queue_months=queue,
            ))
        await s.commit()

    # Decisions: exercise the router n times with diverse inputs.
    from .themes.energy import ENERGY_PER_TOKEN_KWH
    rng = random.Random(3)
    async with SessionLocal() as s:
        regions = [r for r in REGIONS]  # local copy
        for _ in range(n):
            tokens = rng.choice([500, 1000, 2500, 5000, 10000, 50000])
            sla = rng.choice([100, 200, 500, 1000, 2000, 5000])
            w_cost = 1.0
            w_carbon = rng.choice([0.0, 0.3, 1.0, 2.0])
            eligible = [r for r in regions if r[4] <= sla and r[5] > 0]
            if not eligible:
                continue
            energy_kwh = tokens * ENERGY_PER_TOKEN_KWH
            scored = []
            for reg, price, co2, pue, lat, _, _ in eligible:
                cost = price * pue * energy_kwh
                carbon = co2 * pue * energy_kwh
                obj = w_cost * cost + w_carbon * carbon / 1000.0
                scored.append((obj, cost, carbon, reg))
            scored.sort()
            best_obj, best_cost, best_carbon, best_reg = scored[0]
            worst_obj = scored[-1][0]
            savings = 100 * (worst_obj - best_obj) / max(worst_obj, 1e-9)
            s.add(EnergyDecision(
                request_tokens=tokens, latency_sla_ms=sla,
                chosen_region=best_reg, cost_usd=best_cost,
                carbon_g=best_carbon,
                savings_vs_worst_pct=savings,
                reason=f"w_cost={w_cost}, w_carbon={w_carbon}; beat worst by {savings:.1f}%",
            ))
        await s.commit()
    return n


async def seed_inference(n: int = PER_FEATURE * 2) -> int:
    """Seed more than 15 so percentile stats are meaningful."""
    rng = random.Random(4)
    async with SessionLocal() as s:
        for _ in range(n):
            model = rng.choice(MODELS_INF)
            prompt = int(rng.lognormvariate(6.5, 1.1))      # skewed prompt dist
            prompt = max(32, min(prompt, 60000))
            completion = int(rng.lognormvariate(4.5, 0.8))
            completion = max(8, min(completion, 2000))
            ttft = rng.uniform(80, 600) + prompt * 0.02
            total_lat = ttft + completion * rng.uniform(8, 25)
            cost = prompt * PRICING_IN[model] + completion * PRICING_OUT[model]
            kv_reuse = rng.betavariate(2, 4) * 100   # mostly low reuse
            s.add(InferenceRun(
                request_id=uuid.uuid4().hex[:12],
                model=model,
                prompt_tokens=prompt, completion_tokens=completion,
                ttft_ms=ttft, latency_ms=total_lat,
                cost_usd=cost, kv_reuse_pct=kv_reuse,
            ))
        await s.commit()
    return n


async def seed_photonics(n: int = PER_FEATURE) -> int:
    scenarios = []
    rng = random.Random(5)
    names = [
        "small-copper", "small-mixed", "small-all-optical",
        "medium-low-optical", "medium-balanced", "medium-high-optical",
        "large-copper-heavy", "large-mixed", "large-optical",
        "rubin-72-fullcpo", "rubin-72-halfcpo", "rubin-72-quartercpo",
        "edge-cluster-8", "edge-cluster-16", "edge-cluster-32",
        "spine-leaf-128", "spine-leaf-256",
    ]
    for i in range(n):
        name = names[i] if i < len(names) else f"scenario-{i+1}"
        num_gpus = rng.choice([8, 16, 32, 64, 72, 128, 256])
        optical_fraction = rng.choice([0.0, 0.1, 0.25, 0.5, 0.75, 1.0])
        optical_gbps = rng.choice([800, 1600, 3200])
        copper_gbps = rng.choice([200, 400, 900])
        dag_size = rng.choice([32, 64, 128, 256])
        scenarios.append((name, num_gpus, optical_fraction, optical_gbps, copper_gbps, dag_size))

    async with SessionLocal() as s:
        for name, num_gpus, of, og, cg, dag in scenarios:
            r = schedule(num_gpus, of, og, cg, dag, seed=hash(name) & 0xFFFF)
            s.add(PhotonicScenario(
                name=name, num_gpus=num_gpus, optical_fraction=of,
                optical_gbps=og, copper_gbps=cg, dag_size=dag,
                baseline_ms=r["baseline_ms"], optimized_ms=r["optimized_ms"],
                speedup_pct=r["speedup_pct"],
            ))
        await s.commit()
    return len(scenarios)


async def seed_network_algorithms() -> int:
    """Run each paper across several (model, cluster, workload) scenarios."""
    models = [
        net_algos.ModelSpec("llama-3-70b", 70, 80, 8192),
        net_algos.ModelSpec("llama-3-405b", 405, 126, 16384),
        net_algos.ModelSpec("mixtral-8x22b", 141, 56, 6144),
    ]
    clusters = [
        net_algos.ClusterSpec(32, 8, 900, 400, TFLOPS_H100),
        net_algos.ClusterSpec(72, 72, 1800, 800, TFLOPS_B200),
        net_algos.ClusterSpec(128, 8, 900, 400, TFLOPS_H100),
    ]
    workloads = [
        net_algos.Workload(2048, 256, rate_rps=20, slo_ttft_ms=600, slo_tpot_ms=50),
        net_algos.Workload(8192, 512, rate_rps=10, slo_ttft_ms=2000, slo_tpot_ms=50),
    ]
    rows = 0
    async with SessionLocal() as s:
        for m in models:
            for c in clusters:
                for w in workloads:
                    scen = f"{m.name}/{c.gpus}gpu/p{w.prompt_tokens}"

                    r = net_algos.distserve(m, c, w)
                    s.add(NetworkAlgoRun(
                        paper="DistServe", arxiv="2401.09670", scenario=scen,
                        metric_name="goodput_rps", metric_value=r["goodput_rps"],
                        baseline_value=r["baseline_goodput_rps"],
                        improvement_pct=r["improvement_pct"],
                        notes=f"P=(tp{r['tp_p']},pp{r['pp_p']},{r['gpus_p']}gpu) "
                              f"D=(tp{r['tp_d']},pp{r['pp_d']},{r['gpus_d']}gpu)",
                    )); rows += 1

                    r = net_algos.splitwise(m, c, w)
                    s.add(NetworkAlgoRun(
                        paper="Splitwise", arxiv="2311.18677", scenario=scen,
                        metric_name="ttft_ms", metric_value=r["total_ttft_ms"],
                        baseline_value=r["baseline_ttft_ms"],
                        improvement_pct=r["improvement_pct"],
                        notes=f"prompt={r['prompt_gpus']}gpu, token={r['token_gpus']}gpu, "
                              f"kv-xfer={r['kv_transfer_ms']:.1f}ms",
                    )); rows += 1

                    r = net_algos.loongserve(m, c, w)
                    s.add(NetworkAlgoRun(
                        paper="LoongServe", arxiv="2404.09526", scenario=scen,
                        metric_name="total_ms", metric_value=r["total_ms_elastic"],
                        baseline_value=r["total_ms_static"],
                        improvement_pct=r["improvement_pct"],
                        notes=f"sp_prefill={r['sp_prefill']}, sp_decode={r['sp_decode']}",
                    )); rows += 1

                    r = net_algos.helix(m, c, w)
                    s.add(NetworkAlgoRun(
                        paper="Helix", arxiv="2406.01566", scenario=scen,
                        metric_name="rps", metric_value=r["helix_rps"],
                        baseline_value=r["baseline_rps"],
                        improvement_pct=r["improvement_pct"],
                        notes=f"max-flow over {len(r['tiers'])} heterogeneous tiers",
                    )); rows += 1

                    r = net_algos.spotserve(m, c, w, preemption_pct=0.25)
                    s.add(NetworkAlgoRun(
                        paper="SpotServe", arxiv="2311.15566", scenario=scen,
                        metric_name="rps_after_reconfig",
                        metric_value=r["rps_after_reconfig"],
                        baseline_value=r["rps_naive_during_event"],
                        improvement_pct=r["improvement_pct"],
                        notes=f"preempted {r['preempted_gpus']}gpu, "
                              f"migrate {r['migration_gb']:.1f}GB in {r['migration_ms']:.0f}ms",
                    )); rows += 1
        await s.commit()
    return rows


async def seed_energy_algorithms() -> int:
    rows = 0
    async with SessionLocal() as s:
        # Perseus: 4 pipeline shapes
        for stages, times in [(4, [100, 90, 75, 60]), (8, [80, 78, 70, 65, 60, 55, 50, 45]),
                              (6, [120, 115, 100, 95, 90, 70])]:
            r = en_algos.perseus(stages, times)
            s.add(EnergyAlgoRun(
                paper="Perseus", arxiv="2312.06902",
                scenario=f"{stages}-stage pipeline",
                metric_name="energy_saved",
                metric_value=r["optimised_energy"],
                baseline_value=r["baseline_energy"],
                improvement_pct=r["improvement_pct"],
                notes=f"freqs={r['stage_freqs']}, period={r['period_ms']:.0f}ms",
            )); rows += 1

        # POLCA: 3 GPU/rack caps
        for gpu, cap_kw, capw in [("H100", 40, 500), ("B200", 50, 700), ("H100", 30, 450)]:
            r = en_algos.polca(num_gpus=0, gpu=gpu, rack_power_cap_kw=cap_kw,
                               safe_capped_watts=capw)
            s.add(EnergyAlgoRun(
                paper="POLCA", arxiv="2308.12908",
                scenario=f"{gpu}/{cap_kw}kW/cap{capw}W",
                metric_name="agg_throughput",
                metric_value=r["aggregate_throughput_polca"],
                baseline_value=r["aggregate_throughput_baseline"],
                improvement_pct=r["improvement_pct"],
                notes=f"{r['gpus_per_rack_baseline']}→{r['gpus_per_rack_polca']} GPUs/rack, "
                      f"perf hit {r['single_gpu_perf_hit_pct']:.1f}%",
            )); rows += 1

        # DynamoLLM: 3 workload mixes
        for rps in [50, 200, 1000]:
            tiers = [
                {"name": "llama-8b",  "quality_frac": 0.6, "energy_mj_per_tok": 0.2, "accepts_rps_share": 0.6},
                {"name": "llama-70b", "quality_frac": 0.9, "energy_mj_per_tok": 1.0, "accepts_rps_share": 0.3},
                {"name": "gpt-oss",   "quality_frac": 1.0, "energy_mj_per_tok": 2.0, "accepts_rps_share": 0.1},
            ]
            r = en_algos.dynamo_llm(rps, tiers)
            s.add(EnergyAlgoRun(
                paper="DynamoLLM", arxiv="2408.00741",
                scenario=f"{rps}rps mixed workload",
                metric_name="energy_mj",
                metric_value=r["energy_mj_dynamo"],
                baseline_value=r["energy_mj_baseline"],
                improvement_pct=r["improvement_pct"],
                notes="; ".join(f"{m['tier']}={m['share_pct']}%" for m in r["mix"]),
            )); rows += 1

        # LLMCarbon: 3 model scales
        for pb, gh, tok in [(70, 1_600_000, 5e13), (405, 16_000_000, 5e13), (8, 150_000, 1e13)]:
            r = en_algos.llm_carbon(pb, gh, tok, grid_gco2_per_kwh=380)
            s.add(EnergyAlgoRun(
                paper="LLMCarbon", arxiv="2309.14393",
                scenario=f"{pb}B params, {gh:,} GPU-h, {tok:.1e} served tok",
                metric_name="addressable_kgco2",
                metric_value=r["addressable_kg"],
                baseline_value=r["total_kg"],
                improvement_pct=r["improvement_pct"],
                notes=f"train={r['train_op_kg']:.0f}kg, serve={r['serving_op_kg']:.0f}kg, "
                      f"embodied={r['embodied_kg']:.0f}kg",
            )); rows += 1

        # VCC: 2 intensity profiles
        import random as _r
        _r.seed(13)
        for label, base in [("ca-grid", 400), ("nordic", 80)]:
            intensity = [base + 200 * abs((h - 18) / 12) for h in range(24)]
            demand = [max(1, _r.gauss(1000, 300)) for _ in range(24)]
            r = en_algos.vcc_shift(intensity, demand, shiftable_fraction=0.4)
            s.add(EnergyAlgoRun(
                paper="VCC", arxiv="2106.11750",
                scenario=f"{label} grid",
                metric_name="gco2_per_tok",
                metric_value=r["carbon_after_g_per_tok"],
                baseline_value=r["carbon_before_g_per_tok"],
                improvement_pct=r["improvement_pct"],
                notes=f"clean hrs={r['cleanest_hours'][:4]}, shifted=40%",
            )); rows += 1

        await s.commit()
    return rows


async def seed_inference_algorithms() -> int:
    import random as _r
    _r.seed(7)
    rows = 0
    async with SessionLocal() as s:
        # Medusa: sweep heads × acceptance
        for heads, acc in [(3, 0.65), (4, 0.70), (5, 0.75), (6, 0.80)]:
            r = inf_algos.medusa(heads, acc)
            s.add(InferenceAlgoRun(
                paper="Medusa", arxiv="2401.10774",
                scenario=f"{heads}-head @acc={acc}",
                metric_name="tokens_per_step",
                metric_value=r["expected_tokens_per_step"],
                baseline_value=r["baseline_tokens_per_step"],
                improvement_pct=r["improvement_pct"],
                notes=f"accepted_expected={r['expected_tokens_per_step']:.2f}",
            )); rows += 1

        # EAGLE: sweep depth × acceptance
        for depth, acc in [(3, 0.8), (4, 0.8), (5, 0.85), (6, 0.7)]:
            r = inf_algos.eagle(depth, branching=2, acceptance_rate=acc)
            s.add(InferenceAlgoRun(
                paper="EAGLE", arxiv="2401.15077",
                scenario=f"depth={depth} acc={acc}",
                metric_name="speedup_pct",
                metric_value=r["improvement_pct"],
                baseline_value=0,
                improvement_pct=r["improvement_pct"],
                notes=f"draft_cost={r['draft_cost']:.2f}",
            )); rows += 1

        # Sarathi-Serve: sweep prompt lengths
        for p in [2048, 8192, 16384, 32768]:
            r = inf_algos.sarathi_serve(p, chunk_size=512)
            s.add(InferenceAlgoRun(
                paper="Sarathi-Serve", arxiv="2403.02310",
                scenario=f"prompt={p}tok",
                metric_name="tbt_ms",
                metric_value=r["tbt_sarathi_ms"],
                baseline_value=r["tbt_naive_ms"],
                improvement_pct=r["improvement_pct"],
                notes=f"{(p + r['chunk_size'] - 1) // r['chunk_size']} chunks of {r['chunk_size']}tok",
            )); rows += 1

        # RouteLLM: sweep query dists
        for label, (strong, weak) in [("gpt4-vs-mixtral", (15, 0.6)),
                                       ("opus-vs-haiku", (75, 1.25)),
                                       ("70b-vs-8b", (2.0, 0.3))]:
            dist = [_r.betavariate(2, 5) for _ in range(1000)]
            r = inf_algos.route_llm(strong, weak, dist, quality_threshold=0.7)
            s.add(InferenceAlgoRun(
                paper="RouteLLM", arxiv="2406.18665",
                scenario=label,
                metric_name="cost_per_tok",
                metric_value=r["cost_per_tok_route"],
                baseline_value=r["cost_per_tok_all_strong"],
                improvement_pct=r["improvement_pct"],
                notes=f"{r['fraction_to_strong']*100:.0f}%→strong, "
                      f"{r['fraction_to_weak']*100:.0f}%→weak",
            )); rows += 1

        # LLMLingua-2
        for p, rate in [(2048, 0.5), (4096, 0.4), (8192, 0.33)]:
            r = inf_algos.llmlingua2(p, compress_rate=rate)
            s.add(InferenceAlgoRun(
                paper="LLMLingua-2", arxiv="2403.12968",
                scenario=f"prompt={p}@{int(rate*100)}%",
                metric_name="effective_cost_tok",
                metric_value=r["cost_compressed_tok"],
                baseline_value=r["cost_base_tok"],
                improvement_pct=r["improvement_pct"],
                notes=f"compressed {p}→{r['compressed_tokens']}tok, Q={r['quality_retention']}",
            )); rows += 1

        # RadixAttention
        for uniq, total in [(100, 1000), (300, 1000), (50, 500), (10, 200)]:
            r = inf_algos.radix_attention(uniq, total)
            s.add(InferenceAlgoRun(
                paper="RadixAttention", arxiv="2312.07104",
                scenario=f"{uniq}uniq/{total}req",
                metric_name="ttft_saved_pct",
                metric_value=r["improvement_pct"],
                baseline_value=0,
                improvement_pct=r["improvement_pct"],
                notes=f"hit-rate={r['cache_hit_rate']*100:.0f}%",
            )); rows += 1

        await s.commit()
    return rows


async def seed_photonics_algorithms() -> int:
    rows = 0
    async with SessionLocal() as s:
        # TopoOpt: varying DP/PP
        for gpus, dp, pp in [(64, 8, 4), (128, 16, 4), (256, 32, 4), (72, 9, 8)]:
            r = ph_algos.topo_opt(gpus, dp, pp)
            s.add(PhotonicsAlgoRun(
                paper="TopoOpt", arxiv="2202.00433",
                scenario=f"{gpus}gpu DP{dp}/PP{pp}",
                metric_name="ar_ms",
                metric_value=r["ar_ms_topoopt"],
                baseline_value=r["ar_ms_copper"],
                improvement_pct=r["improvement_pct"],
                notes="optical DP-ring vs copper fat-tree",
            )); rows += 1

        # SiP-ML: wavelength sweep
        for gpus, wl in [(32, 4), (64, 8), (128, 8), (128, 16)]:
            r = ph_algos.sip_ml(gpus, wavelengths=wl)
            s.add(PhotonicsAlgoRun(
                paper="SiP-ML", arxiv="2104.05308",
                scenario=f"{gpus}gpu ×{wl}λ",
                metric_name="bisection_gbps",
                metric_value=r["bisection_sipml_gbps"],
                baseline_value=r["bisection_copper_gbps"],
                improvement_pct=r["improvement_pct"],
                notes=f"per-pair eff bw={r['effective_per_pair_gbps']}Gbps",
            )); rows += 1

        # TACCL: topology sweep
        for gpus, topo in [(64, "mesh"), (128, "torus"), (256, "dragonfly"),
                           (64, "fat-tree"), (128, "ring")]:
            r = ph_algos.taccl(gpus, topology=topo)
            s.add(PhotonicsAlgoRun(
                paper="TACCL", arxiv="2111.04867",
                scenario=f"{gpus}gpu {topo}",
                metric_name="ar_ms",
                metric_value=r["taccl_ms"],
                baseline_value=r["ring_baseline_ms"],
                improvement_pct=r["improvement_pct"],
                notes="synthesized collective algorithm",
            )); rows += 1

        # Rail-only
        for per, rails in [(32, 8), (64, 8), (32, 16), (128, 8)]:
            r = ph_algos.rail_only(per, rails)
            s.add(PhotonicsAlgoRun(
                paper="Rail-only", arxiv="2307.12169",
                scenario=f"{per}×{rails}={per*rails}gpu",
                metric_name="ar_ms",
                metric_value=r["ar_ms_rail_only"],
                baseline_value=r["ar_ms_clos_baseline"],
                improvement_pct=r["improvement_pct"],
                notes=f"fabric cost -{r['fabric_cost_savings_pct']}%",
            )); rows += 1

        # Jupiter OCS
        for blocks, skew in [(16, 1.5), (32, 2.0), (64, 2.5), (8, 1.2)]:
            r = ph_algos.jupiter_ocs(blocks, traffic_matrix_skew=skew)
            s.add(PhotonicsAlgoRun(
                paper="JupiterOCS", arxiv="sigcomm22",
                scenario=f"{blocks}blocks skew={skew}",
                metric_name="path_ms",
                metric_value=r["ocs_path_ms"],
                baseline_value=r["naive_path_ms"],
                improvement_pct=r["improvement_pct"],
                notes=f"{r['matched_pairs']} direct circuits, "
                      f"{r['fraction_traffic_direct']*100:.0f}% traffic direct",
            )); rows += 1

        await s.commit()
    return rows


async def main() -> None:
    print("[seed] drop_all + init_db")
    await drop_all()
    await init_db()

    n_hbm = await seed_hbm()
    print(f"[seed] {n_hbm:4d} rows → hbm_decisions")

    n_hbma = await seed_hbm_algorithms()
    print(f"[seed] {n_hbma:4d} rows → hbm_algo_runs (5 papers × 5 scenarios)")

    n_net = await seed_networking()
    print(f"[seed] {n_net:4d} rows → network_plans")

    n_neta = await seed_network_algorithms()
    print(f"[seed] {n_neta:4d} rows → network_algo_runs (5 papers)")

    n_en = await seed_energy()
    print(f"[seed] {n_en:4d} decisions + {len(REGIONS)} regions → energy_*")

    n_ena = await seed_energy_algorithms()
    print(f"[seed] {n_ena:4d} rows → energy_algo_runs (5 papers)")

    n_inf = await seed_inference()
    print(f"[seed] {n_inf:4d} rows → inference_runs")

    n_infa = await seed_inference_algorithms()
    print(f"[seed] {n_infa:4d} rows → inference_algo_runs (6 papers)")

    n_ph = await seed_photonics()
    print(f"[seed] {n_ph:4d} rows → photonic_scenarios")

    n_pha = await seed_photonics_algorithms()
    print(f"[seed] {n_pha:4d} rows → photonics_algo_runs (5 papers)")

    total = (n_hbm + n_hbma + n_net + n_neta + n_en + n_ena
             + n_inf + n_infa + n_ph + n_pha + len(REGIONS))
    print(f"[seed] done. {total} rows total across 11 tables (floor 15/feature).")


if __name__ == "__main__":
    asyncio.run(main())
