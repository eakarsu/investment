// Thin fetch helpers. All requests are same-origin via next.config.mjs rewrites
// which forward /api/* to the FastAPI backend on :8080.

export type OverviewTheme = { id: string; name: string; rows: number; kpi: string };
export type Overview = { themes: OverviewTheme[] };

export type HBMDecision = {
  id: number; request_id: string; model: string; prompt_tokens: number;
  kv_mb: number; pressure_pct: number; decision: "admit" | "evict" | "reject";
  evicted: number; reason: string; created_at: string | null;
};
export type HBMDecisions = {
  decisions: HBMDecision[];
  summary: { total: number; admit_pct?: number; evict_pct?: number; reject_pct?: number;
             avg_kv_mb?: number; avg_pressure_pct?: number };
};

export type HBMAlgoRun = {
  id: number; paper: string; arxiv: string; scenario: string;
  prompt_tokens: number; budget_tokens: number;
  recall: number; compression_ratio: number; notes: string;
  created_at: string | null;
};
export type HBMAlgorithms = {
  papers: Record<string, { arxiv: string; year: number; title: string }>;
  runs: HBMAlgoRun[];
};

export type NetPlan = {
  id: number; model: string; params_b: number; cluster: string; gpus: number;
  nvlink_gbps: number; inter_gbps: number; tp: number; pp: number; dp: number;
  step_ms: number; comm_ms: number; comm_frac: number; created_at: string | null;
};

export type Region = {
  region: string; price_usd_per_kwh: number; carbon_gco2_per_kwh: number;
  pue: number; latency_ms: number; available_gpus: number; grid_queue_months: number;
};
export type EnergyDecision = {
  id: number; tokens: number; sla_ms: number; chosen: string;
  cost_usd: number; carbon_g: number; savings_pct: number;
  reason: string; created_at: string | null;
};

export type InfStats = {
  requests: number; prompt_tokens: number; completion_tokens: number;
  total_cost_usd: number;
  latency_ms_p50: number; latency_ms_p95: number;
  ttft_ms_p50: number; ttft_ms_p95: number;
  by_model: Record<string, { count: number; prompt_tokens: number; cost: number }>;
};
export type InfRec = {
  module: string; title: string; detail: string; paper: string; expected_gain: string;
};

export type AlgoRun = {
  id: number; paper: string; arxiv: string; scenario: string;
  metric_name: string; metric_value: number; baseline_value: number;
  improvement_pct: number; notes: string; created_at: string | null;
};
export type AlgoBundle = {
  papers: Record<string, { arxiv: string; year: number; title: string }>;
  runs: AlgoRun[];
};

export type PhotonicScenario = {
  id: number; name: string; num_gpus: number; optical_fraction: number;
  optical_gbps: number; copper_gbps: number; dag_size: number;
  baseline_ms: number; optimized_ms: number; speedup_pct: number; created_at: string | null;
};

async function j<T>(url: string): Promise<T> {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} — ${url}`);
  return r.json();
}

export type PaperSource = {
  theme: string; paper: string; module: string;
  symbols: string[]; source: string; language: string;
};

export type PaperRun = {
  theme: string; paper: string; elapsed_ms: number;
  result: Record<string, any>;
  model?: string;
  executed_by?: string;
};

export type ChartSpec = {
  type: "bar" | "line";
  title: string;
  x_label: string;
  y_label: string;
  series: { name: string; data: { x: string | number; y: number }[] }[];
};

export type PaperNarration = {
  theme: string; paper: string;
  run: { elapsed_ms: number; result: Record<string, any> };
  narration_md: string;
  chart: ChartSpec | null;
  model: string;
  llm_elapsed_ms: number;
};

async function jpost<T>(url: string): Promise<T> {
  const r = await fetch(url, { method: "POST", cache: "no-store" });
  if (!r.ok) {
    const txt = await r.text().catch(() => "");
    throw new Error(`${r.status} ${r.statusText} — ${txt.slice(0, 240) || url}`);
  }
  return r.json();
}

export const api = {
  overview: () => j<Overview>("/api/overview"),
  source: (theme: string, paper: string) =>
    j<PaperSource>(`/api/source/${theme}/${encodeURIComponent(paper)}`),
  runPaper: (theme: string, paper: string) =>
    jpost<PaperRun>(`/api/run/${theme}/${encodeURIComponent(paper)}`),
  narratePaper: (theme: string, paper: string) =>
    jpost<PaperNarration>(`/api/narrate/${theme}/${encodeURIComponent(paper)}`),
  hbm:        () => j<HBMDecisions>("/api/themes/hbm/decisions"),
  hbmAlgos:   () => j<HBMAlgorithms>("/api/themes/hbm/algorithms"),
  netPlans:   () => j<{ plans: NetPlan[] }>("/api/themes/networking/plans"),
  netAlgos:   () => j<AlgoBundle>("/api/themes/networking/algorithms"),
  regions:    () => j<{ regions: Region[] }>("/api/themes/energy/regions"),
  enDecisions:() => j<{ decisions: EnergyDecision[] }>("/api/themes/energy/decisions"),
  enAlgos:    () => j<AlgoBundle>("/api/themes/energy/algorithms"),
  infStats:   () => j<InfStats>("/api/themes/inference/stats"),
  infRecs:    () => j<{ recommendations: InfRec[] }>("/api/themes/inference/recs"),
  infAlgos:   () => j<AlgoBundle>("/api/themes/inference/algorithms"),
  photonics:  () => j<{ scenarios: PhotonicScenario[] }>("/api/themes/photonics/scenarios"),
  phAlgos:    () => j<AlgoBundle>("/api/themes/photonics/algorithms"),
};
