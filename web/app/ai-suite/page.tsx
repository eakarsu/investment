"use client";

/**
 * AI Suite — UI for the new investment-backend features:
 *   1. HBM admission backtest
 *   2. Portfolio what-if overlay
 *   3. Algorithm leaderboard
 *   4. Price-cache freshness panel
 *   5. Reproducibility export download
 */

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

const THEMES = ["hbm", "networking", "energy", "inference", "photonics"] as const;

export default function AISuite() {
  return (
    <div className="space-y-6">
      <h2 className="text-xl font-semibold">AI Suite (NEW)</h2>
      <BacktestPanel />
      <WhatIfPanel />
      <LeaderboardPanel />
      <PriceFreshnessPanel />
      <ReproducibilityPanel />
      <AiUsageSummaryPanel />
    </div>
  );
}

// ---- AI usage summary (apply pass 4) ----
function AiUsageSummaryPanel() {
  const [out, setOut] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.aiResultsSummary();
      setOut(r);
    } catch (e: any) {
      const msg = String(e?.message || e);
      setError(msg.includes("503") ? `AI service unavailable: ${msg}` : msg);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  return (
    <div className="card">
      <h3 className="font-semibold">AI usage summary</h3>
      <p className="text-sm text-muted">
        Aggregated calls / errors / latency from <code>ai_results</code>. Read-only.
      </p>
      <button
        className="bg-accent text-bg px-3 py-1 rounded text-sm mt-2"
        disabled={busy}
        onClick={load}
      >
        {busy ? "loading…" : "refresh"}
      </button>
      {error && <div className="text-red-400 text-xs mt-2">{error}</div>}
      {out && (
        <div className="mt-3 text-sm">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
            <Stat label="Total calls" value={out.total_calls} />
            <Stat label="Errors" value={out.total_errors} />
            <Stat label="Error rate %" value={out.error_rate_pct} />
            <Stat label="Avg duration ms" value={out.avg_duration_ms} />
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-muted text-left">
                  <th className="py-1 pr-2">Feature</th>
                  <th className="py-1 pr-2">Calls</th>
                  <th className="py-1 pr-2">Errors</th>
                  <th className="py-1 pr-2">Avg ms</th>
                  <th className="py-1 pr-2">Last</th>
                </tr>
              </thead>
              <tbody>
                {(out.by_feature || []).map((bf: any) => (
                  <tr key={bf.feature} className="border-t border-muted/20">
                    <td className="py-1 pr-2 font-mono">{bf.feature}</td>
                    <td className="py-1 pr-2">{bf.calls}</td>
                    <td className="py-1 pr-2">{bf.errors}</td>
                    <td className="py-1 pr-2">{bf.avg_duration_ms}</td>
                    <td className="py-1 pr-2 text-muted">{bf.last_called_at || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: any }) {
  return (
    <div className="bg-bg/50 border border-muted/20 rounded p-2">
      <div className="text-[10px] text-muted uppercase tracking-wider">{label}</div>
      <div className="text-base font-semibold">{value ?? "—"}</div>
    </div>
  );
}

// ---- 1. Backtest ----
function BacktestPanel() {
  const [trace, setTrace] = useState(
    JSON.stringify(
      [
        { prompt_tokens: 2048, max_completion: 512, model: "llama-3-70b" },
        { prompt_tokens: 4096, max_completion: 256, model: "llama-3-70b" },
        { prompt_tokens: 8192, max_completion: 128, model: "llama-3-70b" },
      ],
      null,
      2,
    ),
  );
  const [capacity, setCapacity] = useState(80000);
  const [out, setOut] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      const parsed = JSON.parse(trace);
      const r = await api.backtestHbm({ trace: parsed, hbm_capacity_mb: Number(capacity) });
      setOut(r);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card">
      <h3 className="font-semibold">1. HBM admission backtest</h3>
      <p className="text-sm text-muted">
        Replay a request trace through the controller. Compare smart vs naive (admit-everything) baseline.
      </p>
      <textarea
        className="w-full bg-bg border border-muted/30 rounded p-2 mt-2 text-xs font-mono"
        rows={6}
        value={trace}
        onChange={(e) => setTrace(e.target.value)}
      />
      <div className="flex items-center gap-2 mt-2">
        <label className="text-xs text-muted">HBM capacity (MB)</label>
        <input
          type="number"
          value={capacity}
          onChange={(e) => setCapacity(Number(e.target.value))}
          className="bg-bg border border-muted/30 rounded px-2 py-1 text-sm w-32"
        />
        <button onClick={run} disabled={busy} className="bg-accent text-bg px-3 py-1.5 rounded text-sm font-medium">
          {busy ? "Running…" : "Run backtest"}
        </button>
      </div>
      {error && <div className="text-danger text-sm mt-2">{error}</div>}
      {out && (
        <pre className="bg-bg/40 mt-3 p-2 rounded text-xs overflow-x-auto">{JSON.stringify(out, null, 2)}</pre>
      )}
    </div>
  );
}

// ---- 2. What-if overlay ----
function WhatIfPanel() {
  const [theme, setTheme] = useState("networking");
  const [pct, setPct] = useState(5);
  const [tickers, setTickers] = useState("NVDA,AMD");
  const [out, setOut] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.portfolioWhatIf({
        theme,
        improvement_pct: pct,
        tickers_affected: tickers.split(",").map((t) => t.trim()).filter(Boolean),
      });
      setOut(r);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card">
      <h3 className="font-semibold">2. Portfolio what-if overlay</h3>
      <p className="text-sm text-muted">Apply a hypothetical theme improvement to selected tickers and ask the LLM for analysis.</p>
      <div className="grid grid-cols-3 gap-2 mt-2 text-sm">
        <select value={theme} onChange={(e) => setTheme(e.target.value)} className="bg-bg border border-muted/30 rounded px-2 py-1">
          {THEMES.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <input type="number" value={pct} onChange={(e) => setPct(Number(e.target.value))} className="bg-bg border border-muted/30 rounded px-2 py-1" />
        <input value={tickers} onChange={(e) => setTickers(e.target.value)} placeholder="NVDA,AMD" className="bg-bg border border-muted/30 rounded px-2 py-1" />
      </div>
      <button onClick={run} disabled={busy} className="mt-2 bg-accent text-bg px-3 py-1.5 rounded text-sm font-medium">
        {busy ? "Running…" : "Compute overlay"}
      </button>
      {error && <div className="text-danger text-sm mt-2">{error}</div>}
      {out && (
        <div className="mt-3 space-y-2">
          {out.analysis && <p className="text-sm italic">{out.analysis}</p>}
          <pre className="bg-bg/40 p-2 rounded text-xs overflow-x-auto">{JSON.stringify(out.overlay, null, 2)}</pre>
        </div>
      )}
    </div>
  );
}

// ---- 3. Leaderboard ----
function LeaderboardPanel() {
  const [theme, setTheme] = useState("networking");
  const [out, setOut] = useState<any>(null);
  const [page, setPage] = useState(1);

  useEffect(() => {
    api.leaderboard(theme, page, 10).then(setOut).catch((e) => setOut({ error: e.message }));
  }, [theme, page]);

  return (
    <div className="card">
      <h3 className="font-semibold">3. Leaderboard</h3>
      <div className="flex gap-2 mt-2 text-sm">
        <select value={theme} onChange={(e) => { setTheme(e.target.value); setPage(1); }} className="bg-bg border border-muted/30 rounded px-2 py-1">
          {THEMES.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
      </div>
      {out?.summary && <p className="italic text-sm mt-2">{out.summary}</p>}
      {out?.data?.length > 0 ? (
        <ul className="mt-2 space-y-1 text-sm">
          {out.data.map((r: any, i: number) => (
            <li key={r.paper} className="flex justify-between border-b border-muted/20 py-1">
              <span>#{(page - 1) * 10 + i + 1} <strong>{r.paper}</strong> · {r.arxiv}</span>
              <span className="text-accent">
                {r.avg_improvement_pct !== undefined
                  ? `${r.avg_improvement_pct}%`
                  : `recall ${r.avg_recall} · cr ${r.avg_compression_ratio}`}
              </span>
            </li>
          ))}
        </ul>
      ) : <p className="text-muted text-sm">no entries</p>}
      {out?.pagination?.total_pages > 1 && (
        <div className="flex gap-2 mt-2 text-xs">
          <button disabled={page <= 1} onClick={() => setPage(page - 1)} className="border border-muted/30 px-2 py-0.5 rounded disabled:opacity-50">prev</button>
          <span>page {page} of {out.pagination.total_pages}</span>
          <button disabled={page >= out.pagination.total_pages} onClick={() => setPage(page + 1)} className="border border-muted/30 px-2 py-0.5 rounded disabled:opacity-50">next</button>
        </div>
      )}
    </div>
  );
}

// ---- 4. Price freshness ----
function PriceFreshnessPanel() {
  const [out, setOut] = useState<any>(null);
  useEffect(() => {
    api.priceLastRefresh().then(setOut).catch((e) => setOut({ error: e.message }));
  }, []);
  return (
    <div className="card">
      <h3 className="font-semibold">4. Price-cache freshness</h3>
      <p className="text-sm text-muted">
        Alpha Vantage configured: <strong>{String(out?.alpha_vantage_configured)}</strong>
      </p>
      {out?.data?.length > 0 ? (
        <table className="text-xs w-full mt-2">
          <thead><tr><th>ticker</th><th>price</th><th>source</th><th>fetched</th></tr></thead>
          <tbody>
            {out.data.map((r: any) => (
              <tr key={r.ticker}>
                <td>{r.ticker}</td>
                <td className="text-right">${r.price.toFixed(2)}</td>
                <td>{r.source}</td>
                <td>{new Date(r.fetched_at).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : <p className="text-muted text-sm mt-2">no cache entries yet — run the price_refresh worker</p>}
    </div>
  );
}

// ---- 5. Reproducibility export ----
function ReproducibilityPanel() {
  const [theme, setTheme] = useState("hbm");
  const [paper, setPaper] = useState("H2O");
  return (
    <div className="card">
      <h3 className="font-semibold">5. Reproducibility export</h3>
      <p className="text-sm text-muted">Download a tar.gz with the exact source, default inputs, source hash, and a README.</p>
      <div className="flex gap-2 mt-2 text-sm">
        <select value={theme} onChange={(e) => setTheme(e.target.value)} className="bg-bg border border-muted/30 rounded px-2 py-1">
          {THEMES.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <input value={paper} onChange={(e) => setPaper(e.target.value)} className="bg-bg border border-muted/30 rounded px-2 py-1" />
        <a
          href={api.reproducibility(theme, paper)}
          target="_blank"
          rel="noreferrer"
          className="bg-accent text-bg px-3 py-1.5 rounded text-sm font-medium"
        >
          Download bundle
        </a>
      </div>
    </div>
  );
}
