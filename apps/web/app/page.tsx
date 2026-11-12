import {
  ArrowRight,
  Ban,
  Check,
  CircleSlash,
  Fingerprint,
  GitBranch,
  Lock,
  ShieldCheck,
  Sigma,
  TestTubeDiagonal,
  X,
} from "lucide-react";
import Link from "next/link";

import { EquityChart } from "@/components/charts";
import { Logo } from "@/components/logo";
import { Badge, cn } from "@/components/ui";
import { equitySeries } from "@/lib/mock";

const GAUNTLET = [
  {
    icon: Sigma,
    title: "Deflated Sharpe",
    body: "Every trial in the org increments one global counter. DSR is computed against that count, not the trials you chose to show.",
  },
  {
    icon: TestTubeDiagonal,
    title: "PBO / CSCV",
    body: "Combinatorially symmetric cross-validation estimates the probability that the selected configuration is overfit.",
  },
  {
    icon: GitBranch,
    title: "Walk-forward + CPCV",
    body: "Anchored and rolling out-of-sample folds, purged and embargoed K-fold. No shuffled time series, ever.",
  },
  {
    icon: Fingerprint,
    title: "Monte Carlo nulls",
    body: "Trade bootstrap, block bootstrap, random entry and signal shuffle. If noise can do it, the strategy is noise.",
  },
  {
    icon: CircleSlash,
    title: "Cost and delay stress",
    body: "2× and 3× commissions, slippage, borrow, plus one extra bar of execution delay. Edges that die at 3× never ship.",
  },
  {
    icon: Lock,
    title: "Sealed holdout",
    body: "The final slice of history is locked behind a hash. Agents cannot read it; a human opens it once, on a frozen configuration.",
  },
];

const AGENTS = [
  ["Orchestrator", "Plans, budgets, promotes or kills"],
  ["Ideator", "Falsifiable hypotheses, no repeats"],
  ["Quant", "Signal code inside the sandbox"],
  ["Engineer", "Fixes runtime and leakage faults"],
  ["Backtester", "Cost-aware grids, next-bar fills"],
  ["Skeptic", "Tries to destroy every result"],
  ["Risk officer", "Exposure, drawdown, borrow"],
  ["Librarian", "Embedding dedupe and lineage"],
  ["Portfolio", "HRP, risk parity, inverse vol"],
  ["Auditor", "Replayable trail and tearsheets"],
];

const SAMPLE = equitySeries(160, 11, 0.0006, 0.0072);

