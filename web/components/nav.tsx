"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";

const tabs = [
  { href: "/",           label: "Overview" },
  { href: "/hbm",        label: "1. HBM" },
  { href: "/networking", label: "2. Networking" },
  { href: "/energy",     label: "3. Energy" },
  { href: "/inference",  label: "4. Inference" },
  { href: "/photonics",  label: "5. Photonics" },
];

export function Nav() {
  const path = usePathname();
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
        <div className="ml-auto text-xs text-muted">auto-refresh 5s</div>
      </div>
    </header>
  );
}
