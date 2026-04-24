"use client";
import { api } from "@/lib/api";
import { fmt } from "@/lib/format";
import { StatCard } from "@/components/stat-card";
import { AlgoSection } from "@/components/algo-section";
import { usePoll } from "@/components/use-poll";
import {
  Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";

export default function PhotonicsPage() {
  const { data, error, loading } = usePoll(api.photonics);
  const algos = usePoll(api.phAlgos);
  if (loading) return <div className="text-muted">loading…</div>;
  if (error)   return <div className="text-danger">{error}</div>;
  if (!data)   return null;

  const rows = data.scenarios;
  const avgSpeedup = rows.length
    ? rows.reduce((a, r) => a + r.speedup_pct, 0) / rows.length : 0;
  const maxSpeedup = rows.length
    ? rows.reduce((a, r) => Math.max(a, r.speedup_pct), 0) : 0;

  const chart = rows.slice().reverse().map((r) => ({
    name: r.name, baseline: r.baseline_ms, optimized: r.optimized_ms,
  }));

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold">5. Photonic collective scheduler</h1>
        <p className="text-muted text-sm">
          Critical-path scheduler over a collective DAG on a mixed-topology cluster
          (optical + copper links). Routes through optical intermediates when 2-hop
          optical beats a direct copper link.
        </p>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Scenarios" value={fmt.int(rows.length)} />
        <StatCard label="Avg speedup"
                  value={`${avgSpeedup.toFixed(1)}%`}
                  sub="vs copper-only baseline" />
        <StatCard label="Max speedup" value={`${maxSpeedup.toFixed(1)}%`} />
        <StatCard label="Avg optical"
                  value={`${(100 * rows.reduce((a, r) => a + r.optical_fraction, 0) / Math.max(rows.length, 1)).toFixed(0)}%`}
                  sub="link fraction across scenarios" />
      </div>

      <section className="card">
        <h3>Baseline vs optimized collective critical-path (ms)</h3>
        <div className="h-72 -mx-2">
          <ResponsiveContainer>
            <BarChart data={chart}>
              <CartesianGrid stroke="#222831" strokeDasharray="3 3" />
              <XAxis dataKey="name" stroke="#8a93a0" fontSize={10} angle={-30} textAnchor="end" height={70} />
              <YAxis stroke="#8a93a0" fontSize={11} unit="ms" />
              <Tooltip
                contentStyle={{ background: "#141820", border: "1px solid #222831", borderRadius: 6 }}
              />
              <Bar dataKey="baseline" fill="#8a93a0" radius={[3,3,0,0]} />
              <Bar dataKey="optimized" fill="#4ade80" radius={[3,3,0,0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </section>

      <AlgoSection
        title="Paper-derived optical/topology algorithms"
        description="Implementations of TopoOpt, SiP-ML, TACCL, Rail-only, and Jupiter OCS b-matching. Δ is the improvement vs copper fat-tree / ring / CLOS baseline."
        bundle={algos.data}
        theme="photonics"
      />

      <section className="card">
        <h3>Scenarios</h3>
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th className="text-right">GPUs</th>
              <th className="text-right">Optical %</th>
              <th className="text-right">Optical</th>
              <th className="text-right">Copper</th>
              <th className="text-right">DAG size</th>
              <th className="text-right">Baseline</th>
              <th className="text-right">Optimized</th>
              <th className="text-right">Speedup</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td><code className="text-accent">{r.name}</code></td>
                <td className="text-right tabular-nums">{r.num_gpus}</td>
                <td className="text-right tabular-nums">{(r.optical_fraction * 100).toFixed(0)}%</td>
                <td className="text-right tabular-nums">{fmt.gbps(r.optical_gbps)}</td>
                <td className="text-right tabular-nums">{fmt.gbps(r.copper_gbps)}</td>
                <td className="text-right tabular-nums">{r.dag_size}</td>
                <td className="text-right tabular-nums">{r.baseline_ms.toFixed(1)} ms</td>
                <td className="text-right tabular-nums">{r.optimized_ms.toFixed(1)} ms</td>
                <td className="text-right tabular-nums text-accent">{r.speedup_pct.toFixed(1)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