export default function LandingPage() {
  return (
    <div className="min-h-screen">
      <header className="mx-auto flex h-16 max-w-6xl items-center justify-between px-5">
        <Logo />
        <nav className="hidden items-center gap-7 text-[13px] text-muted md:flex">
          <a href="#gauntlet" className="hover:text-text">The gauntlet</a>
          <a href="#agents" className="hover:text-text">Agents</a>
          <a href="#evidence" className="hover:text-text">Evidence</a>
          <Link href="/docs" className="hover:text-text">Docs</Link>
        </nav>
        <div className="flex items-center gap-2">
          <Link
            href="/login"
            className="focus-ring hidden rounded-lg px-3 py-2 text-[13px] text-muted hover:text-text sm:block"
          >
            Sign in
          </Link>
          <Link
            href="/dashboard"
            className="focus-ring rounded-lg bg-signal px-3.5 py-2 text-[13px] font-medium text-ink hover:bg-signal/90"
          >
            Open the lab
          </Link>
        </div>
      </header>

      <section className="relative overflow-hidden border-y border-line/60">
        <div className="grid-bg pointer-events-none absolute inset-0 opacity-40" />
        <div className="pointer-events-none absolute -top-40 left-1/3 h-[420px] w-[620px] rounded-full bg-signal/10 blur-[120px]" />
        <div className="relative mx-auto grid max-w-6xl gap-12 px-5 py-20 md:grid-cols-[1.05fr_0.95fr] md:py-28">
          <div>
            <Badge tone="signal">multi-agent quant research</Badge>
            <h1 className="mt-6 text-5xl font-semibold leading-[1.02] tracking-[-0.045em] text-text md:text-6xl">
              Most strategies
              <br />
              are noise.
              <br />
              <span className="text-signal">Prove yours isn&apos;t.</span>
            </h1>
            <p className="mt-6 max-w-md text-[15px] leading-relaxed text-muted">
              Nullpoint runs a team of ten AI researchers that write, backtest and try to falsify
              systematic strategies. Only what survives the gauntlet reaches the leaderboard.
            </p>
            <div className="mt-8 flex flex-wrap items-center gap-3">
              <Link
                href="/dashboard"
                className="focus-ring inline-flex items-center gap-2 rounded-lg bg-signal px-4 py-2.5 text-sm font-medium text-ink hover:bg-signal/90"
              >
                Explore a sprint <ArrowRight size={15} />
              </Link>
              <Link
                href="/docs"
                className="focus-ring inline-flex items-center gap-2 rounded-lg border border-line bg-slate/60 px-4 py-2.5 text-sm text-text hover:border-muted/40"
              >
                Read the method
              </Link>
            </div>
            <p className="mt-8 font-mono text-[10px] uppercase tracking-[0.16em] text-muted">
              Nothing ships until it beats the null.
            </p>
          </div>

          <div className="relative">
            <div className="card overflow-hidden">
              <div className="flex items-center justify-between border-b border-line/70 px-4 py-3">
                <div className="flex items-center gap-2">
                  <span className="relative flex h-2 w-2">
                    <span className="absolute inline-flex h-full w-full animate-pulse-ring rounded-full bg-signal" />
                    <span className="relative inline-flex h-2 w-2 rounded-full bg-signal" />
                  </span>
                  <span className="font-mono text-[11px] text-text">sprint · sector momentum</span>
                </div>
                <Badge tone="signal">evolving</Badge>
              </div>
              <div className="space-y-3 px-4 py-4 font-mono text-[11px] leading-relaxed">
                <p className="text-muted">
                  <span className="text-beam">ideator</span> hypothesis: 12-1 momentum survives only with a
                  trend filter.
                </p>
                <p className="text-muted">
                  <span className="text-text">backtester</span> 24-config grid · best IS Sharpe 1.42 · trial
                  #41
                </p>
                <p className="text-danger/90">
                  <span className="text-danger">skeptic</span> PBO 0.44 (gate 0.35) → reject this
                  configuration.
                </p>
                <p className="text-signal/90">
                  <span className="text-signal">orchestrator</span> variant queued · one axis changed ·
                  holdout sealed.
                </p>
              </div>
              <div className="border-t border-line/70 px-2 pb-2 pt-3">
                <EquityChart data={SAMPLE} height={170} />
              </div>
              <div className="grid grid-cols-3 divide-x divide-line/70 border-t border-line/70 text-center">
                {[
                  ["41", "trials counted"],
                  ["1", "survivor"],
                  ["0", "live hooks"],
                ].map(([value, label]) => (
                  <div key={label} className="px-2 py-3">
                    <p className="mono text-lg text-text">{value}</p>
                    <p className="label mt-0.5 text-[9px]">{label}</p>
                  </div>
                ))}
              </div>
            </div>
            <p className="mt-3 text-right font-mono text-[10px] text-muted/60">Illustrative demo data</p>
          </div>
        </div>
      </section>

      <section className="mx-auto grid max-w-6xl grid-cols-2 gap-px border-b border-line/60 px-5 py-0 md:grid-cols-4">
        {[
          ["10", "specialist agents"],
          ["1", "sealed holdout"],
          ["0", "live-trading paths"],
          ["100%", "replayable audit trail"],
        ].map(([value, label]) => (
          <div key={label} className="py-8 md:px-6">
            <p className="mono text-3xl tracking-tight text-text">{value}</p>
            <p className="mt-1 text-xs text-muted">{label}</p>
          </div>
        ))}
      </section>

      <section id="gauntlet" className="mx-auto max-w-6xl px-5 py-20">
        <div className="max-w-2xl">
          <p className="label">The falsification gauntlet</p>
          <h2 className="mt-3 text-3xl font-semibold tracking-tight md:text-4xl">
            A backtest is a hypothesis.
            <br />
            <span className="text-muted">This is the part that hurts.</span>
          </h2>
          <p className="mt-4 text-sm leading-relaxed text-muted">
            Every candidate runs the same ordered battery. Gates are explicit, versioned and shown to
            the user — including the ones that failed. Rejections are stored, not deleted.
          </p>
        </div>
        <div className="mt-10 grid gap-px overflow-hidden rounded-xl border border-line bg-line md:grid-cols-3">
          {GAUNTLET.map((item) => {
            const Icon = item.icon;
            return (
              <div key={item.title} className="bg-graphite p-6">
                <Icon size={17} className="text-signal" />
                <h3 className="mt-4 text-sm font-medium text-text">{item.title}</h3>
                <p className="mt-2 text-xs leading-relaxed text-muted">{item.body}</p>
              </div>
            );
          })}
        </div>
      </section>

      <section id="agents" className="border-y border-line/60 bg-graphite/40">
        <div className="mx-auto max-w-6xl px-5 py-20">
          <div className="flex flex-col justify-between gap-4 md:flex-row md:items-end">
            <div>
              <p className="label">The team</p>
              <h2 className="mt-3 text-3xl font-semibold tracking-tight">Ten roles. One skeptical mandate.</h2>
            </div>
            <p className="max-w-sm text-xs leading-relaxed text-muted">
              Typed messages, tool whitelists, budgets and a kill switch. The skeptic has no incentive to
              agree with the ideator.
            </p>
          </div>
          <div className="mt-8 grid gap-px overflow-hidden rounded-xl border border-line bg-line sm:grid-cols-2 lg:grid-cols-5">
            {AGENTS.map(([name, role], index) => (
              <div key={name} className="bg-graphite p-4">
                <div className="flex items-center justify-between">
                  <span className="mono text-[10px] text-muted">{String(index + 1).padStart(2, "0")}</span>
                  <span className="h-1.5 w-1.5 rounded-full bg-signal/70" />
                </div>
                <p className="mt-3 text-[13px] font-medium text-text">{name}</p>
                <p className="mt-1 text-[11px] leading-relaxed text-muted">{role}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section id="evidence" className="mx-auto max-w-6xl px-5 py-20">
        <div className="grid gap-10 md:grid-cols-[1fr_1.1fr]">
          <div>
            <p className="label">What survives looks like this</p>
            <h2 className="mt-3 text-3xl font-semibold tracking-tight">
              Numbers with units, periods and a verification state.
            </h2>
            <p className="mt-4 text-sm leading-relaxed text-muted">
              The leaderboard is not a return chart. It is a record of what passed, what failed, and how
              many attempts it took. A 1.14 Sharpe after 3× costs and 41 trials is a different claim from
              a 1.14 Sharpe you found on the first try.
            </p>
            <div className="mt-6 space-y-2.5">
              {[
                ["DSR 0.93", "≥ 0.90 against 41 trials", true],
                ["PBO 0.31", "≤ 0.35 overfit probability", true],
                ["3× cost Sharpe 0.46", "≥ 0.30 after stress", true],
                ["Holdout Sharpe 0.64", "opened once, after freeze", true],
              ].map(([value, note, pass]) => (
                <div
                  key={value as string}
                  className="flex items-center justify-between rounded-lg border border-line/80 bg-graphite/60 px-3.5 py-2.5"
                >
                  <div className="flex items-center gap-2.5">
                    <span
                      className={cn(
                        "grid h-4 w-4 place-items-center rounded-full",
                        pass ? "bg-signal/15 text-signal" : "bg-danger/15 text-danger",
                      )}
                    >
                      {pass ? <Check size={10} /> : <X size={10} />}
                    </span>
                    <span className="mono text-xs text-text">{value}</span>
                  </div>
                  <span className="font-mono text-[10px] text-muted">{note}</span>
                </div>
              ))}
            </div>
          </div>
          <div className="card overflow-hidden">
            <div className="flex items-center justify-between border-b border-line/70 px-4 py-3">
              <span className="label">Candidate · out-of-sample</span>
              <Badge tone="signal">validated</Badge>
            </div>
            <div className="px-2 pt-4">
              <EquityChart data={equitySeries(200, 5, 0.0007, 0.0068)} height={220} />
            </div>
            <div className="grid grid-cols-4 divide-x divide-line/70 border-t border-line/70">
              {[
                ["1.14", "Sharpe"],
                ["13.4%", "CAGR"],
                ["-11.8%", "max DD"],
                ["412", "trades"],
              ].map(([value, label]) => (
                <div key={label} className="px-3 py-3">
                  <p className="mono text-sm text-text">{value}</p>
                  <p className="label mt-1 text-[9px]">{label}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section className="border-t border-line/60">
        <div className="mx-auto max-w-6xl px-5 py-16">
          <div className="card relative overflow-hidden p-8 md:p-12">
            <div className="pointer-events-none absolute -right-20 -top-24 h-72 w-72 rounded-full bg-signal/10 blur-3xl" />
            <div className="relative flex flex-col justify-between gap-8 md:flex-row md:items-center">
              <div>
                <div className="flex items-center gap-2 text-signal">
                  <ShieldCheck size={16} />
                  <span className="label text-signal">research only</span>
                </div>
                <h2 className="mt-3 max-w-xl text-2xl font-semibold tracking-tight md:text-3xl">
                  Run the agents. Keep the receipts. Ship nothing you can&apos;t defend.
                </h2>
                <p className="mt-3 max-w-lg text-sm text-muted">
                  Start with demo data in one click. Connect the API when you want real sprints, your own
                  universe and a persistent audit trail.
                </p>
              </div>
              <Link
                href="/dashboard"
                className="focus-ring inline-flex shrink-0 items-center gap-2 rounded-lg bg-signal px-5 py-3 text-sm font-medium text-ink hover:bg-signal/90"
              >
                Open the lab <ArrowRight size={15} />
              </Link>
            </div>
          </div>
        </div>
      </section>

      <footer className="border-t border-line/60">
        <div className="mx-auto flex max-w-6xl flex-col justify-between gap-3 px-5 py-8 text-[11px] text-muted md:flex-row">
          <div className="flex items-center gap-3">
            <Logo compact />
            <span>© {new Date().getFullYear()} Nullpoint</span>
          </div>
          <p className="max-w-xl leading-relaxed">
            Research software only. Not investment advice, not a signal service, not a broker. Backtests
            are hypotheses; free data has licence and survivorship limitations.
          </p>
        </div>
      </footer>
    </div>
  );
}
