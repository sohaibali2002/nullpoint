"use client";

import { ArrowUpDown, Search } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { Badge, DataStamp, PageHeader, Panel, StatusBadge } from "@/components/ui";
import { useApi } from "@/lib/api";
import { formatMetric, num, when } from "@/lib/format";
import { mockStrategies } from "@/lib/mock";
import type { Strategy } from "@/lib/types";

const FILTERS = ["all", "candidate", "validated", "paper", "rejected", "backtested"] as const;

export default function StrategiesPage() {
  const { data: strategies, demo } = useApi<Strategy[]>("/strategies?sort=sharpe", mockStrategies);
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("all");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<"sharpe" | "recent">("sharpe");

  const rows = useMemo(() => {
    const filtered = strategies.filter((strategy) => {
      const statusOk = filter === "all" || strategy.status === filter;
      const queryOk =
        !query ||
        strategy.name.toLowerCase().includes(query.toLowerCase()) ||
        strategy.tags.some((tag) => tag.toLowerCase().includes(query.toLowerCase()));
      return statusOk && queryOk;
    });
    return filtered.sort((a, b) =>
      sort === "sharpe"
        ? Number(b.metrics.sharpe ?? 0) - Number(a.metrics.sharpe ?? 0)
        : new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
    );
  }, [strategies, filter, query, sort]);

  return (
    <div>
      <PageHeader
        eyebrow="Strategy registry"
        title="Only what survived is on this page."
        description="Every strategy is versioned, hashed and tied to its experiments. Promoted and rejected live side by side; deleting failures would make the trial count lie."
        actions={<DataStamp demo={demo} />}
      />

      <div className="mb-3 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div className="flex flex-wrap gap-1.5">
          {FILTERS.map((option) => (
            <button
              key={option}
              onClick={() => setFilter(option)}
              className={`focus-ring rounded-md border px-2.5 py-1.5 font-mono text-[10px] uppercase tracking-wider transition ${
                filter === option
                  ? "border-signal/40 bg-signal/10 text-signal"
                  : "border-line bg-graphite/60 text-muted hover:text-text"
              }`}
            >
              {option}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" />
            <input
              className="input h-8 w-52 pl-8 text-xs"
              placeholder="Filter name or tag"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          <button
            onClick={() => setSort((s) => (s === "sharpe" ? "recent" : "sharpe"))}
            className="focus-ring inline-flex h-8 items-center gap-1.5 rounded-lg border border-line bg-graphite/60 px-2.5 text-[11px] text-muted hover:text-text"
          >
            <ArrowUpDown size={12} /> {sort === "sharpe" ? "Sharpe" : "Recent"}
          </button>
        </div>
      </div>

      <Panel>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] border-collapse">
            <thead>
              <tr className="border-b border-line/60">
                <th className="table-head">Strategy</th>
                <th className="table-head">Status</th>
                <th className="table-head text-right">Sharpe</th>
                <th className="table-head text-right">CAGR</th>
                <th className="table-head text-right">Max DD</th>
                <th className="table-head text-right">Trades</th>
                <th className="table-head">Universe</th>
                <th className="table-head">Updated</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((strategy) => (
                <tr key={strategy.id} className="group transition-colors hover:bg-slate/40">
                  <td className="table-cell">
                    <Link href={`/strategies/${strategy.id}`} className="block">
                      <span className="text-[13px] text-text group-hover:text-signal">{strategy.name}</span>
                      <span className="mt-0.5 flex items-center gap-2 font-mono text-[10px] text-muted">
                        v{strategy.version}
                        {strategy.tags.slice(0, 2).map((tag) => (
                          <span key={tag} className="text-muted/70">#{tag}</span>
                        ))}
                      </span>
                    </Link>
                  </td>
                  <td className="table-cell"><StatusBadge status={strategy.status} /></td>
                  <td className="table-cell mono text-right text-xs text-text">
                    {num(Number(strategy.metrics.sharpe ?? 0))}
                  </td>
                  <td className="table-cell mono text-right text-xs text-muted">
                    {formatMetric("cagr", strategy.metrics.cagr)}
                  </td>
                  <td className="table-cell mono text-right text-xs text-danger/90">
                    {formatMetric("max_drawdown", strategy.metrics.max_drawdown)}
                  </td>
                  <td className="table-cell mono text-right text-xs text-muted">
                    {strategy.metrics.trades ?? "—"}
                  </td>
                  <td className="table-cell max-w-[160px] truncate font-mono text-[10px] text-muted">
                    {strategy.universe?.slice(0, 3).join(" · ") ?? "—"}
                  </td>
                  <td className="table-cell whitespace-nowrap text-[10px] text-muted">{when(strategy.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {rows.length === 0 && (
          <div className="px-5 py-14 text-center text-xs text-muted">No strategies match this filter.</div>
        )}
      </Panel>

      <div className="mt-3 flex items-center gap-2 text-[10px] text-muted/70">
        <Badge tone="warn">note</Badge>
        Sharpe on the visible sample is not evidence. Open a strategy to read every gate, including the
        ones that failed.
      </div>
    </div>
  );
}
