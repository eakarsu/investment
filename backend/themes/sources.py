"""
Return the real source code of each paper's implementation.

Clicking a paper in the web UI hits `/api/source/{theme}/{paper}` which calls
`inspect.getsource` on each mapped symbol and concatenates them — so what the
user sees is exactly what the seeder / endpoints execute. No stubs, no string
literals hand-written here: everything flows through `inspect`.
"""

from __future__ import annotations

import inspect

from . import (
    hbm_algorithms,
    networking_algorithms,
    energy_algorithms,
    inference_algorithms,
    photonics_algorithms,
)


# (module, [symbol names — functions, dataclasses, helpers])
# The list is in reading order so the snippet tells a coherent story.
_MAP: dict[str, dict[str, tuple[object, list[str]]]] = {
    "hbm": {
        "H2O":          (hbm_algorithms, ["_attention_at_step", "h2o_evict"]),
        "StreamingLLM": (hbm_algorithms, ["streamingllm_keep",
                                          "streamingllm_remap",
                                          "streamingllm_evict"]),
        "Scissorhands": (hbm_algorithms, ["_attention_at_step",
                                          "scissorhands_evict"]),
        "SnapKV":       (hbm_algorithms, ["snapkv_compress"]),
        "KIVI":         (hbm_algorithms, ["KIVIReport", "kivi_memory"]),
    },
    "networking": {
        "DistServe":  (networking_algorithms,
                       ["_SimReq", "_simulate_distserve", "distserve"]),
        "Splitwise":  (networking_algorithms, ["splitwise"]),
        "LoongServe": (networking_algorithms, ["loongserve"]),
        "Helix":      (networking_algorithms, ["_edmonds_karp", "helix"]),
        "SpotServe":  (networking_algorithms, ["spotserve"]),
    },
    "energy": {
        "Perseus":   (energy_algorithms, ["perseus"]),
        "POLCA":     (energy_algorithms, ["polca"]),
        "DynamoLLM": (energy_algorithms,
                      ["_sigmoid", "_train_logreg", "dynamo_llm"]),
        "LLMCarbon": (energy_algorithms, ["llm_carbon"]),
        "VCC":       (energy_algorithms, ["vcc_shift"]),
    },
    "inference": {
        "Medusa":         (inference_algorithms, ["medusa"]),
        "EAGLE":          (inference_algorithms, ["_EAGLENode", "eagle"]),
        "Sarathi-Serve":  (inference_algorithms, ["_Req", "sarathi_serve"]),
        "RouteLLM":       (inference_algorithms,
                           ["_sigmoid", "_train_logreg", "route_llm"]),
        "LLMLingua-2":    (inference_algorithms,
                           ["_score_token", "llmlingua2"]),
        "RadixAttention": (inference_algorithms,
                           ["RadixNode", "RadixTree", "radix_attention"]),
    },
    "photonics": {
        "TopoOpt":     (photonics_algorithms, ["topo_opt"]),
        "SiP-ML":      (photonics_algorithms, ["_Flow", "sip_ml"]),
        "TACCL":       (photonics_algorithms,
                        ["_topology_adjacency", "_synth_ring",
                         "_synth_tree", "_synth_recursive_doubling",
                         "_diameter", "taccl"]),
        "Rail-only":   (photonics_algorithms, ["rail_only"]),
        "Jupiter OCS": (photonics_algorithms,
                        ["_weighted_b_matching", "jupiter_ocs"]),
    },
}


# Default invocation for each paper: a callable that runs the algorithm with
# representative args and returns a JSON-serialisable result dict. This is what
# the website's "Run" button executes.
def _run_hbm_h2o():
    from . import hbm_algorithms as hbm
    attn = hbm.synth_attention(length=4096, seed=42)
    keep, recall = hbm.h2o_evict(attn, budget=512, recent=128, num_decode_steps=16)
    return {
        "inputs": {"context_len": 4096, "budget": 512, "recent": 128, "decode_steps": 16},
        "kept_tokens": len(keep),
        "recall": round(recall, 4),
        "compression_ratio": round(4096 / len(keep), 2),
        "first_kept": keep[:10], "last_kept": keep[-10:],
    }

def _run_hbm_streaming():
    from . import hbm_algorithms as hbm
    attn = hbm.synth_attention(4096, seed=42)
    keep, pos_map = hbm.streamingllm_remap(4096, sinks=4, window=508)
    return {
        "inputs": {"context_len": 4096, "sinks": 4, "window": 508},
        "kept_tokens": len(keep),
        "recall": round(hbm.recall_of(attn, set(keep)), 4),
        "pos_remap_first3": dict(list(pos_map.items())[:3]),
        "pos_remap_last3": dict(list(pos_map.items())[-3:]),
    }

