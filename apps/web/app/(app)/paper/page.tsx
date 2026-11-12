"use client";

import { Activity, Plus, Square } from "lucide-react";
import { useState } from "react";

import { EquityChart } from "@/components/charts";
import { Badge, Button, DataStamp, Notice, PageHeader, Panel, StatusBadge } from "@/components/ui";
import { api, getToken, useApi } from "@/lib/api";
import { pct, usd } from "@/lib/format";
import { equitySeries, mockPaper, mockStrategies } from "@/lib/mock";
import type { PaperRun, Strategy } from "@/lib/types";

export default function PaperPage() {
  const { data: runs, demo, reload } = useApi<PaperRun[]>("/paper", mockPaper);
  const { data: strategies } = useApi<Strategy[]>("/strategies", mockStrategies);
  const [selected, setSelected] = useState<string | null>(runs[0]?.run_id ?? null);
  const { data: detail, reload: reloadDetail } = useApi<PaperRun>(
    selected ? `/paper/${selected}` : null,
    { ...(runs[0] ?? mockPaper[0]), curve: equitySeries(30, 3, 0.0004, 0.004).map((p) => ({ ...p, ret: 0 })) },
  );
  const [strategyId, setStrategyId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const paperReady = strategies.filter((s) => s.status === "paper");
  const curve = detail?.curve?.length
    ? detail.curve.map((point) => ({ date: point.date, equity: point.equity }))
    : equitySeries(30, 3, 0.0004, 0.004);

  const start = async () => {
    setError(null);
    setNotice(null);
    if (!getToken()) {
      setError("Demo mode: start paper runs after signing in and approving a strategy.");
      return;
    }
    if (!strategyId) {
      setError("Choose a strategy that has been approved for paper.");
      return;
    }
    setBusy(true);
    try {
      const run = await api<PaperRun>("/paper", {
        method: "POST",
        body: JSON.stringify({ strategy_id: strategyId, initial_equity: 100000 }),
      });
      setSelected(run.run_id);
      setNotice("Paper run started. The simulator bootstraps the strategy's own out-of-sample returns.");
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start paper run");
    } finally {
      setBusy(false);
    }
  };

  const advance = async () => {
    if (!getToken() || !selected) {
      setError("Demo mode: advancing a run needs a signed-in session.");
      return;
    }
    setBusy(true);
    try {
      await api(`/paper/${selected}/mark?steps=10`, { method: "POST" });
      await reloadDetail();
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Mark failed");
    } finally {
      setBusy(false);
    }
  };

  const stop = async () => {
    if (!getToken() || !selected) {
      setError("Demo mode: stopping a run needs a signed-in session.");
      return;
    }
    setBusy(true);
    try {
      await api(`/paper/${selected}/stop`, { method: "POST" });
      await reloadDetail();
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Stop failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <PageHeader
        eyebrow="Forward simulation"
        title="Paper trading"
        description="The last step before 'live ready' — and the only place a strategy meets forward time. Nullpoint has no live order routing; paper is either a deterministic simulator or a read-only Alpaca paper snapshot."
        actions={<DataStamp demo={demo} />}
      />

      <div className="grid gap-3 xl:grid-cols-[1.45fr_1fr]">
        <Panel
          title="Equity"
          eyebrow={selected ? `run ${selected}` : "no run selected"}
          action={detail && <StatusBadge status={detail.status} />}
        >
          <div className="px-2 pt-4">
            <EquityChart data={curve} height={250} />
          </div>
          <div className="grid grid-cols-3 divide-x divide-line/60 border-t border-line/60">
            {[
              ["Equity", usd(detail?.equity ?? 0), "simulated"],
              ["Return", pct(detail?.return_pct ?? 0), "since start"],
              ["Broker", detail?.broker ?? "—", "paper only"],
            ].map(([label, value, hint]) => (
              <div key={label} className="px-4 py-3">
                <p className="mono text-sm text-text">{value}</p>
                <p className="label mt-1 text-[9px]">{label}</p>
                <p className="mt-0.5 text-[9px] text-muted">{hint}</p>
              </div>
            ))}
          </div>
        </Panel>

        <Panel title="Control" eyebrow="approval required before paper">
          <div className="space-y-4 px-4 py-4">
            <div>
              <span className="label">Approved strategy</span>
              <select
                className="input mt-1.5 text-xs"
                value={strategyId}
                onChange={(e) => setStrategyId(e.target.value)}
              >
                <option value="">Select…</option>
                {paperReady.map((strategy) => (
                  <option key={strategy.id} value={strategy.id} className="bg-graphite">
                    {strategy.name}
                  </option>
                ))}
              </select>
              {paperReady.length === 0 && (
                <p className="mt-1.5 text-[10px] text-warn">
                  No accepted strategy yet. Approve a candidate on its detail page.
                </p>
              )}
            </div>
            <div className="flex gap-2">
              <Button variant="primary" onClick={start} loading={busy} className="flex-1">
                <Plus size={13} /> Start run
              </Button>
              <Button variant="secondary" onClick={advance} loading={busy}>
                <Activity size={13} /> +10d
              </Button>
              <Button variant="danger" onClick={stop} loading={busy} disabled={detail?.status !== "running"}>
                <Square size={12} />
              </Button>
            </div>
            {error && <Notice tone="danger">{error}</Notice>}
            {notice && <Notice tone="signal">{notice}</Notice>}
            <Notice tone="warn">
              Simulator returns are bootstrapped from the strategy&apos;s own history. They are not observed
              market performance and must stay labelled as simulated.
            </Notice>
          </div>
        </Panel>
      </div>

      <Panel className="mt-3" title="Paper runs" eyebrow={`${runs.length} runs`}>
        <ul className="divide-y divide-line/60">
          {runs.map((run) => (
            <li key={run.run_id}>
              <button
                onClick={() => setSelected(run.run_id)}
                className={`flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-slate/40 ${
                  selected === run.run_id ? "bg-slate/30" : ""
                }`}
              >
                <div className="min-w-0 flex-1">
                  <p className="text-[12px] text-text">{run.strategy_id}</p>
                  <p className="mt-0.5 font-mono text-[10px] text-muted">
                    {run.broker} · {run.run_id}
                  </p>
                </div>
                <div className="text-right">
                  <p className="mono text-xs text-text">{usd(run.equity)}</p>
                  <p className={`mono text-[10px] ${run.return_pct >= 0 ? "text-signal" : "text-danger"}`}>
                    {pct(run.return_pct, 2)}
                  </p>
                </div>
                <StatusBadge status={run.status} />
              </button>
            </li>
          ))}
          {runs.length === 0 && (
            <li className="px-5 py-12 text-center text-xs text-muted">No paper runs yet.</li>
          )}
        </ul>
      </Panel>

      <div className="mt-3 flex items-center gap-2 text-[10px] text-muted/70">
        <Badge tone="danger">not live</Badge>
        There is no code path from Nullpoint to a broker order endpoint. Paper runs require a stored approval
        record from a human user.
      </div>
    </div>
  );
}
