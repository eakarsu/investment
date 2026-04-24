"use client";
import type { ChartSpec } from "@/lib/api";
import {
  Bar, BarChart, CartesianGrid, Legend, Line, LineChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";

const COLORS = ["#4ade80", "#60a5fa", "#fbbf24", "#f472b6", "#a78bfa"];

/** Renders the compact chart spec returned by the narrate endpoint. */
export function AlgoChart({ spec }: { spec: ChartSpec }) {
  // Merge series into a single `{x, series1, series2, ...}` array for recharts.
  const xs = new Set<string | number>();
  for (const s of spec.series) for (const p of s.data) xs.add(p.x);
  const rows = Array.from(xs).map((x) => {
    const row: Record<string, any> = { x };
    for (const s of spec.series) {
      const hit = s.data.find((p) => p.x === x);
      row[s.name] = hit ? hit.y : null;
    }
    return row;
  });

  return (
    <div>
      <div className="text-xs text-muted mb-1">{spec.title}</div>
      <div className="h-56 -mx-1">
        <ResponsiveContainer>
          {spec.type === "line" ? (
            <LineChart data={rows}>
              <CartesianGrid stroke="#222831" strokeDasharray="3 3" />
              <XAxis dataKey="x" stroke="#8a93a0" fontSize={11}
                     label={{ value: spec.x_label, fill: "#8a93a0", fontSize: 10, dy: 12 }} />
              <YAxis stroke="#8a93a0" fontSize={11}
                     label={{ value: spec.y_label, fill: "#8a93a0", fontSize: 10, angle: -90, dx: -12 }} />
              <Tooltip contentStyle={{ background: "#141820", border: "1px solid #222831", borderRadius: 6 }} />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              {spec.series.map((s, i) => (
                <Line key={s.name} type="monotone" dataKey={s.name}
                      stroke={COLORS[i % COLORS.length]} strokeWidth={2}
                      dot={{ r: 2 }} isAnimationActive animationDuration={900} />
              ))}
            </LineChart>
          ) : (
            <BarChart data={rows}>
              <CartesianGrid stroke="#222831" strokeDasharray="3 3" />
              <XAxis dataKey="x" stroke="#8a93a0" fontSize={11}
                     label={{ value: spec.x_label, fill: "#8a93a0", fontSize: 10, dy: 12 }} />
              <YAxis stroke="#8a93a0" fontSize={11}
                     label={{ value: spec.y_label, fill: "#8a93a0", fontSize: 10, angle: -90, dx: -12 }} />
              <Tooltip contentStyle={{ background: "#141820", border: "1px solid #222831", borderRadius: 6 }} />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              {spec.series.map((s, i) => (
                <Bar key={s.name} dataKey={s.name}
                     fill={COLORS[i % COLORS.length]} radius={[3, 3, 0, 0]}
                     isAnimationActive animationDuration={800} />
              ))}
            </BarChart>
          )}
        </ResponsiveContainer>
      </div>
    </div>
  );
}