def _run_hbm_scissorhands():
    from . import hbm_algorithms as hbm
    attn = hbm.synth_attention(4096, seed=42)
    keep, recall = hbm.scissorhands_evict(attn, 512, recent=128, history=64,
                                          top_k_per_step=16, num_decode_steps=16)
    return {"inputs": {"context_len": 4096, "budget": 512, "history": 64,
                       "top_k_per_step": 16, "num_decode_steps": 16},
            "kept_tokens": len(keep), "recall": round(recall, 4)}

def _run_hbm_snapkv():
    from . import hbm_algorithms as hbm
    attn = hbm.synth_attention(4096, seed=42)
    keep, recall = hbm.snapkv_compress(attn, 512, obs_window=64, pool_kernel=7)
    return {"inputs": {"context_len": 4096, "budget": 512, "obs_window": 64, "pool_kernel": 7},
            "kept_tokens": len(keep), "recall": round(recall, 4)}

def _run_hbm_kivi():
    from . import hbm_algorithms as hbm
    rep = hbm.kivi_memory(kv_tokens=4096, d_head=128, num_heads=8, layers=80,
                          bits=2, residual_tokens=32, group_size=32)
    return {"inputs": {"kv_tokens": 4096, "d_head": 128, "heads": 8, "layers": 80,
                       "bits": 2, "residual": 32, "group_size": 32},
            "original_bytes": rep.original_bytes,
            "compressed_bytes": rep.compressed_bytes,
            "compression_ratio": round(rep.compression_ratio, 2),
            "original_gb": round(rep.original_bytes / 1e9, 2),
            "compressed_gb": round(rep.compressed_bytes / 1e9, 2),
            "scale_overhead_mb": round(rep.scale_overhead_bytes / 1e6, 3),
            "per_channel_scales": rep.per_channel_scales,
            "per_token_scales": rep.per_token_scales}

def _run_net(fn_name):
    from . import networking_algorithms as net
    m = net.ModelSpec("llama-3-70b", 70, 80, 8192)
    c = net.ClusterSpec(32, 8, 900, 400, 989)
    w = net.Workload(2048, 256, rate_rps=20, slo_ttft_ms=600, slo_tpot_ms=50)
    fn = getattr(net, fn_name)
    out = fn(m, c, w)
    # Trim non-JSON pieces.
    return {"inputs": {"model": m.name, "gpus": c.gpus, "prompt_tokens": w.prompt_tokens,
                       "completion": w.completion_tokens, "rate_rps": w.rate_rps},
            **{k: v for k, v in out.items()
               if isinstance(v, (int, float, str, bool, list, dict, type(None)))}}

def _run_en_perseus():
    from . import energy_algorithms as en
    r = en.perseus(pipeline_stages=4, stage_times_ms=[100, 90, 75, 60])
    return {"inputs": {"stages": 4, "times_ms": [100, 90, 75, 60]}, **r}

def _run_en_polca():
    from . import energy_algorithms as en
    r = en.polca(num_gpus=0, gpu="H100", rack_power_cap_kw=40, safe_capped_watts=500)
    # drop the long per-tick logs for readability
    r = {k: v for k, v in r.items() if not isinstance(v, list) or len(v) < 10}
    return {"inputs": {"gpu": "H100", "rack_cap_kw": 40, "capped_w": 500}, **r}

def _run_en_dynamo():
    from . import energy_algorithms as en
    tiers = [
        {"name": "llama-8b",  "quality_frac": 0.6, "energy_mj_per_tok": 0.2, "accepts_rps_share": 0.6},
        {"name": "llama-70b", "quality_frac": 0.9, "energy_mj_per_tok": 1.0, "accepts_rps_share": 0.3},
        {"name": "gpt-oss",   "quality_frac": 1.0, "energy_mj_per_tok": 2.0, "accepts_rps_share": 0.1},
    ]
    r = en.dynamo_llm(workload_rps=200, model_tiers=tiers)
    return {"inputs": {"rps": 200, "tiers": [t["name"] for t in tiers]}, **r}

def _run_en_carbon():
    from . import energy_algorithms as en
    r = en.llm_carbon(params_b=70, training_gpu_hours=1_600_000, serving_tokens=5e13,
                      grid_gco2_per_kwh=380)
    return {"inputs": {"params_b": 70, "train_gpu_h": 1_600_000, "serve_tok": 5e13,
                       "grid_gco2_per_kwh": 380}, **r}

