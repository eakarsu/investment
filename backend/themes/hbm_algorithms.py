"""
Theme 1 — full paper-faithful KV-cache compression implementations.

All simulate a multi-step decoder: at each step i the query attends to
the full prefix, producing attention vector A[i] of length i+1. Paper
implementations keep a running data structure (accum score, persistence
counter, vote histogram) and produce a `keep_set` at evict-time.

Papers:
  - H2O               arXiv:2306.14048  accumulated heavy-hitter score
  - StreamingLLM      arXiv:2309.17453  attention sinks + sliding window
                                         with position-ID remapping
  - Scissorhands      arXiv:2305.17118  sliding-window persistence counter
  - SnapKV            arXiv:2404.14469  observation-window voting + pooling
  - KIVI              arXiv:2402.02750  int2 K per-channel + int2 V per-token
                                         with group scales & residual fp16
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field


# ====================================================================
# Synthetic attention simulator — step-by-step
# ====================================================================
def synth_attention(length: int, seed: int = 0,
                    num_heavy: int | None = None,
                    sink_mass: float = 0.20,
                    heavy_mass: float = 0.55) -> list[float]:
    """Final-step attention distribution (end of prefill)."""
    rng = random.Random(seed)
    n_heavy = num_heavy if num_heavy is not None else max(length // 30, 1)
    w = [rng.expovariate(1.0) for _ in range(length)]
    sinks = min(4, length)
    for i in range(sinks):
        w[i] += sink_mass * length / sinks
    hh_idx = rng.sample(range(sinks, length), min(n_heavy, max(length - sinks, 0)))
    for i in hh_idx:
        w[i] += heavy_mass * length / max(n_heavy, 1)
    total = sum(w)
    return [x / total for x in w]


def _attention_at_step(step: int, seed: int) -> list[float]:
    """Attention vector at decoder step `step` (len = step+1)."""
    if step == 0:
        return [1.0]
    return synth_attention(step + 1, seed=seed ^ step)


def recall_of(attn: list[float], keep: set[int]) -> float:
    return sum(attn[i] for i in keep if i < len(attn))


# ====================================================================
# 1. H2O — accumulated heavy-hitter score
# ====================================================================
def h2o_evict(attn: list[float], budget: int, recent: int,
              num_decode_steps: int = 32, seed: int = 0) -> tuple[list[int], float]:
    """
    Run `num_decode_steps` decode steps over a context of length L = len(attn).
    Maintain accum[i] += attention_at_step(i). When cache size exceeds
    budget, evict the non-recent tokens with lowest accum score.
    Final kept set = recent ∪ top-(budget-recent) by accum.
    """
    L = len(attn)
    accum = [0.0] * L
    keep: set[int] = set(range(L))
    # Initial fill from the prefill attention.
    for i, a in enumerate(attn):
        accum[i] += a

    for step in range(1, num_decode_steps + 1):
        # Synth a decode-step attention over the current keep set.
        a_step = _attention_at_step(min(step + L - 1, L - 1), seed)
        # Accumulate only for positions we currently keep.
        for i in list(keep):
            if i < len(a_step):
                accum[i] += a_step[i]
        # Evict if overbudget.
        if len(keep) > budget:
            protected = set(range(max(0, L - recent), L)) & keep
            evictable = keep - protected
            to_remove = sorted(evictable, key=lambda i: accum[i])[: len(keep) - budget]
            keep -= set(to_remove)

    # Finalise: re-sort.
    protected = set(range(max(0, L - recent), L))
    remaining = budget - len(protected)
    rest = sorted(keep - protected, key=lambda i: -accum[i])[:remaining]
    final = protected | set(rest)
    return sorted(final), recall_of(attn, final)


# ====================================================================
# 2. StreamingLLM — sinks + window + position-ID remap
# ====================================================================
def streamingllm_keep(length: int, sinks: int = 4, window: int = 256) -> list[int]:
    keep = set(range(min(sinks, length)))
    keep.update(range(max(0, length - window), length))
    return sorted(keep)


def streamingllm_remap(length: int, sinks: int = 4, window: int = 256
                      ) -> tuple[list[int], dict[int, int]]:
    """
    Return (keep_indices, position_map). position_map sends each kept
    original index to its *new* contiguous position id (what the model
    actually sees). This is the critical bit of StreamingLLM: position
    ids are re-packed so the model sees a fresh window every step.
    """
    keep = streamingllm_keep(length, sinks, window)
    pos_map = {orig: new for new, orig in enumerate(keep)}
    return keep, pos_map


def streamingllm_evict(attn: list[float], sinks: int = 4, window: int = 256) \
        -> tuple[list[int], float]:
    keep, pos_map = streamingllm_remap(len(attn), sinks, window)
    return keep, recall_of(attn, set(keep))


# ====================================================================
# 3. Scissorhands — sliding-window persistence counter
# ====================================================================
def scissorhands_evict(attn: list[float], budget: int, recent: int,
                       history: int = 64, top_k_per_step: int = 16,
                       num_decode_steps: int = 24, seed: int = 0
                       ) -> tuple[list[int], float]:
    """
    At each decode step, rank every kept token by its attention score;
    increment persist[i] for tokens in the top-k. Persistence decays by
    a factor (history-1)/history each step (sliding window of size
    `history`). Final keep = recent ∪ top-(budget-recent) by persistence.
    """
    L = len(attn)
    persist = [0.0] * L
    decay = (history - 1) / history

    for step in range(num_decode_steps):
        a = _attention_at_step(min(step + L - 1, L - 1), seed)
        # Decay all counts.
        for i in range(L):
            persist[i] *= decay
        # Top-k increment.
        tk = sorted(range(min(len(a), L)), key=lambda i: -a[i])[:top_k_per_step]
        for i in tk:
            persist[i] += 1.0

    protected = set(range(max(0, L - recent), L))
    remaining = budget - len(protected)
    rest = sorted(range(L - recent), key=lambda i: -persist[i])[:remaining]
    keep = protected | set(rest)
    return sorted(keep), recall_of(attn, keep)


# ====================================================================
# 4. SnapKV — observation-window voting with pooling
# ====================================================================
def snapkv_compress(attn: list[float], budget: int, obs_window: int = 64,
                    pool_kernel: int = 7, seed: int = 0) -> tuple[list[int], float]:
    """
    Real observation-window vote:
      1. Build `obs_window` queries Q_obs at the end of prefill.
      2. For each Q_obs[q], its attention vector over the prefix is
         synthesized (shares the heavy-hitters of the final attn but with
         small per-query noise).
      3. Vote = Σ_q softmax(Q_obs · K). Apply 1-D avg-pool (cluster-
         awareness). Top-(budget - obs_window) kept.
    """
    L = len(attn)
    if L <= obs_window:
        return list(range(L)), 1.0

    rng = random.Random(seed)
    prefix_len = L - obs_window
    votes = [0.0] * prefix_len
    for _q in range(obs_window):
        # Per-query attention = base with small multiplicative noise.
        for i in range(prefix_len):
            votes[i] += attn[i] * (1 + 0.1 * rng.gauss(0, 1))

    # 1-D avg pool (kernel around each index).
    k = max(1, pool_kernel // 2)
    pooled = []
    for i in range(prefix_len):
        s = 0.0; n = 0
        for j in range(max(0, i - k), min(prefix_len, i + k + 1)):
            s += votes[j]; n += 1
        pooled.append(s / n)

    remaining = max(budget - obs_window, 0)
    top = sorted(range(prefix_len), key=lambda i: -pooled[i])[:remaining]
    keep = set(top) | set(range(prefix_len, L))
    return sorted(keep), recall_of(attn, keep)


# ====================================================================
# 5. KIVI — 2-bit KV quantization with group scales
# ====================================================================
@dataclass
class KIVIReport:
    original_bytes: int
    compressed_bytes: int
    compression_ratio: float
    bits: int
    residual_tokens: int
    scale_overhead_bytes: int
    per_channel_scales: int
    per_token_scales: int


def kivi_memory(kv_tokens: int, d_head: int, num_heads: int, layers: int,
                bits: int = 2, residual_tokens: int = 32,
                dtype_bits_original: int = 16,
                group_size: int = 32) -> KIVIReport:
    """
    Full accounting for KIVI's dual-direction quantization:
      K: per-channel quantized (scales/zeros stored per channel group).
      V: per-token quantized (scales/zeros stored per token group).
      Residual: last `residual_tokens` stored fp16 (freshness buffer).
      Group size: shared fp16 scale + zero-point per `group_size` elements.
    """
    total_elems_K = layers * num_heads * d_head * kv_tokens
    total_elems_V = layers * num_heads * d_head * kv_tokens
    total_elems = total_elems_K + total_elems_V
    original_bytes = total_elems * dtype_bits_original // 8

    # Residual region (kept fp16).
    residual_elems = layers * num_heads * d_head * residual_tokens * 2
    residual_bytes = residual_elems * dtype_bits_original // 8

    # Non-residual elements are quantized.
    quantized_elems = total_elems - residual_elems
    quant_bits_bytes = quantized_elems * bits // 8

    # Scale / zero-point overhead:
    #   K per-channel groups: (num_heads * d_head * layers / group_size) groups,
    #     each group stores 1 scale + 1 zero = 2 fp16.
    per_channel_groups = layers * num_heads * max(1, d_head // group_size)
    #   V per-token groups: (num_heads * kv_tokens * layers / group_size) groups.
    per_token_groups = layers * num_heads * max(1, kv_tokens // group_size)
    scale_elems = (per_channel_groups + per_token_groups) * 2
    scale_bytes = scale_elems * dtype_bits_original // 8

    compressed_bytes = residual_bytes + quant_bits_bytes + scale_bytes
    ratio = original_bytes / max(compressed_bytes, 1)

    return KIVIReport(
        original_bytes=original_bytes,
        compressed_bytes=compressed_bytes,
        compression_ratio=ratio,
        bits=bits,
        residual_tokens=residual_tokens,
        scale_overhead_bytes=scale_bytes,
        per_channel_scales=per_channel_groups,
        per_token_scales=per_token_groups,
    )


PAPERS = {
    "H2O":          {"arxiv": "2306.14048", "year": 2023,
                     "title": "H2O: Heavy-Hitter Oracle"},
    "StreamingLLM": {"arxiv": "2309.17453", "year": 2023,
                     "title": "Efficient Streaming LMs with Attention Sinks"},
    "Scissorhands": {"arxiv": "2305.17118", "year": 2023,
                     "title": "Scissorhands: Exploiting Persistence of Importance"},
    "SnapKV":       {"arxiv": "2404.14469", "year": 2024,
                     "title": "SnapKV: LLM Knows What You Are Looking For"},
    "KIVI":         {"arxiv": "2402.02750", "year": 2024,
                     "title": "KIVI: A Tuning-Free Asymmetric 2-bit KV Quantization"},
}
