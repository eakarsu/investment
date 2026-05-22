// Thin fetch helpers. All requests are same-origin via next.config.mjs rewrites
// which forward /api/* to the FastAPI backend on :8080.
//
// Bearer-token auth: store the JWT in localStorage under "investment.jwt".
// The login page (/login) writes it; every request below reads it.

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

// ---------------- Auth ----------------

const TOKEN_KEY = "investment.jwt";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(TOKEN_KEY);
}

function authHeaders(): HeadersInit {
  const t = getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

async function j<T>(url: string): Promise<T> {
  const r = await fetch(url, { cache: "no-store", headers: { ...authHeaders() } });
  if (r.status === 401 && typeof window !== "undefined") {
    window.location.href = "/login";
    throw new Error("Unauthorized — redirecting to login.");
  }
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} — ${url}`);
  return r.json();
}

async function jpost<T>(url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (r.status === 401 && typeof window !== "undefined") {
    window.location.href = "/login";
    throw new Error("Unauthorized — redirecting to login.");
  }
  if (!r.ok) {
    const txt = await r.text().catch(() => "");
    throw new Error(`${r.status} ${r.statusText} — ${txt.slice(0, 240) || url}`);
  }
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
  source_hash?: string;
  seed?: number;
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

export type Paged<T> = {
  data: T[];
  pagination: { page: number; page_size: number; total_items: number; total_pages: number };
};

export const api = {
  // ---- auth ----
  login: async (username: string, password: string) => {
    const r = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (!r.ok) {
      const txt = await r.text().catch(() => "");
      throw new Error(txt || `${r.status} ${r.statusText}`);
    }
    const out = await r.json();
    setToken(out.access_token);
    return out;
  },
  register: async (username: string, email: string, password: string) => {
    const r = await fetch("/api/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, email, password }),
    });
    if (!r.ok) {
      const txt = await r.text().catch(() => "");
      throw new Error(txt || `${r.status} ${r.statusText}`);
    }
    const out = await r.json();
    setToken(out.access_token);
    return out;
  },
  me: () => j<{ id: number; username: string; email: string }>("/api/auth/me"),
  logout: () => {
    clearToken();
    if (typeof window !== "undefined") window.location.href = "/login";
  },

  // ---- existing endpoints ----
  overview: () => j<Overview>("/api/overview"),
  source: (theme: string, paper: string) =>
    j<PaperSource>(`/api/source/${theme}/${encodeURIComponent(paper)}`),
  runPaper: (theme: string, paper: string, body?: object) =>
    jpost<PaperRun>(`/api/run/${theme}/${encodeURIComponent(paper)}`, body),
  narratePaper: (theme: string, paper: string, body?: object) =>
    jpost<PaperNarration>(`/api/narrate/${theme}/${encodeURIComponent(paper)}`, body),
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

  // ---- NEW FEATURE endpoints ----
  backtestHbm: (body: object) => jpost<any>("/api/backtest/hbm", body),
  listBacktests: (page = 1, pageSize = 20) =>
    j<Paged<any>>(`/api/backtest?page=${page}&page_size=${pageSize}`),
  portfolioWhatIf: (body: object) => jpost<any>("/api/portfolio/whatif", body),
  leaderboard: (theme: string, page = 1, pageSize = 20) =>
    j<any>(`/api/leaderboard/${theme}?page=${page}&page_size=${pageSize}`),
  priceLastRefresh: () => j<any>("/api/portfolio/value/last-refresh"),
  reproducibility: (theme: string, paper: string) =>
    `/api/reproducibility/${theme}/${encodeURIComponent(paper)}`,
  listAiResults: (page = 1, pageSize = 20, feature?: string) =>
    j<Paged<any>>(`/api/ai-results?page=${page}&page_size=${pageSize}${feature ? `&feature=${feature}` : ""}`),
  aiResultsSummary: () => j<any>("/api/ai-results/summary"),
  taxLotDrift: () => j<any>("/api/tax-lot-drift"),
};
