"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, getToken, clearToken } from "@/lib/api";

const tabs = [
  { href: "/",           label: "Overview" },
  { href: "/hbm",        label: "1. HBM" },
  { href: "/networking", label: "2. Networking" },
  { href: "/energy",     label: "3. Energy" },
  { href: "/inference",  label: "4. Inference" },
  { href: "/photonics",  label: "5. Photonics" },
  { href: "/tax-lot-drift", label: "Tax-Lot Drift" },
  { href: "/ai-suite",   label: "AI Suite (NEW)" },
];

export function Nav() {
  const path = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<string | null>(null);

  useEffect(() => {
    const token = getToken();
    if (!token) {
      setUser(null);
      return;
    }
    api.me().then((u) => setUser(u.username)).catch(() => setUser(null));
  }, [path]);

  return (
    <header className="border-b border-border bg-panel/50 backdrop-blur sticky top-0 z-10">
      <div className="max-w-6xl mx-auto flex items-center gap-6 px-6 py-3">
        <div className="font-semibold">investment · 5 themes</div>
        <nav className="flex gap-1 text-sm">
          {tabs.map((t) => {
            const on = path === t.href;
            return (
              <Link
                key={t.href}
                href={t.href}
                className={`px-3 py-1 rounded-md ${on ? "bg-border text-fg" : "text-muted hover:text-fg"}`}
              >
                {t.label}
              </Link>
            );
          })}
        </nav>
        <div className="ml-auto text-xs text-muted flex items-center gap-3">
          {user ? (
            <>
              <span>{user}</span>
              <button
                className="underline"
                onClick={() => {
                  clearToken();
                  router.push("/login");
                }}
              >
                sign out
              </button>
            </>
          ) : (
            <Link href="/login" className="underline">sign in</Link>
          )}
          <span>auto-refresh 5s</span>
        </div>
      </div>
    </header>
  );
}
