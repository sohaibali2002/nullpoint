"use client";

import { KeyRound, Save, SlidersHorizontal } from "lucide-react";
import { useState } from "react";

import { Badge, Button, DataStamp, Notice, PageHeader, Panel } from "@/components/ui";
import { api, getToken, useApi } from "@/lib/api";
import { pct, usd } from "@/lib/format";
import { mockSettings, mockUsage } from "@/lib/mock";
import type { SettingsData, UsageData } from "@/lib/types";

export default function SettingsPage() {
  const { data: settings, demo, reload } = useApi<SettingsData>("/settings", mockSettings);
  const { data: usage } = useApi<UsageData>("/usage", mockUsage);
  const [budget, setBudget] = useState<number>(settings.runtime?.sprint_budget_usd ?? 2);
  const [status, setStatus] = useState<{ tone: "signal" | "danger"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const save = async () => {
    setStatus(null);
    if (!getToken()) {
      setStatus({ tone: "danger", text: "Demo mode: sign in to persist settings." });
      return;
    }
    setBusy(true);
    try {
      await api("/settings", { method: "PATCH", body: JSON.stringify({ sprint_budget_usd: budget }) });
      setStatus({ tone: "signal", text: "Saved. New sprints will use this budget." });
      await reload();
    } catch (err) {
      setStatus({ tone: "danger", text: err instanceof Error ? err.message : "Save failed" });
    } finally {
      setBusy(false);
    }
  };

  const runtime = settings.runtime ?? {};
  const keys = Object.entries(settings.keys ?? {});

  return (
    <div>
      <PageHeader
        eyebrow="Workspace"
        title="Settings"
        description="Budgets, model slots, provider keys and the safety envelope. Secrets are never returned in full; set them in the server environment."
        actions={<DataStamp demo={demo} />}
      />

      <div className="grid gap-3 xl:grid-cols-[1.2fr_1fr]">
        <Panel title="Runtime" eyebrow="org-scoped configuration">
          <div className="space-y-4 px-4 py-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block">
                <span className="label">Sprint budget · USD</span>
                <input
                  type="number"
                  min={0.1}
                  step={0.5}
                  className="input mt-1.5 font-mono"
                  value={budget}
                  onChange={(e) => setBudget(Number(e.target.value))}
                />
              </label>
              <div>
                <span className="label">Monthly budget</span>
                <div className="input mt-1.5 flex items-center font-mono text-muted">
                  {usd(settings.org?.monthly_budget_usd ?? 0)}
                </div>
              </div>
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              {[
                ["LLM provider", runtime.llm_provider],
                ["Job runner", runtime.job_runner],
                ["Sandbox", runtime.sandbox_backend],
                ["Wall clock", `${runtime.sprint_wall_clock_minutes ?? "—"} min`],
                ["Iterations / agent", runtime.max_agent_iterations],
                ["Network in data layer", runtime.allow_network ? "enabled" : "disabled"],
              ].map(([label, value]) => (
                <div key={String(label)} className="flex items-center justify-between rounded-lg border border-line/70 bg-ink/30 px-3 py-2 text-[11px]">
                  <span className="text-muted">{label}</span>
                  <span className="mono text-text">{String(value ?? "—")}</span>
                </div>
              ))}
            </div>
            {status && <Notice tone={status.tone}>{status.text}</Notice>}
            <Button variant="primary" onClick={save} loading={busy}>
              <Save size={13} /> Save runtime
            </Button>
            <div className="rounded-lg border border-line/70 bg-ink/30 px-3 py-2.5">
              <div className="flex items-center gap-2">
                <SlidersHorizontal size={12} className="text-warn" />
                <p className="font-mono text-[10px] uppercase tracking-wider text-warn">Safety envelope</p>
              </div>
              <p className="mt-1.5 text-[10px] leading-relaxed text-muted">
                Live trading is disabled at the code level. The kill switch is {runtime.kill_switch ? "engaged" : "ready"}.
                Model overrides apply to new jobs; the worker may need a restart.
              </p>
            </div>
          </div>
        </Panel>

        <Panel title="Cost dashboard" eyebrow="last 30 days">
          <div className="grid grid-cols-2 divide-x divide-line/60 border-b border-line/60">
            <div className="px-4 py-4">
              <p className="label">LLM spend</p>
              <p className="mono mt-1.5 text-xl text-text">{usd(usage.total_llm_spend_usd, 2)}</p>
              <p className="mt-1 text-[10px] text-muted">
                {usage.budget_used_pct !== null ? `${usage.budget_used_pct.toFixed(1)}% of budget` : "no budget set"}
              </p>
            </div>
            <div className="px-4 py-4">
              <p className="label">Per candidate</p>
              <p className="mono mt-1.5 text-xl text-signal">
                {usage.cost_per_validated_strategy_usd !== null ? usd(usage.cost_per_validated_strategy_usd) : "—"}
              </p>
              <p className="mt-1 text-[10px] text-muted">{usage.validated_strategies} candidates · {usage.trials} trials</p>
            </div>
          </div>
          <ul className="divide-y divide-line/60">
            {usage.by_model.map((row) => (
              <li key={row.model} className="flex items-center justify-between gap-3 px-4 py-2.5">
                <span className="mono truncate text-[10px] text-muted">{row.model}</span>
                <span className="mono shrink-0 text-[10px] text-text">{usd(row.cost_usd, 2)}</span>
              </li>
            ))}
          </ul>
          <p className="border-t border-line/60 px-4 py-3 text-[10px] leading-relaxed text-muted">
            Data providers are free tier. LLM spend is the only metered cost, estimated from published
            per-token prices — not a billing record.
          </p>
        </Panel>
      </div>

      <div className="mt-3 grid gap-3 xl:grid-cols-2">
        <Panel title="Model slots" eyebrow="configurable per role">
          <ul className="divide-y divide-line/60">
            {Object.entries(settings.models ?? {}).map(([slot, model]) => (
              <li key={slot} className="flex items-center justify-between gap-3 px-4 py-3">
                <span className="text-[11px] capitalize text-muted">{slot}</span>
                <span className="mono text-[10px] text-text">{model}</span>
              </li>
            ))}
          </ul>
          <p className="border-t border-line/60 px-4 py-3 text-[10px] text-muted">
            Mock mode is a first-class provider: it runs the real tools and gates without an API key.
          </p>
        </Panel>

        <Panel title="Provider keys" eyebrow="presence only, never values">
          <ul className="divide-y divide-line/60">
            {keys.map(([name, value]) => (
              <li key={name} className="flex items-center justify-between gap-3 px-4 py-2.5">
                <span className="flex items-center gap-2 text-[11px] capitalize text-muted">
                  <KeyRound size={11} /> {name.replaceAll("_", " ")}
                </span>
                {value ? <Badge tone="signal">configured</Badge> : <Badge>missing</Badge>}
              </li>
            ))}
          </ul>
          <p className="border-t border-line/60 px-4 py-3 text-[10px] leading-relaxed text-muted">
            {settings.ignored_secrets}
          </p>
        </Panel>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2 text-[10px] text-muted/70">
        <Badge tone="signal">research only</Badge>
        No live-trading credentials are accepted. Alpaca, if configured, is restricted to paper-api.
      </div>
    </div>
  );
}
