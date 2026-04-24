"use client";
import { api } from "@/lib/api";
import { fmt } from "@/lib/format";
import { StatCard } from "@/components/stat-card";
import { AlgoSection } from "@/components/algo-section";
import { usePoll } from "@/components/use-poll";
import {
  Bar, BarChart, Cell, Pie, PieChart, ResponsiveContainer, Tooltip,
} from "recharts";

const PAL = ["#4ade80", "#60a5fa", "#f472b6", "#fbbf24", "#a78bfa", "#34d399", "#f87171"];

export default function InferencePage() {
  const s = usePoll(api.infStats);
  const r = usePoll(api.infRecs);
  const algos = usePoll(api.infAlgos);
  if (s.loading) return <div className="text-muted">loading…</div>;
  if (s.error) return <div className="text-danger">{s.error}</div>;
  if (!s.data) return null;
  const stats = s.data;
  const recs = r.data?.recommendations ?? [];

  const lat = [
    { name: "TTFT p50", ms: stats.ttft_ms_p50 },
    { name: "TTFT p95", ms: stats.ttft_ms_p95 },
    { name: "Total p50", ms: stats.latency_ms_p50 },
    { name: "Total p95", ms: stats.latency_ms_p95 },
  ];
  const byModel = Object.entries(stats.by_model).map(([m, v]) => ({
    name: m, value: v.cost, count: v.count,
  }));

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold">4. Inference auto-tuner</h1>
        <p className="text-muted text-sm">
          Telemetry summary + paper-derived recommendations. Rules fire when observed
          traffic matches the shape each paper targets.
        </p>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Requests" value={fmt.int(stats.requests)} />
        <StatCard label="Tokens"
                  value={fmt.int(stats.prompt_tokens + stats.completion_tokens)}
                  sub={`${fmt.int(stats.prompt_tokens)} in · ${fmt.int(stats.completion_tokens)} out`} />
        <StatCard label="Total cost" value={fmt.money2(stats.total_cost_usd)} />
        <StatCard label="p95 latency"
                  value={fmt.ms(stats.latency_ms_p95)}
                  sub={`p50 ${fmt.ms(stats.latency_ms_p50)}`} />
      </div>

      <div className="grid md:grid-cols-5 gap-6">
        <section className="card md:col-span-3">
          <h3>Latency distribution</h3>
          <div className="h-56 -mx-2">
            <ResponsiveContainer>
              <BarChart data={lat}>
                <Tooltip
                  contentStyle={{ background: "#141820", border: "1px solid #222831", borderRadius: 6 }}
                />
                <Bar dataKey="ms" fill="#4ade80" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </section>
        <section className="card md:col-span-2">
          <h3>Cost share by model</h3>
          <div className="h-56 -mx-2">
            <ResponsiveContainer>
              <PieChart>
                <Pie data={byModel} dataKey="value" nameKey="name"
                     innerRadius={36} outerRadius={72}>
                  {byModel.map((_, i) => <Cell key={i} fill={PAL[i % PAL.length]} />)}
                </Pie>
                <Tooltip
                  contentStyle={{ background: "#141820", border: "1px solid #222831", borderRadius: 6 }}
                  formatter={(v: number) => fmt.money(v)}
                />
              </PieChart>
            </ResponsiveContainer>
          </div>
        </section>
      </div>

      <section className="card">
        <h3>Recommendations</h3>
        {recs.length === 0 ? (
          <div className="text-muted text-sm">No rules have fired yet.</div>
        ) : (
          <div className="space-y-3">
            {recs.map((rec, i) => (
              <article key={i} className="border-l-4 border-l-accent pl-3">
                <div className="flex items-baseline gap-3 mb-1">
                  <span className="badge">{rec.module}</span>
                  <div className="font-semibold">{rec.title}</div>
                  <span className="ml-auto text-accent text-xs font-semibold">{rec.expected_gain}</span>
                </div>
                <p className="text-sm">{rec.detail}</p>
                <div className="text-xs text-muted mt-1 italic">{rec.paper}</div>
              </article>
            ))}
          </div>
        )}
      </section>

      <AlgoSection
        title="Paper-derived inference algorithms"
        description="Faithful implementations of speculative decoding (Medusa, EAGLE), chunked-prefill scheduling (Sarathi-Serve), tier routing (RouteLLM), prompt compression (LLMLingua-2), and prefix-tree KV reuse (RadixAttention)."
        bundle={algos.data}
        theme="inference"
      />

      <section className="card">
        <h3>By model</h3>
        <table>
          <thead>
            <tr>
              <th>Model</th>
              <th className="text-right">Requests</th>
              <th className="text-right">Prompt tokens</th>
              <th className="text-right">Cost</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(stats.by_model)
              .sort((a, b) => b[1].cost - a[1].cost)
              .map(([m, v]) => (
                <tr key={m}>
                  <td><code className="text-accent">{m}</code></td>
                  <td className="text-right tabular-nums">{fmt.int(v.count)}</td>
                  <td className="text-right tabular-nums">{fmt.int(v.prompt_tokens)}</td>
                  <td className="text-right tabular-nums">{fmt.money(v.cost)}</td>
                </tr>
              ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
