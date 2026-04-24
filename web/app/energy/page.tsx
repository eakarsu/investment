"use client";
import { api } from "@/lib/api";
import { fmt } from "@/lib/format";
import { StatCard } from "@/components/stat-card";
import { AlgoSection } from "@/components/algo-section";
import { usePoll } from "@/components/use-poll";
import {
  CartesianGrid, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis, Legend,
} from "recharts";

export default function EnergyPage() {
  const r = usePoll(api.regions);
  const d = usePoll(api.enDecisions);
  const algos = usePoll(api.enAlgos);
  if (r.loading || d.loading) return <div className="text-muted">loading…</div>;
  if (r.error)   return <div className="text-danger">{r.error}</div>;
  if (d.error)   return <div className="text-danger">{d.error}</div>;
  if (!r.data || !d.data) return null;

  const regions = r.data.regions;
  const decisions = d.data.decisions;
  const avgSavings = decisions.length
    ? decisions.reduce((a, x) => a + x.savings_pct, 0) / decisions.length : 0;
  const cleanest = [...regions].sort((a, b) => a.carbon_gco2_per_kwh - b.carbon_gco2_per_kwh)[0];
  const cheapest = [...regions].sort((a, b) => a.price_usd_per_kwh - b.price_usd_per_kwh)[0];

  const scatter = regions.map((x) => ({
    x: x.price_usd_per_kwh, y: x.carbon_gco2_per_kwh,
    z: x.available_gpus, region: x.region, latency: x.latency_ms, pue: x.pue,
  }));

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold">3. Energy-aware router</h1>
        <p className="text-muted text-sm">
          Picks the region where the next token is cheapest to serve, weighted by
          ${`{`}price × PUE{`}`} and carbon intensity, subject to a latency SLA. Grid queue
          length shown so you can see which regions are capacity-constrained.
        </p>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Regions" value={fmt.int(regions.length)} />
        <StatCard label="Decisions" value={fmt.int(decisions.length)} />
        <StatCard label="Avg savings vs worst"
                  value={`${avgSavings.toFixed(1)}%`} sub="combined cost+carbon" />
        <StatCard label="Best carbon"
                  value={cleanest ? `${cleanest.carbon_gco2_per_kwh.toFixed(0)} gCO2/kWh` : "—"}
                  sub={cleanest?.region} />
      </div>

      <section className="card">
        <h3>Regions — price vs carbon (bubble = available GPUs)</h3>
        <div className="h-72 -mx-2">
          <ResponsiveContainer>
            <ScatterChart>
              <CartesianGrid stroke="#222831" strokeDasharray="3 3" />
              <XAxis type="number" dataKey="x" name="USD/kWh" stroke="#8a93a0" fontSize={11} />
              <YAxis type="number" dataKey="y" name="gCO2/kWh" stroke="#8a93a0" fontSize={11} />
              <ZAxis type="number" dataKey="z" range={[50, 600]} />
              <Tooltip
                cursor={{ strokeDasharray: "3 3" }}
                contentStyle={{ background: "#141820", border: "1px solid #222831", borderRadius: 6 }}
                content={({ active, payload }) => {
                  if (!active || !payload?.length) return null;
                  const p: any = payload[0].payload;
                  return (
                    <div className="p-2 text-xs">
                      <div className="font-semibold">{p.region}</div>
                      <div>${p.x.toFixed(3)}/kWh · {p.y.toFixed(0)} gCO2/kWh</div>
                      <div>PUE {p.pue} · {p.latency} ms · {fmt.int(p.z)} GPUs</div>
                    </div>
                  );
                }}
              />
              <Scatter data={scatter} fill="#4ade80" />
            </ScatterChart>
          </ResponsiveContainer>
        </div>
      </section>

      <section className="card">
        <h3>Regions</h3>
        <table>
          <thead>
            <tr>
              <th>Region</th><th className="text-right">USD/kWh</th>
              <th className="text-right">gCO2/kWh</th><th className="text-right">PUE</th>
              <th className="text-right">Latency</th><th className="text-right">GPUs</th>
              <th className="text-right">Queue (mo)</th>
            </tr>
          </thead>
          <tbody>
            {regions.map((x) => (
              <tr key={x.region}>
                <td><code className="text-accent">{x.region}</code></td>
                <td className="text-right tabular-nums">${x.price_usd_per_kwh.toFixed(3)}</td>
                <td className="text-right tabular-nums">{x.carbon_gco2_per_kwh.toFixed(0)}</td>
                <td className="text-right tabular-nums">{x.pue.toFixed(2)}</td>
                <td className="text-right tabular-nums">{x.latency_ms.toFixed(0)} ms</td>
                <td className="text-right tabular-nums">{fmt.int(x.available_gpus)}</td>
                <td className="text-right tabular-nums">{x.grid_queue_months}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <AlgoSection
        title="Paper-derived energy / carbon algorithms"
        description="Implementations of published energy optimisers — pipeline frequency scaling (Perseus), power oversubscription (POLCA), energy-aware routing (DynamoLLM), carbon accounting (LLMCarbon), and spatio-temporal shifting (Google VCC)."
        bundle={algos.data}
        theme="energy"
      />

      <section className="card">
        <h3>Recent routing decisions</h3>
        <table>
          <thead>
            <tr>
              <th className="text-right">Tokens</th>
              <th className="text-right">SLA</th>
              <th>Chosen</th>
              <th className="text-right">Cost</th>
              <th className="text-right">CO2 (g)</th>
              <th className="text-right">Savings</th>
            </tr>
          </thead>
          <tbody>
            {decisions.map((d) => (
              <tr key={d.id}>
                <td className="text-right tabular-nums">{fmt.int(d.tokens)}</td>
                <td className="text-right tabular-nums">{d.sla_ms} ms</td>
                <td><code className="text-accent">{d.chosen}</code></td>
                <td className="text-right tabular-nums">{fmt.money(d.cost_usd)}</td>
                <td className="text-right tabular-nums">{d.carbon_g.toFixed(2)}</td>
                <td className="text-right tabular-nums text-accent">{d.savings_pct.toFixed(1)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
