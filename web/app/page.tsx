"use client";

import { FormEvent, useEffect, useState } from "react";
import Link from "next/link";
import { api, getToken } from "@/lib/api";

function Field({ label, value, onChange, type = "text" }: { label: string; value: string; onChange: (value: string) => void; type?: string }) {
  return <label className="block text-xs text-muted">{label}<input type={type} className="mt-1 w-full bg-bg border border-muted/30 rounded px-2 py-1.5 text-fg" value={value} onChange={event => onChange(event.target.value)} required /></label>;
}

export default function GovernedConsole() {
  const [identity, setIdentity] = useState<{ username: string; role: string } | null>(null);
  const [message, setMessage] = useState("Ready. Every action remains paper-only and auditable.");
  const [result, setResult] = useState<unknown>(null);
  const [marketId, setMarketId] = useState("");
  const [symbol, setSymbol] = useState("NVDA");
  const [price, setPrice] = useState("10000");
  const [orderId, setOrderId] = useState("");
  const [quantity, setQuantity] = useState("10");
  const [version, setVersion] = useState("1");

  useEffect(() => { if (getToken()) api.me().then(setIdentity).catch(() => setIdentity(null)); }, []);
  async function run(label: string, action: () => Promise<unknown>) {
    setMessage(`${label}…`); setResult(null);
    try { const value = await action(); setResult(value); setMessage(`${label} completed.`); }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : `${label} failed.`); }
  }
  if (!identity) return <div className="card"><h1 className="text-xl font-semibold">Governed paper-order operations</h1><p className="text-muted my-3">Sign in with a provisioned, role-scoped identity. No live brokerage or model-driven trading route is mounted.</p><Link href="/login" className="underline text-accent">Sign in</Link></div>;

  const submitMarket = (event: FormEvent) => { event.preventDefault(); run("Licensed market ingestion", async () => {
    const value = await api.marketEvent({ source: "licensed-market", external_event_id: `quote-${symbol}-${Date.now()}`, symbol, price_cents: Number(price), available_quantity: "1000", observed_at: new Date().toISOString(), evidence_url: `https://licensed-market.example.test/quotes/${symbol}` });
    setMarketId(String(value.id)); return value;
  }); };
  const submitOrder = (event: FormEvent) => { event.preventDefault(); run("Risk-evaluated order draft", async () => {
    const value = await api.order({ client_order_id: `console-${Date.now()}`, market_event_id: Number(marketId), side: "BUY", quantity, limit_price_cents: Number(price) });
    const id = value.order.id; if (typeof id === "number") setOrderId(String(id)); return value;
  }); };

  return <div className="space-y-6">
    <section className="card"><div className="flex justify-between gap-4"><div><h1 className="text-xl font-semibold">Paper-order control room</h1><p className="text-muted text-sm">Signed in as {identity.username} ({identity.role}). Duties are intentionally separate; controls do not become bypassable recommendations.</p></div><span className="badge">NO LIVE ORDERS</span></div></section>
    <div className="grid md:grid-cols-2 gap-4">
      <form className="card space-y-3" onSubmit={submitMarket}><h2 className="font-semibold">1. Licensed market event</h2><Field label="Symbol" value={symbol} onChange={setSymbol} /><Field label="Price (integer cents)" value={price} onChange={setPrice} type="number" /><button className="bg-accent text-bg px-3 py-1.5 rounded">Record evidence</button><p className="text-xs text-muted">Requires DATA_OPS or ADMIN and an allowlisted HTTPS source.</p></form>
      <form className="card space-y-3" onSubmit={submitOrder}><h2 className="font-semibold">2. Draft and risk-check</h2><Field label="Market event ID" value={marketId} onChange={setMarketId} type="number" /><Field label="Quantity" value={quantity} onChange={setQuantity} /><button className="bg-accent text-bg px-3 py-1.5 rounded">Create paper order</button><p className="text-xs text-muted">Requires INVESTOR. Blocked orders cannot reach review.</p></form>
      <form className="card space-y-3" onSubmit={event => { event.preventDefault(); run("Independent review", () => api.decision(Number(orderId), { decision: "APPROVE", attestation: "I independently reviewed the market evidence and deterministic risk checks", reason: "", expected_version: Number(version) })); }}><h2 className="font-semibold">3. Independent decision</h2><Field label="Order ID" value={orderId} onChange={setOrderId} type="number" /><Field label="Expected version" value={version} onChange={setVersion} type="number" /><button className="bg-accent text-bg px-3 py-1.5 rounded">Approve after review</button><p className="text-xs text-muted">Requires a different REVIEWER identity and exact attestation.</p></form>
      <form className="card space-y-3" onSubmit={event => { event.preventDefault(); run("Typed paper fill", () => api.fill(Number(orderId), { provider: "paper-broker", external_event_id: `paper-fill-${Date.now()}`, quantity, price_cents: Number(price), occurred_at: new Date().toISOString(), evidence_url: `https://licensed-broker.example.test/paper-fills/${orderId}` })); }}><h2 className="font-semibold">4. Paper fill evidence</h2><Field label="Approved order ID" value={orderId} onChange={setOrderId} type="number" /><Field label="Fill quantity" value={quantity} onChange={setQuantity} /><button className="bg-accent text-bg px-3 py-1.5 rounded">Record paper fill</button><p className="text-xs text-muted">Requires BROKER_OPS, a custody grant, and a registered paper-broker contract.</p></form>
    </div>
    <section className="card flex gap-3"><button className="border border-border rounded px-3 py-1.5" onClick={() => run("Reconciliation", api.reconcile)}>Run reconciliation</button><button className="border border-border rounded px-3 py-1.5" onClick={() => run("Audit export", api.audit)}>Load audit export</button></section>
    <section className="card"><p role="status" className="text-sm">{message}</p>{result !== null && <pre className="mt-3 overflow-auto text-xs whitespace-pre-wrap">{JSON.stringify(result, null, 2)}</pre>}</section>
  </div>;
}
