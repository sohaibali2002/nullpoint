"use client";

import { ArrowLeft, Check, Download, Lock, Play, ShieldCheck, X } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo, useState } from "react";

import { EquityChart } from "@/components/charts";
import { Badge, Button, DataStamp, Notice, Panel, PageHeader, StatusBadge } from "@/components/ui";
import { api, getToken, useApi } from "@/lib/api";
import { formatMetric, metricLabel, num, pct, usd, when } from "@/lib/format";
import { equitySeries, mockStrategies, mockValidation } from "@/lib/mock";
import type { Experiment, Strategy, Validation } from "@/lib/types";

type StrategyDetail = Strategy & {
  validation?: Validation | null;
  validations?: Validation[];
  experiments?: Experiment[];
  lineage?: Strategy[];
  approvals?: Record<string, any>[];
};

const TABS = ["overview", "validation", "code", "lineage"] as const;

export default function StrategyDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id ?? mockStrategies[0].id;
  const fallback = useMemo(
    () => ({ ...mockStrategies.find((s) => s.id === id) ?? mockStrategies[0], validation: mockValidation, experiments: [], lineage: [] }),
    [id],
  );
  const { data: strategy, demo, reload } = useApi<StrategyDetail>(`/strategies/${id}`, fallback);
  const latestExperiment = strategy.experiments?.[0];
  const { data: experiment } = useApi<Experiment>(
    latestExperiment ? `/experiments/${latestExperiment.id}?include_series=true` : null,
    {} as Experiment,
  );
  const [tab, setTab] = useState<(typeof TABS)[number]>("overview");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const curve = useMemo(() => {
    if (experiment?.dates && experiment?.equity) {
      return experiment.dates.map((date, index) => ({ date, equity: experiment.equity?.[index] ?? 100 }));
    }
    if (experiment?.dates && experiment?.returns) {
      let value = 100;
      return experiment.dates.map((date, index) => {
        value *= 1 + (experiment.returns?.[index] ?? 0);
        return { date, equity: Number(value.toFixed(2)) };
      });
    }
    return equitySeries(220, 13);
  }, [experiment]);

  const validation = strategy.validation ?? strategy.validations?.[0];
  const metrics = strategy.metrics ?? {};

  const approve = async () => {
    setError(null);
    setNotice(null);
    if (!getToken()) {
      setError("Demo mode: approval needs a signed-in session and an approval record in Postgres.");
      return;
    }
    setBusy(true);
    try {
      const result = await api<{ status: string }>(`/strategies/${id}/approve`, {
        method: "POST",
        body: JSON.stringify({ target: "paper", note: "Gates reviewed in the UI" }),
      });
      setNotice(`Approved into ${result.status}. Paper trading is now allowed; live routing still does not exist.`);
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Approval failed");
    } finally {
      setBusy(false);
    }
  };

  const startPaper = async () => {
    setError(null);
    setNotice(null);
    if (!getToken()) {
      setError("Demo mode: start paper trading after signing in and approving this strategy.");
      return;
    }
    setBusy(true);
    try {
      const run = await api<{ run_id: string }>("/paper", {
        method: "POST",
        body: JSON.stringify({ strategy_id: id }),
      });
      setNotice(`Paper run ${run.run_id} started on the simulator. It is a bootstrap, not live performance.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start paper run");
    } finally {
      setBusy(false);
    }
  };

  const exportStrategy = async () => {
    if (!getToken()) {
      const blob = new Blob([JSON.stringify(strategy, null, 2)], { type: "application/json" });
      download(blob, `${strategy.name.slice(0, 30)}.json`);
      return;
    }
    try {
      const payload = await api<{ content: string; language: string }>(`/strategies/${id}/export?format=python`);
      download(
        new Blob([payload.content], { type: "text/x-python" }),
        `${strategy.name.slice(0, 30)}.py`,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Export failed");
    }
  };

  return (
    <div>
      <Link href="/strategies" className="mb-5 inline-flex items-center gap-1 text-[11px] text-muted hover:text-text">
        <ArrowLeft size={12} /> Strategy registry
      </Link>
      <PageHeader
        eyebrow={`Strategy · v${strategy.version} · trial #${strategy.trial_index ?? "—"}`}
        title={strategy.name}
        description={strategy.hypothesis}
        actions={
          <>
            <DataStamp demo={demo} />
            <StatusBadge status={strategy.status} />
            <Button size="sm" variant="secondary" onClick={exportStrategy}>
              <Download size={12} /> Export
            </Button>
            {strategy.status === "candidate" && (
              <Button size="sm" variant="primary" onClick={approve} loading={busy}>
                <ShieldCheck size={12} /> Approve for paper
              </Button>
            )}
            {strategy.status === "paper" && (
              <Button size="sm" variant="primary" onClick={startPaper} loading={busy}>
                <Play size={12} /> Start paper run
              </Button>
            )}
          </>
        }
      />

      {notice && <Notice tone="signal" className="mb-3">{notice}</Notice>}
      {error && <Notice tone="danger" className="mb-3">{error}</Notice>}
      {demo && (
        <Notice tone="warn" className="mb-3">
          Demo strategy. Values are illustrative; the validation gate names and thresholds match the
          backend.
        </Notice>
      )}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        {[
          ["Sharpe", num(Number(metrics.sharpe ?? 0)), "net, visible sample", "signal"],
          ["CAGR", formatMetric("cagr", metrics.cagr), "annualised", undefined],
          ["Max drawdown", formatMetric("max_drawdown", metrics.max_drawdown), "peak to trough", "danger"],
          ["Trades", String(metrics.trades ?? "—"), "min 100 to pass", undefined],
          ["Trial index", `#${strategy.trial_index ?? "—"}`, "global counter", undefined],
        ].map(([label, value, hint, tone]) => (
          <div key={label} className="card px-4 py-4">
            <p className="label">{label}</p>
            <p className={`mono mt-2 text-xl ${tone === "signal" ? "text-signal" : tone === "danger" ? "text-danger" : "text-text"}`}>
              {value}
            </p>
            <p className="mt-1 text-[10px] text-muted">{hint}</p>
          </div>
        ))}
      </div>

      <div className="mt-5 flex gap-1 border-b border-line/70">
        {TABS.map((option) => (
          <button
            key={option}
            onClick={() => setTab(option)}
            className={`focus-ring -mb-px border-b-2 px-3 py-2 font-mono text-[11px] uppercase tracking-wider transition ${
              tab === option
                ? "border-signal text-signal"
                : "border-transparent text-muted hover:text-text"
            }`}
          >
            {option}
          </button>
        ))}
      </div>

      {tab === "overview" && (
        <div className="mt-3 grid gap-3 xl:grid-cols-[1.5fr_1fr]">
          <Panel title="Equity · latest experiment" eyebrow={experiment?.kind ?? "backtest"} action={<Badge tone="signal">net of costs</Badge>}>
            <div className="px-2 pt-4">
              <EquityChart data={curve} height={280} />
            </div>
            <div className="grid grid-cols-2 divide-x divide-line/60 border-t border-line/60 sm:grid-cols-4">
              {Object.entries(metrics).slice(0, 8).map(([key, value]) => (
                <div key={key} className="px-4 py-3">
                  <p className="mono text-sm text-text">{formatMetric(key, value)}</p>
                  <p className="label mt-1 text-[9px]">{metricLabel(key)}</p>
                </div>
              ))}
            </div>
          </Panel>
          <div className="space-y-3">
            <Panel title="Definition" eyebrow="frozen configuration">
              <dl className="divide-y divide-line/60 text-[11px]">
                {[
                  ["Universe", strategy.universe?.join(" · ") ?? "—"],
                  ["Tags", strategy.tags.join(", ") || "—"],
                  ["Created", when(strategy.created_at)],
                  ["Code hash", strategy.code_hash ?? "not loaded"],
                  ["Holdout", "sealed · human-only"],
                ].map(([label, value]) => (
                  <div key={label} className="flex justify-between gap-4 px-4 py-2.5">
                    <dt className="shrink-0 text-muted">{label}</dt>
                    <dd className="mono truncate text-right text-text">{value}</dd>
                  </div>
                ))}
              </dl>
            </Panel>
            <Panel title="Promotion path" eyebrow="no shortcuts">
              <ol className="space-y-3 px-4 py-4">
                {["idea", "backtested", "validated", "candidate", "paper", "live_ready"].map((step) => {
                  const order = ["idea", "backtested", "validated", "candidate", "paper", "live_ready"];
                  const current = order.indexOf(strategy.status);
                  const index = order.indexOf(step);
                  const reached = index <= current;
                  return (
                    <li key={step} className="flex items-center gap-2.5 text-[11px]">
                      <span
                        className={`grid h-4 w-4 place-items-center rounded-full border ${
                          reached ? "border-signal/50 bg-signal/15 text-signal" : "border-line text-muted/40"
                        }`}
                      >
                        {reached ? <Check size={9} /> : <Lock size={8} />}
                      </span>
                      <span className={reached ? "text-text" : "text-muted"}>{step.replaceAll("_", " ")}</span>
                      {step === "live_ready" && (
                        <span className="ml-auto font-mono text-[9px] text-danger">label only</span>
                      )}
                    </li>
                  );
                })}
              </ol>
            </Panel>
          </div>
        </div>
      )}

      {tab === "validation" && (
        <div className="mt-3 grid gap-3">
          <Panel
            title="Falsification gauntlet"
            eyebrow={validation ? `${validation.passed ? "passed" : "failed"} · score ${num(validation.score)}` : "no report"}
            action={validation && <Badge tone={validation.passed ? "signal" : "danger"}>{validation.recommendation?.replaceAll("_", " ")}</Badge>}
          >
            {validation?.payload?.gates ? (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[640px] border-collapse">
                  <thead>
                    <tr className="border-b border-line/60">
                      <th className="table-head">Gate</th>
                      <th className="table-head">Result</th>
                      <th className="table-head text-right">Value</th>
                      <th className="table-head">Threshold</th>
                      <th className="table-head">Detail</th>
                    </tr>
                  </thead>
                  <tbody>
                    {validation.payload.gates.map((gate) => (
                      <tr key={gate.name}>
                        <td className="table-cell mono text-xs text-text">{gate.name.replaceAll("_", " ")}</td>
                        <td className="table-cell">
                          <span className={`inline-flex items-center gap-1 font-mono text-[10px] ${gate.passed ? "text-signal" : "text-danger"}`}>
                            {gate.passed ? <Check size={11} /> : <X size={11} />}
                            {gate.passed ? "pass" : "fail"}
                          </span>
                        </td>
                        <td className="table-cell mono text-right text-xs">
                          {typeof gate.value === "number" ? num(gate.value, 3) : "—"}
                        </td>
                        <td className="table-cell mono text-xs text-muted">{gate.threshold}</td>
                        <td className="table-cell text-xs text-muted">{gate.detail}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="px-5 py-12 text-center text-xs text-muted">
                No validation report stored for this strategy yet.
              </div>
            )}
          </Panel>
          <Notice tone="warn">
            Gates are hard constraints, not a score. One failed gate blocks promotion regardless of Sharpe.
          </Notice>
        </div>
      )}

      {tab === "code" && (
        <Panel className="mt-3" title="Signal code" eyebrow="trusted export only — run agent code in the sandbox">
          <pre className="max-h-[520px] overflow-auto px-5 py-4 font-mono text-[11px] leading-relaxed text-muted">
            {strategy.code ?? strategy.spec?.signal_code as string ?? "Code not loaded. Open the API strategy detail endpoint."}
          </pre>
        </Panel>
      )}

      {tab === "lineage" && (
        <Panel className="mt-3" title="Lineage" eyebrow="versions and derivations">
          <ul className="divide-y divide-line/60">
            {(strategy.lineage ?? []).map((item) => (
              <li key={item.id} className="flex items-center justify-between gap-3 px-4 py-3">
                <div>
                  <Link href={`/strategies/${item.id}`} className="text-[12px] text-text hover:text-signal">
                    {item.name}
                  </Link>
                  <p className="mono mt-0.5 text-[10px] text-muted">v{item.version} · {when(item.created_at)}</p>
                </div>
                <StatusBadge status={item.status} />
              </li>
            ))}
            {(strategy.lineage ?? []).length === 0 && (
              <li className="px-5 py-10 text-center text-xs text-muted">This strategy has no parents or children.</li>
            )}
          </ul>
        </Panel>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-3 text-[10px] text-muted/70">
        <span className="font-mono">hash {strategy.code_hash?.slice(0, 20) ?? "unavailable"}</span>
        <span>·</span>
        <span>Research only. Not investment advice.</span>
      </div>
    </div>
  );
}

function download(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}
