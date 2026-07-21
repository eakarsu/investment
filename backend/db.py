"""
SQLAlchemy async setup + models for all 5 themes.

Tables:
  hbm_decisions        — theme 1 (HBM admission + eviction)
  network_plans        — theme 2 (parallelism / placement planning)
  energy_regions       — theme 3 (DC regions with price/carbon/latency)
  energy_decisions     — theme 3 (routing outcomes)
  inference_runs       — theme 4 (inference telemetry, drives recs)
  photonic_scenarios   — theme 5 (optical scheduling scenarios)
"""

from datetime import datetime
from sqlalchemy import (
    Integer, String, Float, Boolean, DateTime, Text, JSON, func,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .config import settings


class Base(DeclarativeBase):
    pass


# ---------- AI results / new-feature tables ----------
class AiResult(Base):
    """Generic AI run audit log — one row per LLM call across all features."""
    __tablename__ = "ai_results"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    feature: Mapped[str] = mapped_column(String(64), index=True)
    user_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    ref_type: Mapped[str | None] = mapped_column(String(48), nullable=True)
    ref_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model: Mapped[str] = mapped_column(String(96))
    input: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    output: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Backtest(Base):
    """Backtest run for the HBM admission controller (and other algorithms).
    Stores the input request trace + result metrics.
    """
    __tablename__ = "backtests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    theme: Mapped[str] = mapped_column(String(32), index=True)
    paper: Mapped[str] = mapped_column(String(48))
    trace: Mapped[list | dict] = mapped_column(JSON)            # input request trace
    metrics: Mapped[dict] = mapped_column(JSON)                 # admit / evict / oom / ...
    baseline_metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    improvement_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PriceCacheEntry(Base):
    """Persistent price cache for the scheduled price-refresh worker.
    Replaces the in-process dict so multiple workers share state.
    """
    __tablename__ = "price_cache"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    price: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------- theme 1 ----------
class HBMDecision(Base):
    __tablename__ = "hbm_decisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(64))
    prompt_tokens: Mapped[int] = mapped_column(Integer)
    max_completion: Mapped[int] = mapped_column(Integer)
    kv_mb_projected: Mapped[float] = mapped_column(Float)
    hbm_pressure_pct: Mapped[float] = mapped_column(Float)
    decision: Mapped[str] = mapped_column(String(16))  # admit | evict | reject
    evicted_count: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AlgoRunMixin:
    """
    Shared columns for every paper-derived algorithm run, one table per theme.
    Keeps result logging uniform (paper, scenario, metric, baseline, improvement).
    """
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    paper: Mapped[str] = mapped_column(String(48))
    arxiv: Mapped[str] = mapped_column(String(24))
    scenario: Mapped[str] = mapped_column(String(96))
    metric_name: Mapped[str] = mapped_column(String(64))
    metric_value: Mapped[float] = mapped_column(Float)
    baseline_value: Mapped[float] = mapped_column(Float, default=0)
    improvement_pct: Mapped[float] = mapped_column(Float, default=0)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NetworkAlgoRun(AlgoRunMixin, Base):
    __tablename__ = "network_algo_runs"


class EnergyAlgoRun(AlgoRunMixin, Base):
    __tablename__ = "energy_algo_runs"


class InferenceAlgoRun(AlgoRunMixin, Base):
    __tablename__ = "inference_algo_runs"


class PhotonicsAlgoRun(AlgoRunMixin, Base):
    __tablename__ = "photonics_algo_runs"


class HBMAlgoRun(Base):
    """Results of running a paper-derived KV-cache compression algorithm."""
    __tablename__ = "hbm_algo_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    paper: Mapped[str] = mapped_column(String(32))       # e.g. "H2O"
    arxiv: Mapped[str] = mapped_column(String(24))
    scenario: Mapped[str] = mapped_column(String(64))
    prompt_tokens: Mapped[int] = mapped_column(Integer)
    budget_tokens: Mapped[int] = mapped_column(Integer)
    recall: Mapped[float] = mapped_column(Float)          # attention-mass preserved
    compression_ratio: Mapped[float] = mapped_column(Float)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------- theme 2 ----------
class NetworkPlan(Base):
    __tablename__ = "network_plans"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_name: Mapped[str] = mapped_column(String(64))
    params_b: Mapped[float] = mapped_column(Float)     # params in billions
    layers: Mapped[int] = mapped_column(Integer)
    hidden: Mapped[int] = mapped_column(Integer)
    cluster_name: Mapped[str] = mapped_column(String(64))
    gpus_total: Mapped[int] = mapped_column(Integer)
    gpus_per_node: Mapped[int] = mapped_column(Integer)
    nvlink_gbps: Mapped[float] = mapped_column(Float)
    inter_gbps: Mapped[float] = mapped_column(Float)
    tp: Mapped[int] = mapped_column(Integer)
    pp: Mapped[int] = mapped_column(Integer)
    dp: Mapped[int] = mapped_column(Integer)
    step_ms: Mapped[float] = mapped_column(Float)
    comm_ms: Mapped[float] = mapped_column(Float)
    comm_frac: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------- theme 3 ----------
class EnergyRegion(Base):
    __tablename__ = "energy_regions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    region: Mapped[str] = mapped_column(String(32), unique=True)
    price_usd_per_kwh: Mapped[float] = mapped_column(Float)
    carbon_gco2_per_kwh: Mapped[float] = mapped_column(Float)
    pue: Mapped[float] = mapped_column(Float)
    latency_ms: Mapped[float] = mapped_column(Float)
    available_gpus: Mapped[int] = mapped_column(Integer)
    grid_queue_months: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EnergyDecision(Base):
    __tablename__ = "energy_decisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_tokens: Mapped[int] = mapped_column(Integer)
    latency_sla_ms: Mapped[int] = mapped_column(Integer)
    chosen_region: Mapped[str] = mapped_column(String(32))
    cost_usd: Mapped[float] = mapped_column(Float)
    carbon_g: Mapped[float] = mapped_column(Float)
    savings_vs_worst_pct: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------- theme 4 ----------
class InferenceRun(Base):
    __tablename__ = "inference_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(64))
    prompt_tokens: Mapped[int] = mapped_column(Integer)
    completion_tokens: Mapped[int] = mapped_column(Integer)
    ttft_ms: Mapped[float] = mapped_column(Float)
    latency_ms: Mapped[float] = mapped_column(Float)
    cost_usd: Mapped[float] = mapped_column(Float)
    kv_reuse_pct: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------- theme 5 ----------
class PhotonicScenario(Base):
    __tablename__ = "photonic_scenarios"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64))
    num_gpus: Mapped[int] = mapped_column(Integer)
    optical_fraction: Mapped[float] = mapped_column(Float)  # 0..1
    optical_gbps: Mapped[float] = mapped_column(Float)
    copper_gbps: Mapped[float] = mapped_column(Float)
    dag_size: Mapped[int] = mapped_column(Integer)
    baseline_ms: Mapped[float] = mapped_column(Float)       # copper-only
    optimized_ms: Mapped[float] = mapped_column(Float)      # mixed w/ optical-aware scheduling
    speedup_pct: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------- engine / session ----------
engine = create_async_engine(settings.db_url, echo=False, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