def _run_en_vcc():
    from . import energy_algorithms as en
    import random
    random.seed(13)
    intensity = [400 + 200 * abs((h - 18) / 12) for h in range(24)]
    demand = [max(1, random.gauss(1000, 300)) for _ in range(24)]
    r = en.vcc_shift(intensity, demand, shiftable_fraction=0.4)
    return {"inputs": {"hours": 24, "shiftable_frac": 0.4}, **r}

def _run_inf(name, **kwargs):
    from . import inference_algorithms as inf
    fn = getattr(inf, name)
    r = fn(**kwargs)
    return {"inputs": kwargs, **{k: v for k, v in r.items()
                                 if isinstance(v, (int, float, str, bool, list, dict, type(None)))}}

def _run_inf_medusa():  return _run_inf("medusa", num_heads=5, acceptance_per_head=0.75)
def _run_inf_eagle():   return _run_inf("eagle",  draft_depth=5, branching=2, acceptance_rate=0.85)
def _run_inf_sarathi():
    r = _run_inf("sarathi_serve", prompt_tokens=8192, chunk_size=512)
    r["chunks"] = (8192 + 511) // 512
    return r
def _run_inf_route():
    from . import inference_algorithms as inf
    import random
    dist = [random.Random(7).betavariate(2, 5) for _ in range(1000)]
    r = inf.route_llm(strong_cost_per_tok=15, weak_cost_per_tok=0.6,
                      query_difficulty_dist=dist, quality_threshold=0.7)
    return {"inputs": {"strong_$/tok": 15, "weak_$/tok": 0.6, "quality_thresh": 0.7,
                       "n_queries": 1000}, **r}
def _run_inf_lingua(): return _run_inf("llmlingua2", prompt_tokens=4096, compress_rate=0.4)
def _run_inf_radix():  return _run_inf("radix_attention", unique_prefixes=100, total_requests=1000)

def _run_ph_topo():
    from . import photonics_algorithms as ph
    r = ph.topo_opt(num_gpus=128, dp=16, pp=4)
    return {"inputs": {"gpus": 128, "dp": 16, "pp": 4}, **r}
def _run_ph_sipml():
    from . import photonics_algorithms as ph
    r = ph.sip_ml(num_gpus=64, wavelengths=8)
    return {"inputs": {"gpus": 64, "wavelengths": 8}, **r}
def _run_ph_taccl():
    from . import photonics_algorithms as ph
    r = ph.taccl(num_gpus=128, topology="torus")
    return {"inputs": {"gpus": 128, "topology": "torus"}, **r}
def _run_ph_rail():
    from . import photonics_algorithms as ph
    r = ph.rail_only(num_gpus_per_rail=32, num_rails=8)
    return {"inputs": {"gpus_per_rail": 32, "rails": 8}, **r}
def _run_ph_ocs():
    from . import photonics_algorithms as ph
    r = ph.jupiter_ocs(num_blocks=32, traffic_matrix_skew=2.0)
    return {"inputs": {"blocks": 32, "skew": 2.0}, **r}


_RUNNERS: dict[str, dict[str, callable]] = {
    "hbm": {
        "H2O": _run_hbm_h2o, "StreamingLLM": _run_hbm_streaming,
        "Scissorhands": _run_hbm_scissorhands, "SnapKV": _run_hbm_snapkv,
        "KIVI": _run_hbm_kivi,
    },
    "networking": {
        "DistServe":  lambda: _run_net("distserve"),
        "Splitwise":  lambda: _run_net("splitwise"),
        "LoongServe": lambda: _run_net("loongserve"),
        "Helix":      lambda: _run_net("helix"),
        "SpotServe":  lambda: _run_net("spotserve"),
    },
    "energy": {
        "Perseus": _run_en_perseus, "POLCA": _run_en_polca,
        "DynamoLLM": _run_en_dynamo, "LLMCarbon": _run_en_carbon,
        "VCC": _run_en_vcc,
    },
    "inference": {
        "Medusa": _run_inf_medusa, "EAGLE": _run_inf_eagle,
        "Sarathi-Serve": _run_inf_sarathi, "RouteLLM": _run_inf_route,
        "LLMLingua-2": _run_inf_lingua, "RadixAttention": _run_inf_radix,
    },
    "photonics": {
        "TopoOpt": _run_ph_topo, "SiP-ML": _run_ph_sipml,
        "TACCL": _run_ph_taccl, "Rail-only": _run_ph_rail,
        "Jupiter OCS": _run_ph_ocs,
    },
}


