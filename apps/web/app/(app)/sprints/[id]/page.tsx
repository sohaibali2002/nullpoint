"use client";

import { AlertTriangle, ArrowLeft, Lock, Square } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo, useState } from "react";

import { Badge, Button, DataStamp, Notice, Panel, PageHeader, StatusBadge } from "@/components/ui";
import { api, getToken, useApi, useLiveMessages } from "@/lib/api";
import { usd, when } from "@/lib/format";
import { mockAgentMessages, mockSprints } from "@/lib/mock";
import type { Sprint } from "@/lib/types";

export default function SprintDetailPage() {
  const params = useParams<{ id: string }>();
  const sprintId = params?.id ?? mockSprints[0].id;
  const fallback = useMemo(
    () => mockSprints.find((s) => s.id === sprintId) ?? mockSprints[0],
    [sprintId],
  );
  const { data: sprint, demo, reload } = useApi<Sprint>(`/sprints/${sprintId}`, fallback);
  const { messages, demo: messagesDemo } = useLiveMessages(sprintId, mockAgentMessages);
  const [error, setError] = useState<string | null>(null);
  const isDemo = demo || messagesDemo;

  const kill = async () => {
    if (!getToken()) {
      setError("Demo mode: stop requests need a signed-in session.");
      return;
    }
    try {
      await api(`/sprints/${sprintId}/kill`, { method: "POST" });
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Kill request failed");
    }
  };

  const dataset = sprint.result?.dataset as Record<string, any> | undefined;
  const holdout = sprint.result?.sealed_holdout as Record<string, any> | undefined;
  const budgetPct = sprint.budget_usd ? Math.min(100, (sprint.cost_usd / sprint.budget_usd) * 100) : 0;

  return (
    <div>
      <Link href="/sprints" className="mb-5 inline-flex items-center gap-1 text-[11px] text-muted hover:text-text">
        <ArrowLeft size={12} /> All sprints
      </Link>
      <PageHeader
        eyebrow={`Sprint · ${sprintId}`}
        title={sprint.objective}
        description={`${sprint.universe.join(" · ")} · created ${when(sprint.created_at)}`}
        actions={
          <>
            <DataStamp demo={isDemo} />
            <StatusBadge status={sprint.status} />
            {(sprint.status === "running" || sprint.status === "queued") && (
              <Button variant="danger" size="sm" onClick={kill}>
                <Square size={12} /> Stop
              </Button>
            )}
          </>
        }
      />

      {dataset?.synthetic && (
        <Notice tone="warn" className="mb-3">
          <AlertTriangle size={12} className="mr-1.5 inline" />
          This sprint ran on <strong>synthetic data</strong> because market data was unavailable. Results
          verify the pipeline only and are not market evidence.
        </Notice>
      )}
      {error && <Notice tone="danger" className="mb-3">{error}</Notice>}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[
          ["Trials", String(sprint.trials_used), "counted org-wide"],
          ["Spend", usd(sprint.cost_usd, 3), `of ${usd(sprint.budget_usd)}`],
          ["Strategies found", String(sprint.strategies_found), "survived all gates"],
          ["Audit messages", String(messages.length), "replayable"],
        ].map(([label, value, hint]) => (
          <div key={label} className="card px-4 py-4">
            <p className="label">{label}</p>
            <p className="mono mt-2 text-xl text-text">{value}</p>
            <p className="mt-1 text-[10px] text-muted">{hint}</p>
          </div>
        ))}
      </div>

      <div className="mt-3 grid gap-3 xl:grid-cols-[1.55fr_1fr]">
        <Panel
          title="Agent audit trail"
          eyebrow={isDemo ? "demo replay" : "persisted, resumable"}
          action={<Badge tone="beam">{messages.length} events</Badge>}
        >
          <ol className="divide-y divide-line/60">
            {messages.map((message) => (
              <li key={message.seq} className="px-4 py-3.5">
                <div className="flex items-center gap-2">
                  <span className="mono text-[10px] text-muted/70">#{String(message.seq).padStart(2, "0")}</span>
                  <span className="text-[12px] font-medium text-text">{message.agent}</span>
                  <Badge tone={message.role === "tool" ? "beam" : "neutral"}>{message.role}</Badge>
                  {message.tool_name && (
                    <span className="font-mono text-[10px] text-beam">{message.tool_name}()</span>
                  )}
                  <span className="ml-auto font-mono text-[9px] text-muted/60">{when(message.created_at)}</span>
                </div>
                <p className="mt-1.5 whitespace-pre-wrap text-[12px] leading-relaxed text-muted">
                  {message.content}
                </p>
                {(message.tokens_in || message.cost_usd) && (
                  <p className="mt-1.5 font-mono text-[9px] text-muted/60">
                    {message.tokens_in ?? 0} in · {message.tokens_out ?? 0} out · {usd(message.cost_usd ?? 0, 4)}
                  </p>
                )}
              </li>
            ))}
            {messages.length === 0 && (
              <li className="px-5 py-12 text-center text-xs text-muted">
                Waiting for the worker to persist the first message.
              </li>
            )}
          </ol>
        </Panel>

        <div className="space-y-3">
          <Panel title="Budget envelope" eyebrow="hard stops">
            <div className="px-5 py-4">
              <div className="flex items-end justify-between">
                <p className="mono text-lg text-text">{usd(sprint.cost_usd, 3)}</p>
                <p className="font-mono text-[10px] text-muted">limit {usd(sprint.budget_usd)}</p>
              </div>
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-line">
                <div
                  className={`h-full rounded-full ${budgetPct > 85 ? "bg-warn" : "bg-signal"}`}
                  style={{ width: `${Math.max(2, budgetPct)}%` }}
                />
              </div>
              <dl className="mt-4 space-y-2 text-[11px]">
                <div className="flex justify-between">
                  <dt className="text-muted">Tokens</dt>
                  <dd className="mono text-text">{sprint.tokens_used.toLocaleString()}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-muted">Kill requested</dt>
                  <dd className="mono text-text">{sprint.kill_requested ? "yes" : "no"}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-muted">Dataset</dt>
                  <dd className="mono text-text">{dataset?.synthetic ? "synthetic" : dataset?.provider ?? "—"}</dd>
                </div>
              </dl>
            </div>
          </Panel>

          <Panel title="Sealed holdout" eyebrow="untouched by agents">
            <div className="px-5 py-4">
              <div className="flex items-center gap-2 text-signal">
                <Lock size={13} />
                <span className="font-mono text-[11px]">
                  {holdout?.sealed ? `${holdout.sealed[0]} → ${holdout.sealed[1]}` : "sealed until final review"}
                </span>
              </div>
              <p className="mt-2 text-[11px] leading-relaxed text-muted">
                The last 20% of the sample is hashed before the sprint starts. Agents see only the visible
                portion; a human opens the seal once, on a frozen configuration.
              </p>
            </div>
          </Panel>

          {sprint.strategies && sprint.strategies.length > 0 && (
            <Panel title="Strategies from this sprint">
              <ul className="divide-y divide-line/60">
                {sprint.strategies.map((strategy) => (
                  <li key={strategy.id}>
                    <Link
                      href={`/strategies/${strategy.id}`}
                      className="flex items-center justify-between gap-2 px-4 py-3 hover:bg-slate/40"
                    >
                      <span className="truncate text-[12px] text-text">{strategy.name}</span>
                      <StatusBadge status={strategy.status} />
                    </Link>
                  </li>
                ))}
              </ul>
            </Panel>
          )}
        </div>
      </div>
    </div>
  );
}
