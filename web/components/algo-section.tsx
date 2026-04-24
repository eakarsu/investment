"use client";
import { useState } from "react";
import { api, type AlgoBundle, type PaperSource, type PaperRun, type PaperNarration } from "@/lib/api";
import { AlgoChart } from "./algo-chart";

export function AlgoSection({
  title, description, bundle, metricLabel = "Metric", theme,
}: {
  title: string;
  description: string;
  bundle: AlgoBundle | null | undefined;
  metricLabel?: string;
  theme: string;
}) {
  const [openPaper, setOpenPaper] = useState<string | null>(null);
  const [source, setSource] = useState<PaperSource | null>(null);
  const [run, setRun] = useState<PaperRun | null>(null);
  const [narr, setNarr] = useState<PaperNarration | null>(null);
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [explaining, setExplaining] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function toggle(paper: string) {
    if (openPaper === paper) {
      setOpenPaper(null); setSource(null); setRun(null); setNarr(null); return;
    }
    setOpenPaper(paper); setSource(null); setRun(null); setNarr(null);
    setErr(null); setLoading(true);
    try { setSource(await api.source(theme, paper)); }
    catch (e: any) { setErr(String(e?.message ?? e)); }
    finally { setLoading(false); }
  }

  async function runPaper() {
    if (!openPaper) return;
    // Clear any previous narration so the raw JSON panel shows.
    setRunning(true); setRun(null); setNarr(null); setErr(null);
    try { setRun(await api.runPaper(theme, openPaper)); }
    catch (e: any) { setErr(String(e?.message ?? e)); }
    finally { setRunning(false); }
  }

  async function explainPaper() {
    if (!openPaper) return;
    setExplaining(true); setNarr(null); setErr(null);
    try {
      const n = await api.narratePaper(theme, openPaper);
      setNarr(n);
      // Mirror the run panel with the same numbers the LLM saw.
      setRun({ theme, paper: openPaper,
               elapsed_ms: n.run.elapsed_ms, result: n.run.result });
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally { setExplaining(false); }
  }

  if (!bundle || bundle.runs.length === 0) return null;
  const papers: string[] = [];
  for (const r of bundle.runs) if (!papers.includes(r.paper)) papers.push(r.paper);

  return (
    <section className="card">
      <h3>{title}</h3>
      <p className="text-muted text-sm mb-3">{description}</p>
      <p className="text-xs text-muted mb-3">
        Click a paper to see its real Python implementation, then <b>Run</b> (local Python)
        or <b>✨ Explain</b> (runs locally, then narrates + charts via OpenRouter).
      </p>

      <div className="flex flex-wrap gap-2 mb-3">
        {papers.map((p) => (
          <button key={p} onClick={() => toggle(p)}
                  className={`badge ${openPaper === p ? "accent" : "good"}`}
                  style={{ cursor: "pointer" }}>
            {p}{openPaper === p ? " ▾" : " ▸"}
          </button>
        ))}
      </div>

      {openPaper && (
        <div className="mb-4 border border-border rounded p-3 bg-black/30">
          {loading && <p className="text-muted text-xs">Loading source…</p>}
          {err && <p className="text-danger text-xs mb-2">{err}</p>}
          {source && (
            <>
              <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
                <div className="text-xs text-muted">
                  <span className="text-accent font-semibold">{source.paper}</span>{" — "}
                  <code>{source.module.split("/").slice(-2).join("/")}</code>{" · "}
                  {source.symbols.map((s, i) => (
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
                <code>{source.source}</code>
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
            <th>Paper</th>
            <th>arXiv</th>
            <th>Scenario</th>
            <th className="text-right">{metricLabel}</th>
            <th className="text-right">Baseline</th>
            <th className="text-right">Δ</th>
            <th>Notes</th>
          </tr>
        </thead>
        <tbody>
          {bundle.runs.map((r) => (
            <tr key={r.id}>
              <td>
                <button onClick={() => toggle(r.paper)}
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
              <td className="text-right tabular-nums">
                {r.metric_value.toLocaleString(undefined, { maximumFractionDigits: 2 })}
                <span className="text-muted text-xs"> {r.metric_name}</span>
              </td>
              <td className="text-right tabular-nums text-muted">
                {r.baseline_value.toLocaleString(undefined, { maximumFractionDigits: 2 })}
              </td>
              <td className={`text-right tabular-nums ${r.improvement_pct >= 0 ? "text-accent" : "text-danger"}`}>
                {r.improvement_pct >= 0 ? "+" : ""}{r.improvement_pct.toFixed(1)}%
              </td>
              <td className="text-xs text-muted">{r.notes}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
