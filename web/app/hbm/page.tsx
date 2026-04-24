"use client";
import { useState } from "react";
import { api, type PaperSource, type PaperRun, type PaperNarration } from "@/lib/api";
import { AlgoChart } from "@/components/algo-chart";
import { fmt } from "@/lib/format";
import { StatCard } from "@/components/stat-card";
import { usePoll } from "@/components/use-poll";
import {
  Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";

const COLOR: Record<string, string> = {
  admit: "#4ade80",
  evict: "#fbbf24",
  reject: "#f87171",
};

export default function HBMPage() {
  const { data, error, loading } = usePoll(api.hbm);
  const algos = usePoll(api.hbmAlgos);
  const [openPaper, setOpenPaper] = useState<string | null>(null);
  const [src, setSrc] = useState<PaperSource | null>(null);
  const [run, setRun] = useState<PaperRun | null>(null);
  const [narr, setNarr] = useState<PaperNarration | null>(null);
  const [srcErr, setSrcErr] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [explaining, setExplaining] = useState(false);
  async function togglePaper(p: string) {
    if (openPaper === p) {
      setOpenPaper(null); setSrc(null); setRun(null); setNarr(null); return;
    }
    setOpenPaper(p); setSrc(null); setRun(null); setNarr(null); setSrcErr(null);
    try { setSrc(await api.source("hbm", p)); }
    catch (e: any) { setSrcErr(String(e?.message ?? e)); }
  }
  async function runPaper() {
    if (!openPaper) return;
    // Clear any previous narration so the raw JSON panel shows.
    setRunning(true); setRun(null); setNarr(null); setSrcErr(null);
    try { setRun(await api.runPaper("hbm", openPaper)); }
    catch (e: any) { setSrcErr(String(e?.message ?? e)); }
    finally { setRunning(false); }
  }
  async function explainPaper() {
    if (!openPaper) return;
    setExplaining(true); setNarr(null); setSrcErr(null);
    try {
      const n = await api.narratePaper("hbm", openPaper);
      setNarr(n);
      setRun({ theme: "hbm", paper: openPaper,
               elapsed_ms: n.run.elapsed_ms, result: n.run.result });
    } catch (e: any) { setSrcErr(String(e?.message ?? e)); }
    finally { setExplaining(false); }
  }
  if (loading) return <div className="text-muted">loading…</div>;
  if (error)   return <div className="text-danger">{error}</div>;
  if (!data)   return null;

  const rows = data.decisions;
  const sum = data.summary;
  const chart = rows.slice().reverse().map((r, i) => ({
    i, pressure: r.pressure_pct, kv: r.kv_mb, decision: r.decision,
  }));

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold">1. HBM-scarcity optimizer</h1>
        <p className="text-muted text-sm">
          Per-request admission control. Projects KV-cache footprint, simulates pressure
          against an 80 GB/GPU HBM budget, and picks admit / evict-LRU / reject.
        </p>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Decisions" value={fmt.int(sum.total ?? 0)} />
        <StatCard label="Admit" value={`${sum.admit_pct ?? 0}%`} sub="fit without eviction" />
        <StatCard label="Evict" value={`${sum.evict_pct ?? 0}%`} sub="forced LRU eviction" />
        <StatCard label="Reject" value={`${sum.reject_pct ?? 0}%`} sub="over budget" />
      </div>

      <section className="card">
        <h3>KV pressure over the last {rows.length} requests</h3>
        <div className="h-64 -mx-2">
          <ResponsiveContainer>
            <BarChart data={chart}>
              <CartesianGrid stroke="#222831" strokeDasharray="3 3" />
              <XAxis dataKey="i" stroke="#8a93a0" fontSize={11} />
              <YAxis stroke="#8a93a0" fontSize={11} unit="%" />
              <Tooltip
                contentStyle={{ background: "#141820", border: "1px solid #222831", borderRadius: 6 }}
                labelFormatter={(i) => `#${i}`}
                formatter={(v: number, _n, item: any) =>
                  [`${v.toFixed(1)}%`, `pressure (${item.payload.decision})`]}
              />
              <Bar dataKey="pressure" radius={[3,3,0,0]}
                   shape={(props: any) => {
                     const { x, y, width, height, payload } = props;
                     return <rect x={x} y={y} width={width} height={height}
                                  fill={COLOR[payload.decision] ?? "#4ade80"} rx={3} />;
                   }} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </section>

      {algos.data && algos.data.runs.length > 0 && (
        <section className="card">
          <h3>Paper-derived KV-cache compression algorithms</h3>
          <p className="text-muted text-sm mb-3">
            Each row is a faithful implementation of a specific paper, run on
            a synthetic attention distribution. <code>Recall</code> = fraction
            of total attention mass preserved by the kept tokens (quality
            proxy). <code>Ratio</code> = compression ratio over a bf16 baseline.
          </p>
          <p className="text-xs text-muted mb-2">
            Click any paper name to reveal the real Python implementation.
          </p>
          {openPaper && (
            <div className="mb-4 border border-border rounded p-3 bg-black/30">
              {srcErr && <p className="text-danger text-xs">{srcErr}</p>}
              {!src && !srcErr && <p className="text-muted text-xs">Loading source…</p>}
              {src && (
                <>
                  <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
                    <div className="text-xs text-muted">
                      <span className="text-accent font-semibold">{src.paper}</span>{" — "}
                      <code>{src.module.split("/").slice(-2).join("/")}</code>{" · "}
                      symbols: {src.symbols.map((s, i) => (
                        <code key={s} className="text-accent">{i > 0 ? ", " : ""}{s}</code>
                      ))}
                    </div>
                    <div className="flex gap-2">
                      <button onClick={runPaper} disabled={running || explaining}
                              className="badge accent"
                              style={{ cursor: running ? "wait" : "pointer" }}>
                        {running ? "Running…" : "▶ Run"}
                      </button>
                      <button onClick={explainPaper} disabled={running || explaining}
                              className="badge accent"
                              style={{ cursor: explaining ? "wait" : "pointer" }}>
                        {explaining ? "Asking OpenRouter…" : "✨ Explain & visualize"}
                      </button>
                    </div>
                  </div>
                  <pre className="text-xs overflow-auto max-h-[320px] leading-snug mb-3">
                    <code>{src.source}</code>
                  </pre>
                  {narr?.chart && (
                    <div className="border border-border rounded p-2 bg-black/40 mb-3">
                      <AlgoChart spec={narr.chart} />
                    </div>
                  )}
                  {narr?.narration_md && (
                    <div className="border border-border rounded p-3 bg-black/40 mb-3">
                      <div className="text-xs text-muted mb-2">
                        Narration by <span className="text-accent">{narr.model}</span>
                        {" "}(LLM {narr.llm_elapsed_ms.toFixed(0)} ms,
                        {" "}local run {narr.run.elapsed_ms.toFixed(1)} ms)
                      </div>
                      <div className="text-sm whitespace-pre-wrap leading-relaxed">
                        {narr.narration_md}
                      </div>
                    </div>
                  )}
                  {run && !narr && (
                    <div className="border border-border rounded p-2 bg-black/40">
                      <div className="text-xs text-muted mb-1">
                        Result from <span className="text-accent">{run.model ?? "OpenRouter"}</span>
                        {" "}({run.elapsed_ms.toFixed(0)} ms)
                      </div>
                      <pre className="text-xs overflow-auto max-h-[300px] leading-snug">
                        <code>{JSON.stringify(run.result, null, 2)}</code>
                      </pre>
                    </div>
                  )}
                </>
              )}
            </div>
          )}
          <table>
            <thead>
              <tr>
                <th>Paper</th><th>arXiv</th><th>Scenario</th>
                <th className="text-right">Prompt</th>
                <th className="text-right">Budget</th>
                <th className="text-right">Recall</th>
                <th className="text-right">Compression</th>
                <th>Notes</th>
              </tr>
            </thead>
            <tbody>
              {algos.data.runs.map((r) => (
                <tr key={r.id}>
                  <td>
                    <button onClick={() => togglePaper(r.paper)}
                            className={`badge ${openPaper === r.paper ? "accent" : "good"}`}
                            style={{ cursor: "pointer" }}>
                      {r.paper}
                    </button>
                  </td>
                  <td className="text-xs text-muted">
                    <a className="underline decoration-dotted hover:text-accent"
                       href={`https://arxiv.org/abs/${r.arxiv}`} target="_blank" rel="noreferrer">
                      {r.arxiv}
                    </a>
                  </td>
                  <td className="text-xs"><code className="text-accent">{r.scenario}</code></td>
                  <td className="text-right tabular-nums">{fmt.int(r.prompt_tokens)}</td>
                  <td className="text-right tabular-nums">{fmt.int(r.budget_tokens)}</td>
                  <td className="text-right tabular-nums">{(r.recall * 100).toFixed(1)}%</td>
                  <td className="text-right tabular-nums">{r.compression_ratio.toFixed(1)}×</td>
                  <td className="text-xs text-muted">{r.notes}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      <section className="card">
        <h3>Recent decisions</h3>
        <table>
          <thead>
            <tr>
              <th>Model</th><th className="text-right">Prompt</th>
              <th className="text-right">KV MiB</th>
              <th className="text-right">Pressure</th>
              <th>Decision</th>
              <th className="text-right">Evicted</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td><code className="text-accent">{r.model}</code></td>
                <td className="text-right tabular-nums">{fmt.int(r.prompt_tokens)}</td>
                <td className="text-right tabular-nums">{fmt.int(r.kv_mb)}</td>
                <td className="text-right tabular-nums">{r.pressure_pct}%</td>
                <td>
                  <span className={`badge ${r.decision === "admit" ? "good" :
                                              r.decision === "evict" ? "warn" : "bad"}`}>
                    {r.decision}
                  </span>
                </td>
                <td className="text-right tabular-nums">{r.evicted}</td>
                <td className="text-xs text-muted">{r.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
