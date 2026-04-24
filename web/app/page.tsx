"use client";
import Link from "next/link";
import { api } from "@/lib/api";
import { usePoll } from "@/components/use-poll";

const THEME_LINKS: Record<string, { href: string; desc: string }> = {
  hbm:        { href: "/hbm",        desc: "Admission controller + KV-cache eviction simulator against per-GPU 80 GB HBM budget." },
  networking: { href: "/networking", desc: "Closed-form parallelism planner (TP, PP, DP) for NVLink/Rubin-class clusters." },
  energy:     { href: "/energy",     desc: "Region router: cheapest token under latency SLA, weighted by cost and carbon." },
  inference:  { href: "/inference",  desc: "Telemetry ingestion + paper-derived recommendations (staggered batch, spec decode, routing)." },
  photonics:  { href: "/photonics",  desc: "Critical-path collective scheduler on mixed copper + optical topology." },
};

export default function Overview() {
  const { data, error, loading } = usePoll(api.overview);
  if (loading) return <div className="text-muted">loading…</div>;
  if (error)   return <div className="text-danger">{error}</div>;
  if (!data)   return null;

  return (
    <div className="space-y-6">
      <div className="card">
        <h3>5 AI investment themes</h3>
        <p className="text-muted text-sm">
          Software solutions — not just a dashboard — for each structural bottleneck in the
          AI-build-out: HBM scarcity, NVLink-scale networking, grid-limited energy,
          inference efficiency, and silicon photonics.
        </p>
      </div>

      <div className="grid md:grid-cols-2 gap-4">
        {data.themes.map((t, i) => {
          const meta = THEME_LINKS[t.id] ?? { href: "/", desc: "" };
          return (
            <Link key={t.id} href={meta.href} className="card hover:border-accent/50 transition-colors">
              <div className="flex items-baseline gap-2 mb-1">
                <span className="badge">{i + 1}</span>
                <h2 className="font-semibold">{t.name}</h2>
                <span className="ml-auto text-accent text-xs font-semibold tabular-nums">{t.kpi}</span>
              </div>
              <p className="text-sm text-muted">{meta.desc}</p>
              <div className="text-xs text-muted mt-2 tabular-nums">{t.rows} rows in Postgres</div>
            </Link>
          );
        })}
      </div>
    </div>
  );
}
