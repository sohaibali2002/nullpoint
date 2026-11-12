"use client";

import { ArrowRight, Play, Square } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Button, DataStamp, Notice, Panel, PageHeader, StatusBadge } from "@/components/ui";
import { api, getToken, useApi } from "@/lib/api";
import { usd, when } from "@/lib/format";
import { mockSprints } from "@/lib/mock";
import type { Sprint } from "@/lib/types";

export default function SprintsPage() {
  const { data: sprints, demo, loading, reload } = useApi<Sprint[]>("/sprints", mockSprints);
  const [objective, setObjective] = useState(
    "Find cross-sectional momentum strategies that survive 3× costs on sector ETFs",
  );
  const [universe, setUniverse] = useState("SPY, QQQ, XLK, XLF, XLE, XLV");
  const [maxStrategies, setMaxStrategies] = useState(2);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const launch = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    setMessage(null);
    if (!getToken()) {
      setError("Demo mode: sign in to launch a real sprint. The API needs an org, a budget and a worker.");
      return;
    }
    setBusy(true);
    try {
      const payload = await api<Sprint>("/sprints", {
        method: "POST",
        body: JSON.stringify({
          objective,
          universe: universe.split(",").map((s) => s.trim()).filter(Boolean),
          max_strategies: maxStrategies,
        }),
      });
      setMessage(
        payload.status === "completed"
          ? "Sprint completed synchronously. Open it to read the audit trail."
          : "Sprint queued. The worker will stream messages into the audit trail.",
      );
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not launch sprint");
    } finally {
      setBusy(false);
    }
  };

  const kill = async (id: string) => {
    if (!getToken()) {
      setError("Demo mode: killing a sprint requires a signed-in session.");
      return;
    }
    try {
      await api(`/sprints/${id}/kill`, { method: "POST" });
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Kill request failed");
    }
  };

  return (
    <div>
      <PageHeader
        eyebrow="Live research"
        title="Agent sprints"
        description="A sprint is a bounded research run: an objective, a universe, a budget and an audit trail. Every message and every trial is persisted."
        actions={<DataStamp demo={demo} />}
      />

      <div className="grid gap-3 xl:grid-cols-[1fr_1.45fr]">
        <Panel title="Launch a sprint" eyebrow="objective → universe → budget">
          <form onSubmit={launch} className="space-y-4 px-5 py-5">
            <label className="block">
              <span className="label">Research objective</span>
              <textarea
                className="input mt-1.5 min-h-[92px] resize-y"
                value={objective}
                onChange={(e) => setObjective(e.target.value)}
                minLength={8}
                required
              />
            </label>
            <label className="block">
              <span className="label">Universe · comma separated</span>
              <input
                className="input mt-1.5 font-mono text-xs"
                value={universe}
                onChange={(e) => setUniverse(e.target.value)}
                required
              />
            </label>
            <div className="grid grid-cols-2 gap-3">
              <label className="block">
                <span className="label">Strategy attempts</span>
                <input
                  type="number"
                  min={1}
                  max={12}
                  className="input mt-1.5 font-mono"
                  value={maxStrategies}
                  onChange={(e) => setMaxStrategies(Number(e.target.value))}
                />
              </label>
              <div>
                <span className="label">Safety</span>
                <div className="mt-1.5 rounded-lg border border-line bg-ink/40 px-3 py-2 text-[11px] text-muted">
                  Sealed holdout on · live hooks off
                </div>
              </div>
            </div>
            {error && <Notice tone="danger">{error}</Notice>}
            {message && <Notice tone="signal">{message}</Notice>}
            <Button type="submit" variant="primary" loading={busy} className="w-full">
              <Play size={14} /> Run agents
            </Button>
            <p className="text-[10px] leading-relaxed text-muted">
              Real runs execute in a sandboxed worker, not in the API process. In mock mode the sprint is
              deterministic and still runs the real backtests and gates.
            </p>
          </form>
        </Panel>

        <Panel title="Runs" eyebrow={loading ? "loading" : `${sprints.length} sprints`}>
          <ul className="divide-y divide-line/60">
            {sprints.map((sprint) => {
              const live = sprint.status === "running" || sprint.status === "queued";
              const budgetUsed = sprint.budget_usd ? Math.min(1, sprint.cost_usd / sprint.budget_usd) : 0;
              return (
                <li key={sprint.id} className="px-4 py-4">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <Link href={`/sprints/${sprint.id}`} className="text-[13px] text-text hover:text-signal">
                        {sprint.objective}
                      </Link>
                      <p className="mt-1 truncate font-mono text-[10px] text-muted">
                        {sprint.universe.slice(0, 8).join(" · ")} {sprint.universe.length > 8 ? "…" : ""}
                      </p>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      <StatusBadge status={sprint.status} />
                      {live && getToken() && (
                        <button
                          onClick={() => kill(sprint.id)}
                          className="focus-ring rounded-md p-1 text-muted hover:text-danger"
                          title="Request stop"
                        >
                          <Square size={12} />
                        </button>
                      )}
                    </div>
                  </div>
                  <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
                    {[
                      ["trials", String(sprint.trials_used)],
                      ["spend", usd(sprint.cost_usd, 3)],
                      ["found", String(sprint.strategies_found)],
                      ["created", when(sprint.created_at)],
                    ].map(([label, value]) => (
                      <div key={label}>
                        <p className="label text-[9px]">{label}</p>
                        <p className="mono mt-0.5 text-[11px] text-text">{value}</p>
                      </div>
                    ))}
                  </div>
                  <div className="mt-3 flex items-center gap-2">
                    <div className="h-1 flex-1 overflow-hidden rounded-full bg-line/60">
                      <div
                        className={`h-full rounded-full ${budgetUsed > 0.85 ? "bg-warn" : "bg-signal/70"}`}
                        style={{ width: `${Math.max(2, budgetUsed * 100)}%` }}
                      />
                    </div>
                    <span className="font-mono text-[9px] text-muted">
                      {usd(sprint.cost_usd, 3)} / {usd(sprint.budget_usd, 2)}
                    </span>
                    <Link href={`/sprints/${sprint.id}`} className="text-muted hover:text-text">
                      <ArrowRight size={13} />
                    </Link>
                  </div>
                </li>
              );
            })}
            {sprints.length === 0 && (
              <li className="px-5 py-12 text-center text-xs text-muted">No sprints yet. Launch one on the left.</li>
            )}
          </ul>
        </Panel>
      </div>
    </div>
  );
}
