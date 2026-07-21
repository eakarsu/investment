"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, clearToken, getToken } from "@/lib/api";

export function Nav() {
  const path = usePathname();
  const router = useRouter();
  const [identity, setIdentity] = useState<string | null>(null);
  useEffect(() => {
    if (!getToken()) return setIdentity(null);
    api.me().then(user => setIdentity(`${user.username} · ${user.role}`)).catch(() => setIdentity(null));
  }, [path]);
  return (
    <header className="border-b border-border bg-panel/50 backdrop-blur sticky top-0 z-10">
      <div className="max-w-6xl mx-auto flex items-center gap-6 px-6 py-3">
        <Link href="/" className="font-semibold">Governed paper trading</Link>
        <span className="badge">PAPER ONLY</span>
        <div className="ml-auto text-xs text-muted flex items-center gap-3">
          {identity ? <><span>{identity}</span><button className="underline" onClick={() => { clearToken(); router.push("/login"); }}>sign out</button></> : <Link className="underline" href="/login">sign in</Link>}
        </div>
      </div>
    </header>
  );
}
