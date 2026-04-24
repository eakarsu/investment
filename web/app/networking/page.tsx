"use client";
import { api } from "@/lib/api";
import { fmt } from "@/lib/format";
import { StatCard } from "@/components/stat-card";
import { AlgoSection } from "@/components/algo-section";
import { usePoll } from "@/components/use-poll";
import {
  Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis, Cell,
} from "recharts";

export default function NetworkingPage() {
  const { data, error, loading } = usePoll(api.netPlans);
  const algos = usePoll(api.netAlgos);
  if (loading) return <div className="text-muted">loading…</div>;
  if (error)   return <div className="text-danger">{error}</div>;
  if (!data)   return null;

  const plans = data.plans;
  const avgComm = plans.length
    ? plans.reduce((a, p) => a + p.comm_frac, 0) / plans.length : 0;
  const avgStep = plans.length
    ? plans.reduce((a, p) => a + p.step_ms, 0) / plans.length : 0;
  const chart = plans.slice().reverse().map((p, i) => ({
    i, label: `${p.model}@${p.cluster}`,
    compute: Math.max(p.step_ms - p.comm_ms, 0),
    comm: p.comm_ms,
  }));

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold">2. Network placement planner</h1>
        <p className="text-muted text-sm">
          Searches (TP, PP, DP) factorizations subject to per-node NVLink and
          inter-node bandwidth; picks the split with minimum step time (Megatron-style
          perf model).
        </p>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Plans" value={fmt.int(plans.length)} />
        <StatCard label="Avg step time" value={fmt.ms(avgStep)} />
        <StatCard label="Avg comm frac" value={`${(avgComm * 100).toFixed(1)}%`}
                  sub="time spent in collectives" />
        <StatCard label="Clusters"
                  value={fmt.int(new Set(plans.map(p => p.cluster)).size)}
                  sub="unique topologies" />
      </div>

      <section className="card">
        <h3>Step-time breakdown (compute vs comm) — ms</h3>
        <div className="h-72 -mx-2">
          <ResponsiveContainer>
            <BarChart data={chart}>
              <CartesianGrid stroke="#222831" strokeDasharray="3 3" />
              <XAxis dataKey="i" stroke="#8a93a0" fontSize={11} />
              <YAxis stroke="#8a93a0" fontSize={11} unit="ms" />
              <Tooltip
                contentStyle={{ background: "#141820", border: "1px solid #222831", borderRadius: 6 }}
                labelFormatter={(i) => chart[i as number]?.label}
              />
              <Bar dataKey="compute" stackId="t" fill="#4ade80" />
              <Bar dataKey="comm"    stackId="t" fill="#f472b6" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </section>

      <AlgoSection
        title="Paper-derived placement & scheduling algorithms"
        description="Each row is a faithful implementation of the paper's decision function, evaluated across model/cluster/workload scenarios. Δ = relative improvement over the paper's stated baseline (colocated / homogeneous / non-adaptive)."
        bundle={algos.data}
        theme="networking"
      />

      <section className="card">
        <h3>Plans</h3>
        <table>
          <thead>
            <tr>
              <th>Model</th><th className="text-right">Params B</th>
              <th>Cluster</th>
              <th className="text-right">GPUs</th>
              <th className="text-right">NVLink</th>
              <th className="text-right">Inter</th>
              <th className="text-right">TP/PP/DP</th>
              <th className="text-right">Step</th>
              <th className="text-right">Comm %</th>
            </tr>
          </thead>
          <tbody>
            {plans.map((p) => (
              <tr key={p.id}>
                <td><code className="text-accent">{p.model}</code></td>
                <td className="text-right tabular-nums">{p.params_b}</td>
                <td className="text-muted">{p.cluster}</td>
                <td className="text-right tabular-nums">{p.gpus}</td>
                <td className="text-right tabular-nums">{fmt.gbps(p.nvlink_gbps)}</td>
                <td className="text-right tabular-nums">{fmt.gbps(p.inter_gbps)}</td>
                <td className="text-right tabular-nums">{p.tp}·{p.pp}·{p.dp}</td>
                <td className="text-right tabular-nums">{fmt.ms(p.step_ms)}</td>
                <td className="text-right tabular-nums">{(p.comm_frac * 100).toFixed(1)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
