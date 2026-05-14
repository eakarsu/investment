"use client";

/**
 * Login + Register UI.
 *
 * On success the JWT is stored in localStorage under "investment.jwt" — every
 * subsequent fetch in lib/api.ts attaches it as a Bearer token.
 */

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      if (mode === "login") {
        await api.login(username, password);
      } else {
        await api.register(username, email, password);
      }
      router.push("/");
    } catch (err: any) {
      setError(err.message || "Auth failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="max-w-md mx-auto card mt-12">
      <h2 className="font-semibold mb-2">
        {mode === "login" ? "Sign in" : "Create account"}
      </h2>
      <p className="text-muted text-sm mb-4">
        {mode === "login"
          ? "Use your investment account to access portfolio + algorithm endpoints."
          : "Pick a username + email, minimum 8-character password."}
      </p>
      <form onSubmit={onSubmit} className="space-y-3">
        <div>
          <label className="text-xs text-muted">Username</label>
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="w-full bg-bg border border-muted/30 rounded px-2 py-1"
            required
          />
        </div>
        {mode === "register" && (
          <div>
            <label className="text-xs text-muted">Email</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full bg-bg border border-muted/30 rounded px-2 py-1"
              required
            />
          </div>
        )}
        <div>
          <label className="text-xs text-muted">Password</label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full bg-bg border border-muted/30 rounded px-2 py-1"
            required
            minLength={8}
          />
        </div>
        {error && <div className="text-danger text-sm">{error}</div>}
        <button
          type="submit"
          disabled={loading}
          className="bg-accent text-bg px-3 py-1.5 rounded font-medium disabled:opacity-50"
        >
          {loading ? "…" : mode === "login" ? "Sign in" : "Register"}
        </button>
      </form>
      <button
        type="button"
        onClick={() => setMode(mode === "login" ? "register" : "login")}
        className="block text-xs text-muted mt-3 underline"
      >
        {mode === "login" ? "Need an account? Register" : "Have an account? Sign in"}
      </button>
    </div>
  );
}
