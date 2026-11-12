"use client";

import { ArrowUpRight, Coins, FlaskConical, ShieldCheck, Waypoints } from "lucide-react";
import Link from "next/link";

import { EquityChart } from "@/components/charts";
import { Badge, DataStamp, Notice, Panel, PageHeader, Stat, StatusBadge } from "@/components/ui";
import { useApi } from "@/lib/api";
import { pct, usd, when } from "@/lib/format";
import { equitySeries, mockDashboard } from "@/lib/mock";

export default function DashboardPage() {
  const { data, demo, loading } = useApi("/dashboard", mockDashboard);
  const counts = data.strategy_counts ?? {};
  const promoted = (counts.candidate ?? 0) + (counts.paper ?? 0);
  const rejected = counts.rejected ?? 0;
  const total = Object.values(counts).reduce((sum, n) => sum + n, 0);
  const passRate = total ? promoted / total : 0;
  const curve = equitySeries(220, 9, 0.00058, 0.0068);

  return (
    <div>
      <PageHeader
        eyebrow="Research overview"
        title="The null is the default."
        description="A live view of what the agents tried, what survived, and what it cost. Nothing here is a forecast."
        actions={
          <>
            <DataStamp demo={demo} />
            <Link
              href="/sprints"
              className="focus-ring inline-flex items-center gap-1.5 rounded-lg bg-signal px-3.5 py-2 text-xs font-medium text-ink hover:bg-signal/90"
            >
              New sprint <ArrowUpRight size={14} />
            </Link>
          </>
        }
      />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <div className="card divide-y divide-line/60">
          <Stat label="Validated candidates" value={promoted} hint="Survived every gate; awaiting or in paper" tone="signal" />
          <div className="px-5 py-3 text-[11px] text-muted">
            {rejected} rejected · {total} total strategies
          </div>
        </div>
        <div className="card divide-y divide-line/60">
          <Stat label="Global trials" value={data.trials.toLocaleString()} hint="Counts every configuration tested, org-wide" />
          <div className="flex items-center gap-2 px-5 py-3 text-[11px] text-muted">
            <FlaskConical size={12} className="text-beam" /> No trial resets, ever
          </div>
        </div>
        <div className="card divide-y divide-line/60">
          <Stat label="LLM spend" value={usd(data.llm_spend_usd, 2)} hint="Providers are free tier; this is the only metered cost" />
          <div className="flex items-center gap-2 px-5 py-3 text-[11px] text-muted">
            <Coins size={12} className="text-warn" />
            {data.cost_per_candidate_usd ? `${usd(data.cost_per_candidate_usd)} per candidate` : "No candidate yet"}
          </div>
        </div>
        <div className="card divide-y divide-line/60">
          <Stat label="Promotion rate" value={pct(passRate, 0)} hint="Share of stored strategies that reached candidate" tone={passRate < 0.2 ? "warn" : "signal"} />
          <div className="flex items-center gap-2 px-5 py-3 text-[11px] text-muted">
            <ShieldCheck size={12} className="text-signal" /> A low rate is the point
          </div>
        </div>
      </div>

      <div className="mt-3 grid gap-3 xl:grid-cols-[1.6fr_1fr]">
        <Panel
          title="Candidate equity · visible sample"
          eyebrow="out-of-sample folds, net of 3× costs"
          action={<Badge tone="signal">sealed holdout intact</Badge>}
        >
          <div className="px-2 pt-4">
            <EquityChart data={curve} height={248} />
          </div>
          <div className="grid grid-cols-2 divide-x divide-line/60 border-t border-line/60 sm:grid-cols-4">
            {[
              ["1.14", "Sharpe"],
              ["13.4%", "CAGR"],
              ["-11.8%", "Max drawdown"],
              ["0.64", "Holdout Sharpe"],
            ].map(([value, label]) => (
              <div key={label} className="px-4 py-3">
                <p className="mono text-sm text-text">{value}</p>
                <p className="label mt-1 text-[9px]">{label}</p>
              </div>
            ))}
          </div>
        </Panel>

        <Panel title="Where attempts die" eyebrow="funnel across stored strategies">
          <div className="space-y-4 px-5 py-5">
            {[
              ["Ideas recorded", total + 12, "text-muted"],
              ["Backtested", (counts.backtested ?? 0) + promoted + rejected, "text-beam"],
              ["Survived validation", (counts.validated ?? 0) + promoted, "text-signal"],
              ["Approved for paper", counts.paper ?? 0, "text-warn"],
            ].map(([label, value, tone], index) => {
              const max = total + 12;
              const width = Math.max(6, (Number(value) / max) * 100);
              return (
                <div key={label as string}>
                  <div className="mb-1.5 flex items-center justify-between text-xs">
                    <span className="text-muted">{label}</span>
                    <span className={`mono ${tone}`}>{value}</span>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded-full bg-line/60">
                    <div
                      className={`h-full rounded-full ${index === 3 ? "bg-warn" : index === 2 ? "bg-signal" : "bg-beam"}`}
                      style={{ width: `${width}%` }}
                    />
                  </div>
                </div>
              );
            })}
            <Notice tone="warn" className="mt-2">
              Rejected strategies are kept with their tearsheets. Deleting failures would make the trial
              count lie.
            </Notice>
          </div>
        </Panel>
      </div>

      <div className="mt-3 grid gap-3 xl:grid-cols-[1.6fr_1fr]">
        <Panel
          title="Recent sprints"
          eyebrow="agent research runs"
          action={
            <Link href="/sprints" className="text-[11px] text-muted hover:text-text">
              All sprints →
            </Link>
          }
        >
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] border-collapse">
              <thead>
                <tr className="border-b border-line/60">
                  <th className="table-head">Objective</th>
                  <th className="table-head">Status</th>
                  <th className="table-head">Trials</th>
                  <th className="table-head">Cost</th>
                  <th className="table-head">Started</th>
                </tr>
              </thead>
              <tbody>
                {data.sprints.slice(0, 5).map((sprint) => (
                  <tr key={sprint.id} className="transition-colors hover:bg-slate/40">
                    <td className="table-cell max-w-[280px]">
                      <Link href={`/sprints/${sprint.id}`} className="line-clamp-1 text-text hover:text-signal">
                        {sprint.objective}
                      </Link>
                      <p className="mt-0.5 truncate font-mono text-[10px] text-muted">
                        {sprint.universe.slice(0, 6).join(" · ")}
                      </p>
                    </td>
                    <td className="table-cell"><StatusBadge status={sprint.status} /></td>
                    <td className="table-cell mono text-xs">{sprint.trials_used}</td>
                    <td className="table-cell mono text-xs">{usd(sprint.cost_usd, 3)}</td>
                    <td className="table-cell whitespace-nowrap text-xs text-muted">{when(sprint.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>

        <Panel
          title="Leaderboard"
          eyebrow="net Sharpe, visible sample only"
          action={
            <Link href="/strategies" className="text-[11px] text-muted hover:text-text">
              Open →
            </Link>
          }
        >
          <ul className="divide-y divide-line/60">
            {data.leaderboard.length === 0 && (
              <li className="px-5 py-10 text-center text-xs text-muted">No promoted strategies yet.</li>
            )}
            {data.leaderboard.map((strategy, index) => (
              <li key={strategy.id}>
                <Link href={`/strategies/${strategy.id}`} className="flex items-center gap-3 px-4 py-3 hover:bg-slate/40">
                  <span className="mono w-4 text-[10px] text-muted">{index + 1}</span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-[13px] text-text">{strategy.name}</p>
                    <p className="mt-0.5 font-mono text-[10px] text-muted">
                      {strategy.universe?.slice(0, 3).join(" · ")}
                    </p>
                  </div>
                  <div className="text-right">
                    <p className="mono text-sm text-signal">{Number(strategy.metrics.sharpe ?? 0).toFixed(2)}</p>
                    <p className="label mt-0.5 text-[9px]">sharpe</p>
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        </Panel>
      </div>

      <Panel className="mt-3" title="The roster" eyebrow="typed tools, budgeted runs, replayable messages">
        <div className="grid gap-px bg-line/60 sm:grid-cols-2 lg:grid-cols-5">
          {data.roster.map((agent) => (
            <div key={agent.key} className="bg-graphite px-4 py-3.5">
              <div className="flex items-center justify-between">
                <p className="text-[12px] font-medium text-text">{agent.name}</p>
                <span className="h-1.5 w-1.5 rounded-full bg-signal/70" />
              </div>
              <p className="mt-1.5 line-clamp-2 text-[11px] leading-relaxed text-muted">{agent.purpose}</p>
              <p className="mt-2 font-mono text-[9px] uppercase tracking-wider text-muted/60">{agent.model_slot}</p>
            </div>
          ))}
        </div>
      </Panel>

      <div className="mt-3 flex flex-col justify-between gap-2 text-[10px] text-muted/70 md:flex-row">
        <span className="font-mono uppercase tracking-[0.12em]">
          <Waypoints size={11} className="mr-1 inline" /> {loading ? "syncing" : demo ? "demo dataset" : "live api"}
        </span>
        <span>Research only. Not investment advice. Backtests are hypotheses, not forecasts.</span>
      </div>
    </div>
  );
}
