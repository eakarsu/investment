"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault(); setError(""); setLoading(true);
    try { await api.login(username, password); router.push("/"); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Sign-in failed"); }
    finally { setLoading(false); }
  }
  async function fillDemoCredentials() {
    setError("");
    try {
      const response = await fetch("/api/auth/demo-credentials", { cache: "no-store" });
      const credentials = await response.json();
      if (!response.ok) throw new Error(credentials.detail || credentials.error || "Demo credentials are unavailable");
      setUsername(credentials.username || credentials.email || "");
      setPassword(credentials.password || "");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Demo credentials are unavailable");
    }
  }
  return <div className="max-w-md mx-auto card mt-12">
    <h1 className="text-xl font-semibold">Named-user sign in</h1>
    <p className="text-muted text-sm my-3">Accounts and duties are provisioned by an administrator. Local demo credentials are available only from the development runtime.</p>
    <form onSubmit={submit} className="space-y-3">
      <label className="block text-xs text-muted">Username<input className="w-full bg-bg border border-muted/30 rounded px-2 py-2" value={username} onChange={event => setUsername(event.target.value)} required /></label>
      <label className="block text-xs text-muted">Password<input type="password" className="w-full bg-bg border border-muted/30 rounded px-2 py-2" value={password} onChange={event => setPassword(event.target.value)} minLength={12} required /></label>
      {error && <p role="alert" className="text-danger text-sm">{error}</p>}
      <button type="button" onClick={fillDemoCredentials} className="border border-muted/30 px-4 py-2 rounded font-medium">Auto Fill Demo Credentials</button>
      <button disabled={loading} className="bg-accent text-bg px-4 py-2 rounded font-medium disabled:opacity-50">{loading ? "Signing in…" : "Sign In"}</button>
    </form>
  </div>;
}