def run_paper(theme: str, paper: str) -> dict:
    """Execute the paper's algorithm with representative defaults, return its
    output (JSON-serialisable). KeyError -> 404 at the HTTP layer."""
    fn = _RUNNERS[theme][paper]
    return fn()


# Representative inputs per paper. Used by endpoints that delegate the actual
# execution to an LLM (so we don't have to run the local Python first).
_INPUTS: dict[str, dict[str, dict]] = {
    "hbm": {
        "H2O":          {"context_len": 4096, "budget": 512, "recent": 128, "decode_steps": 16},
        "StreamingLLM": {"context_len": 4096, "sinks": 4, "window": 508},
        "Scissorhands": {"context_len": 4096, "budget": 512, "history": 64,
                         "top_k_per_step": 16, "num_decode_steps": 16},
        "SnapKV":       {"context_len": 4096, "budget": 512, "obs_window": 64, "pool_kernel": 7},
        "KIVI":         {"kv_tokens": 4096, "d_head": 128, "heads": 8, "layers": 80,
                         "bits": 2, "residual": 32, "group_size": 32},
    },
    "networking": {
        "DistServe":  {"model": "llama-3-70b", "gpus": 32, "prompt_tokens": 2048,
                       "completion": 256, "rate_rps": 20},
        "Splitwise":  {"model": "llama-3-70b", "gpus": 32, "prompt_tokens": 2048,
                       "completion": 256, "rate_rps": 20},
        "LoongServe": {"model": "llama-3-70b", "gpus": 32, "prompt_tokens": 2048,
                       "completion": 256, "rate_rps": 20},
        "Helix":      {"model": "llama-3-70b", "gpus": 32, "prompt_tokens": 2048,
                       "completion": 256, "rate_rps": 20},
        "SpotServe":  {"model": "llama-3-70b", "gpus": 32, "prompt_tokens": 2048,
                       "completion": 256, "rate_rps": 20, "preemption_pct": 0.25},
    },
    "energy": {
        "Perseus":   {"stages": 4, "times_ms": [100, 90, 75, 60]},
        "POLCA":     {"gpu": "H100", "rack_cap_kw": 40, "capped_w": 500},
        "DynamoLLM": {"rps": 200,
                      "tiers": ["llama-8b", "llama-70b", "gpt-oss"]},
        "LLMCarbon": {"params_b": 70, "train_gpu_h": 1_600_000, "serve_tok": 5e13,
                      "grid_gco2_per_kwh": 380},
        "VCC":       {"hours": 24, "shiftable_frac": 0.4},
    },
    "inference": {
        "Medusa":         {"num_heads": 5, "acceptance_per_head": 0.75},
        "EAGLE":          {"draft_depth": 5, "branching": 2, "acceptance_rate": 0.85},
        "Sarathi-Serve":  {"prompt_tokens": 8192, "chunk_size": 512},
        "RouteLLM":       {"strong_cost_per_tok": 15, "weak_cost_per_tok": 0.6,
                           "quality_thresh": 0.7, "n_queries": 1000},
        "LLMLingua-2":    {"prompt_tokens": 4096, "compress_rate": 0.4},
        "RadixAttention": {"unique_prefixes": 100, "total_requests": 1000},
    },
    "photonics": {
        "TopoOpt":     {"gpus": 128, "dp": 16, "pp": 4},
        "SiP-ML":      {"gpus": 64, "wavelengths": 8},
        "TACCL":       {"gpus": 128, "topology": "torus"},
        "Rail-only":   {"gpus_per_rail": 32, "rails": 8},
        "Jupiter OCS": {"blocks": 32, "skew": 2.0},
    },
}


def get_inputs(theme: str, paper: str) -> dict:
    """Representative inputs used when delegating execution to an LLM."""
    return _INPUTS[theme][paper]


def get_source(theme: str, paper: str) -> dict:
    """Return the concatenated source of every symbol that backs `paper`.

    Raises KeyError if theme/paper unknown — caller maps to HTTP 404.
    """
    mod, symbols = _MAP[theme][paper]
    module_path = inspect.getsourcefile(mod) or mod.__name__
    parts: list[str] = []
    for name in symbols:
        obj = getattr(mod, name)
        src = inspect.getsource(obj)
        try:
            start_line = inspect.getsourcelines(obj)[1]
        except OSError:
            start_line = 0
        parts.append(f"# --- {name}  ({module_path.split('/')[-1]}:{start_line}) ---\n{src}")
    return {
        "theme": theme,
        "paper": paper,
        "module": module_path,
        "symbols": symbols,
        "source": "\n\n".join(parts),
        "language": "python",
    }


def list_papers(theme: str) -> list[str]:
    return sorted(_MAP[theme].keys())
