"use client";

import { FlaskConical, Lock } from "lucide-react";
import Link from "next/link";

import { Badge, DataStamp, PageHeader, Panel } from "@/components/ui";
import { useApi } from "@/lib/api";
import { formatMetric, when } from "@/lib/format";
import { mockExperiments } from "@/lib/mock";
import type { Experiment } from "@/lib/types";

export default function ExperimentsPage() {
  const { data: experiments, demo } = useApi<Experiment[]>("/experiments?limit=100", mockExperiments);
  const trials = new Set(experiments.map((experiment) => experiment.trial_index).filter(Boolean)).size;

  return (
    <div>
      <PageHeader
        eyebrow="Experiment ledger"
        title="Every trial counts."
        description="The global counter is incremented by every configuration tested, by every agent, in every sprint. It is the denominator behind the deflated Sharpe ratio."
        actions={<DataStamp demo={demo} />}
      />

      <div className="mb-3 grid gap-3 sm:grid-cols-3">
        {[
          ["Ledger entries", String(experiments.length), "this view"],
          ["Distinct trials", String(trials || 41), "org-wide counter"],
          ["Out-of-sample", String(experiments.filter((e) => e.is_oos).length), "walk-forward / stress"],
        ].map(([label, value, hint]) => (
          <div key={label} className="card px-4 py-4">
            <p className="label">{label}</p>
            <p className="mono mt-2 text-xl text-text">{value}</p>
            <p className="mt-1 text-[10px] text-muted">{hint}</p>
          </div>
        ))}
      </div>

      <Panel title="Ledger" eyebrow="reproducible by data hash, code hash and seed">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[860px] border-collapse">
            <thead>
              <tr className="border-b border-line/60">
                <th className="table-head">Experiment</th>
                <th className="table-head">Kind</th>
                <th className="table-head">Split</th>
                <th className="table-head text-right">Sharpe</th>
                <th className="table-head text-right">Return</th>
                <th className="table-head">Engine</th>
                <th className="table-head">Params</th>
                <th className="table-head">When</th>
              </tr>
            </thead>
            <tbody>
              {experiments.map((experiment) => (
                <tr key={experiment.id} className="hover:bg-slate/40">
                  <td className="table-cell">
                    <Link
                      href={`/strategies/${experiment.strategy_id}`}
                      className="mono text-[11px] text-text hover:text-signal"
                    >
                      {experiment.strategy_id}
                    </Link>
                    <p className="mono mt-0.5 text-[9px] text-muted/60">{experiment.id}</p>
                  </td>
                  <td className="table-cell">
                    <span className="inline-flex items-center gap-1.5 text-[11px] text-muted">
                      <FlaskConical size={11} className="text-beam" /> {experiment.kind}
                    </span>
                  </td>
                  <td className="table-cell">
                    <Badge tone={experiment.is_oos ? "signal" : "neutral"}>
                      {experiment.is_oos ? <Lock size={8} /> : null} {experiment.split ?? "in sample"}
                    </Badge>
                  </td>
                  <td className="table-cell mono text-right text-xs text-text">
                    {experiment.metrics?.sharpe !== undefined ? Number(experiment.metrics.sharpe).toFixed(2) : "—"}
                  </td>
                  <td className="table-cell mono text-right text-xs text-muted">
                    {experiment.metrics?.total_return !== undefined
                      ? formatMetric("total_return", experiment.metrics.total_return)
                      : "—"}
                  </td>
                  <td className="table-cell mono text-[10px] text-muted">{experiment.engine}</td>
                  <td className="table-cell max-w-[180px] truncate font-mono text-[10px] text-muted">
                    {JSON.stringify(experiment.params)}
                  </td>
                  <td className="table-cell whitespace-nowrap text-[10px] text-muted">{when(experiment.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <p className="mt-3 text-[10px] leading-relaxed text-muted/70">
        Data and code hashes are stored with each trial. Re-running a configuration with the same hashes
        and seed must reproduce the ledger entry; if it does not, the result is discarded.
      </p>
    </div>
  );
}
