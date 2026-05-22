"use client";

import { usePoll } from "@/components/use-poll";
import { api } from "@/lib/api";

export default function TaxLotDriftPage() {
  const { data, error, loading } = usePoll(api.taxLotDrift);

  if (loading) return <div className="text-muted">loading...</div>;
  if (error) return <div className="text-danger">{error}</div>;
  if (!data) return null;

  return (
    <div className="space-y-6">
      <div className="card">
        <h2 className="font-semibold">Tax-Lot Drift</h2>
        <p className="text-sm text-muted">
          Unrealized gain concentration, wash-sale windows, and rebalance candidates across investment lots.
        </p>
      </div>
      <div className="grid md:grid-cols-4 gap-4">
        <Metric label="Embedded Gain" value={`$${data.summary.embedded_gain.toLocaleString()}`} />
        <Metric label="Harvestable Loss" value={`$${data.summary.harvestable_loss.toLocaleString()}`} />
        <Metric label="Wash Windows" value={data.summary.wash_windows} />
        <Metric label="Review Priority" value={data.summary.priority} />
      </div>
      <div className="card">
        <h3 className="font-semibold mb-3">Lots</h3>
        <div className="space-y-3">
          {data.lots.map((lot: any) => (
            <div key={lot.symbol} className="grid md:grid-cols-5 gap-3 text-sm border-b border-border pb-3">
              <strong>{lot.symbol}</strong>
              <span>{lot.account}</span>
              <span>{lot.gain_loss_pct}%</span>
              <span>{lot.holding_period}</span>
              <span>{lot.action}</span>
            </div>
          ))}
        </div>
      </div>
      <div className="card">
        <h3 className="font-semibold mb-3">Controls</h3>
        <ul className="list-disc pl-5 text-sm text-muted">{data.controls.map((item: string) => <li key={item}>{item}</li>)}</ul>
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return <div className="card"><div className="text-xs text-muted">{label}</div><div className="text-xl font-semibold">{value}</div></div>;
}
