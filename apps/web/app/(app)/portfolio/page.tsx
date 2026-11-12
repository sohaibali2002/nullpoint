"use client";

import { Boxes, Check } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { WeightBars } from "@/components/charts";
import { Badge, Button, DataStamp, Notice, PageHeader, Panel, StatusBadge } from "@/components/ui";
import { api, getToken, useApi } from "@/lib/api";
import { formatMetric, metricLabel, num, pct, when } from "@/lib/format";
import { mockPortfolios, mockStrategies } from "@/lib/mock";
import type { Portfolio, Strategy } from "@/lib/types";

const METHODS = [
  ["hrp", "Hierarchical risk parity", "Clusters correlated strategies, then allocates by cluster variance."],
  ["risk_parity", "Risk parity", "Equal risk contribution from a fixed-point covariance solve."],
  ["inverse_vol", "Inverse volatility", "Weights proportional to 1/σ, capped per strategy."],
  ["mean_variance", "Mean-variance", "Long-only shrinkage covariance, capped weights."],
] as const;

export default function PortfolioPage() {
  const { data: strategies } = useApi<Strategy[]>("/strategies?sort=sharpe", mockStrategies);
  const { data: portfolios, demo, reload } = useApi<Portfolio[]>("/portfolios", mockPortfolios);
  const [selected, setSelected] = useState<string[]>([]);
  const [method, setMethod] = useState<(typeof METHODS)[number][0]>("hrp");
  const [result, setResult] = useState<Portfolio | null>(portfolios[0] ?? null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const eligible = useMemo(
    () => strategies.filter((strategy) => ["candidate", "validated", "paper"].includes(strategy.status)),
    [strategies],
  );

  useEffect(() => {
    if (selected.length === 0 && eligible.length >= 2) {
      setSelected(eligible.slice(0, Math.min(3, eligible.length)).map((s) => s.id));
    }
  }, [eligible, selected.length]);

  const toggle = (id: string) => {
    setSelected((current) =>
      current.includes(id) ? current.filter((value) => value !== id) : [...current, id],
    );
  };

  const construct = async () => {
    setError(null);
    if (selected.length < 2) {
      setError("Select at least two surviving strategies.");
      return;
    }
    if (!getToken()) {
      setError("Demo mode: portfolio construction runs on stored experiment returns and needs the API.");
      return;
    }
    setBusy(true);
    try {
      const payload = await api<Portfolio>("/portfolios", {
        method: "POST",
        body: JSON.stringify({ strategy_ids: selected, method, max_weight: 0.4 }),
      });
      setResult(payload);
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Construction failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <PageHeader
        eyebrow="What-if construction"
        title="Portfolio builder"
        description="Combine strategies that already survived the gauntlet. Weights are constructed from backtest returns, not live performance — this is a research what-if, not an allocation."
        actions={<DataStamp demo={demo} />}
      />

      <div className="grid gap-3 xl:grid-cols-[1fr_1.2fr]">
        <Panel title="Select survivors" eyebrow="candidate / validated / paper">
          <ul className="divide-y divide-line/60">
            {eligible.map((strategy) => {
              const active = selected.includes(strategy.id);
              return (
                <li key={strategy.id}>
                  <button
                    onClick={() => toggle(strategy.id)}
                    className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-slate/40"
                  >
                    <span
                      className={`grid h-4 w-4 place-items-center rounded border ${
                        active ? "border-signal bg-signal text-ink" : "border-line bg-ink/40"
                      }`}
                    >
                      {active && <Check size={10} />}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[12px] text-text">{strategy.name}</span>
                      <span className="font-mono text-[10px] text-muted">
                        Sharpe {num(Number(strategy.metrics.sharpe ?? 0))} · v{strategy.version}
                      </span>
                    </span>
                    <StatusBadge status={strategy.status} />
                  </button>
                </li>
              );
            })}
            {eligible.length < 2 && (
              <li className="px-5 py-10 text-center text-xs text-muted">
                At least two surviving strategies are needed. Approve candidates first.
              </li>
            )}
          </ul>
        </Panel>

        <Panel title="Method" eyebrow="no optimiser magic">
          <div className="space-y-2 px-4 py-4">
            {METHODS.map(([value, label, body]) => (
              <button
                key={value}
                onClick={() => setMethod(value)}
                className={`w-full rounded-lg border px-3.5 py-3 text-left transition ${
                  method === value
                    ? "border-signal/40 bg-signal/5"
                    : "border-line bg-ink/30 hover:border-muted/30"
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="text-[12px] text-text">{label}</span>
                  {method === value && <Badge tone="signal">selected</Badge>}
                </div>
                <p className="mt-1 text-[10px] leading-relaxed text-muted">{body}</p>
              </button>
            ))}
            {error && <Notice tone="danger">{error}</Notice>}
            <Button variant="primary" onClick={construct} loading={busy} className="mt-1 w-full">
              <Boxes size={14} /> Construct portfolio
            </Button>
          </div>
        </Panel>
      </div>

      {result && (
        <div className="mt-3 grid gap-3 xl:grid-cols-[1.2fr_1fr]">
          <Panel
            title={result.name}
            eyebrow={`${result.method.toUpperCase()} · ${Object.keys(result.weights).length} sleeves`}
            action={<Badge tone="signal">research what-if</Badge>}
          >
            <div className="px-2 pt-5">
              <WeightBars weights={result.weights} />
            </div>
            <div className="grid grid-cols-2 divide-x divide-line/60 border-t border-line/60 sm:grid-cols-4">
              {Object.entries(result.metrics).map(([key, value]) => (
                <div key={key} className="px-4 py-3">
                  <p className="mono text-sm text-text">{formatMetric(key, value)}</p>
                  <p className="label mt-1 text-[9px]">{metricLabel(key)}</p>
                </div>
              ))}
            </div>
          </Panel>
          <Panel title="Saved constructions" eyebrow="stored weights, versioned inputs">
            <ul className="divide-y divide-line/60">
              {portfolios.map((portfolio) => (
                <li key={portfolio.id} className="px-4 py-3">
                  <div className="flex items-center justify-between">
                    <p className="text-[12px] text-text">{portfolio.name}</p>
                    <span className="mono text-[10px] text-muted">{when(portfolio.created_at)}</span>
                  </div>
                  <p className="mt-1 font-mono text-[10px] text-muted">
                    Sharpe {num(Number(portfolio.metrics.sharpe ?? 0))} · CAGR{" "}
                    {pct(Number(portfolio.metrics.cagr ?? 0))} · DD{" "}
                    {pct(Number(portfolio.metrics.max_drawdown ?? 0))}
                  </p>
                </li>
              ))}
              {portfolios.length === 0 && (
                <li className="px-5 py-10 text-center text-xs text-muted">No saved constructions.</li>
              )}
            </ul>
            <div className="border-t border-line/60 px-4 py-3">
              <p className="text-[10px] leading-relaxed text-muted">
                Correlation between sleeves is computed on the visible sample. Combining backtests does not
                eliminate overfitting; each input already passed on its own.
              </p>
            </div>
          </Panel>
        </div>
      )}
    </div>
  );
}
