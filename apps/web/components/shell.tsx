"use client";

import {
  Activity,
  BarChart3,
  BookOpen,
  Boxes,
  Database,
  FlaskConical,
  LayoutDashboard,
  Lightbulb,
  Menu,
  Settings,
  ShieldCheck,
  Wallet,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { Logo } from "@/components/logo";
import { Badge, cn } from "@/components/ui";
import { getToken, setToken } from "@/lib/api";

const NAV = [
  {
    label: "Research",
    items: [
      { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
      { href: "/sprints", label: "Agent sprints", icon: Activity },
      { href: "/strategies", label: "Strategies", icon: BarChart3 },
      { href: "/ideas", label: "Idea lab", icon: Lightbulb },
      { href: "/experiments", label: "Experiments", icon: FlaskConical },
    ],
  },
  {
    label: "Portfolio",
    items: [
      { href: "/portfolio", label: "Builder", icon: Boxes },
      { href: "/paper", label: "Paper trading", icon: Wallet },
    ],
  },
  {
    label: "System",
    items: [
      { href: "/data", label: "Data explorer", icon: Database },
      { href: "/settings", label: "Settings", icon: Settings },
      { href: "/docs", label: "Docs", icon: BookOpen },
    ],
  },
];

const TITLES: Array<[string, string, string]> = [
  ["/dashboard", "Dashboard", "Research overview"],
  ["/sprints/", "Sprint replay", "Agent audit trail"],
  ["/sprints", "Agent sprints", "Live research"],
  ["/strategies/", "Strategy", "Validation record"],
  ["/strategies", "Strategies", "Falsification gauntlet"],
  ["/ideas", "Idea lab", "Hypothesis queue"],
  ["/experiments", "Experiments", "Every trial counts"],
  ["/portfolio", "Portfolio builder", "What-if construction"],
  ["/paper", "Paper trading", "Forward simulation"],
  ["/data", "Data explorer", "Licences and coverage"],
  ["/settings", "Settings", "Budget and models"],
  ["/docs", "Docs", "Method and API"],
];

function titleFor(pathname: string): { title: string; eyebrow: string } {
  for (const [path, title, eyebrow] of TITLES) {
    if (pathname === path || (path.endsWith("/") && pathname.startsWith(path))) return { title, eyebrow };
  }
  return { title: "Nullpoint", eyebrow: "Research only" };
}

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [signedIn, setSignedIn] = useState<boolean | null>(null);
  const { title, eyebrow } = titleFor(pathname);

  useEffect(() => {
    setSignedIn(Boolean(getToken()));
    setOpen(false);
  }, [pathname]);

  const signOut = () => {
    setToken(null);
    setSignedIn(false);
    window.location.href = "/";
  };

  const nav = (
    <nav className="flex h-full flex-col gap-6 overflow-y-auto px-3 py-5">
      <div className="px-2">
        <Logo />
      </div>
      <div className="space-y-5">
        {NAV.map((group) => (
          <div key={group.label}>
            <p className="label px-2 pb-2">{group.label}</p>
            <ul className="space-y-0.5">
              {group.items.map((item) => {
                const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
                const Icon = item.icon;
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      className={cn(
                        "focus-ring flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px] transition",
                        active
                          ? "bg-slate text-text shadow-[inset_2px_0_0_0_#5FE3C0]"
                          : "text-muted hover:bg-slate/60 hover:text-text",
                      )}
                    >
                      <Icon size={15} className={active ? "text-signal" : "text-muted/80"} />
                      {item.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>
      <div className="mt-auto space-y-3 px-1">
        <div className="rounded-lg border border-line/80 bg-ink/50 p-3">
          <div className="flex items-center gap-2">
            <ShieldCheck size={14} className="text-signal" />
            <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-signal">Safety envelope</p>
          </div>
          <p className="mt-2 text-[11px] leading-relaxed text-muted">
            No live-trading hooks. Sealed holdout. Human approval before paper.
          </p>
        </div>
        <p className="px-1 font-mono text-[10px] leading-relaxed text-muted/70">
          Research only. Not investment advice.
        </p>
      </div>
    </nav>
  );

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[236px_1fr]">
      <aside className="sticky top-0 hidden h-screen border-r border-line/80 bg-graphite/50 lg:block">{nav}</aside>

      {open && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <button className="absolute inset-0 bg-ink/80" aria-label="Close menu" onClick={() => setOpen(false)} />
          <aside className="relative h-full w-[260px] border-r border-line bg-graphite">{nav}</aside>
        </div>
      )}

      <div className="min-w-0">
        <header className="sticky top-0 z-30 border-b border-line/80 bg-ink/80 backdrop-blur-md">
          <div className="flex h-14 items-center gap-3 px-4 md:px-7">
            <button
              className="focus-ring rounded-md p-1 text-muted hover:text-text lg:hidden"
              onClick={() => setOpen((v) => !v)}
              aria-label="Menu"
            >
              {open ? <X size={18} /> : <Menu size={18} />}
            </button>
            <div className="min-w-0">
              <p className="label">{eyebrow}</p>
              <p className="truncate text-[13px] font-medium text-text">{title}</p>
            </div>
            <div className="ml-auto flex items-center gap-2">
              {signedIn === false && <Badge tone="warn">demo</Badge>}
              <Badge tone="signal" className="hidden sm:inline-flex">
                0 live hooks
              </Badge>
              {signedIn ? (
                <button
                  onClick={signOut}
                  className="focus-ring rounded-full border border-line bg-slate/80 px-2.5 py-1 font-mono text-[10px] uppercase tracking-wider text-muted hover:text-text"
                >
                  sign out
                </button>
              ) : (
                <Link
                  href="/login"
                  className="focus-ring rounded-lg bg-signal px-3 py-1.5 text-xs font-medium text-ink hover:bg-signal/90"
                >
                  Sign in
                </Link>
              )}
            </div>
          </div>
        </header>
        <main className="mx-auto max-w-[1400px] px-4 py-6 md:px-7 md:py-8">{children}</main>
        <footer className="mx-auto max-w-[1400px] px-4 pb-8 md:px-7">
          <div className="flex flex-col justify-between gap-2 border-t border-line/60 pt-4 text-[10px] text-muted/70 md:flex-row">
            <p className="font-mono uppercase tracking-[0.12em]">Nullpoint · nothing ships until it beats the null</p>
            <p>Backtests are hypotheses, not forecasts. No live order routing exists.</p>
          </div>
        </footer>
      </div>
    </div>
  );
}
