"use client";

import { AlertTriangle, Database, ExternalLink, Search } from "lucide-react";
import { useState } from "react";

import { Badge, Button, DataStamp, Notice, PageHeader, Panel } from "@/components/ui";
import { api, getToken, useApi } from "@/lib/api";
import { mockProviders, mockUniverses } from "@/lib/mock";
import type { Provider, Universe } from "@/lib/types";

export default function DataPage() {
  const { data: providerData, demo } = useApi<{ providers: Provider[]; policy: string }>(
    "/data/providers",
    { providers: mockProviders, policy: "Free tiers only; check each licence before commercial use." },
  );
  const { data: universes } = useApi<Universe[]>("/data/universes", mockUniverses);
  const [symbol, setSymbol] = useState("SPY");
  const [preview, setPreview] = useState<{ rows: Record<string, number | string>[]; meta?: any } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const loadPreview = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    setPreview(null);
    if (!getToken()) {
      setError("Demo mode: cached previews need a signed-in session and a seeded data lake.");
      return;
    }
    setBusy(true);
    try {
      const payload = await api<{ rows: any[]; meta?: any }>(`/data/preview?symbol=${encodeURIComponent(symbol)}&limit=40`);
      setPreview(payload);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Preview failed");
    } finally {
      setBusy(false);
    }
  };

  const providers = providerData.providers ?? [];

  return (
    <div>
      <PageHeader
        eyebrow="Data explorer"
        title="Know what you are allowed to use."
        description="Providers, licences, coverage and bias warnings are part of the research record. Synthetic data is labelled as synthetic everywhere it travels."
        actions={<DataStamp demo={demo} />}
      />

      <div className="grid gap-3 sm:grid-cols-3">
        {[
          ["Providers", String(providers.length), `${providers.filter((p) => p.requires_key).length} require keys`],
          ["Cached assets", "Parquet", "DuckDB query surface"],
          ["Point-in-time", String(providers.filter((p) => p.point_in_time).length), "flagged providers"],
        ].map(([label, value, hint]) => (
          <div key={label} className="card px-4 py-4">
            <p className="label">{label}</p>
            <p className="mono mt-2 text-xl text-text">{value}</p>
            <p className="mt-1 text-[10px] text-muted">{hint}</p>
          </div>
        ))}
      </div>

      <Panel className="mt-3" title="Providers" eyebrow="licence notes are not optional">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[820px] border-collapse">
            <thead>
              <tr className="border-b border-line/60">
                <th className="table-head">Provider</th>
                <th className="table-head">Classes</th>
                <th className="table-head">Access</th>
                <th className="table-head">Rate limit</th>
                <th className="table-head">Licence</th>
                <th className="table-head">PIT</th>
              </tr>
            </thead>
            <tbody>
              {providers.map((provider) => (
                <tr key={provider.name} className="hover:bg-slate/40">
                  <td className="table-cell">
                    <div className="flex items-center gap-2">
                      <Database size={12} className="text-beam" />
                      <span className="mono text-xs text-text">{provider.name}</span>
                      {provider.available === false && <Badge tone="warn">key</Badge>}
                    </div>
                  </td>
                  <td className="table-cell font-mono text-[10px] text-muted">
                    {provider.asset_classes.join(" · ")}
                  </td>
                  <td className="table-cell text-[11px] text-muted">
                    {provider.requires_key ? "API key" : "public"}
                  </td>
                  <td className="table-cell mono text-[10px] text-muted">{provider.rate_limit_per_min}/min</td>
                  <td className="table-cell max-w-[420px] text-[10px] leading-relaxed text-muted">
                    {provider.license_note}
                    <a
                      href={provider.homepage}
                      target="_blank"
                      rel="noreferrer"
                      className="ml-1 inline-flex text-beam hover:underline"
                    >
                      <ExternalLink size={9} />
                    </a>
                  </td>
                  <td className="table-cell">
                    <Badge tone={provider.point_in_time ? "signal" : "neutral"}>
                      {provider.point_in_time ? "yes" : "no"}
                    </Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <div className="mt-3 grid gap-3 xl:grid-cols-2">
        <Panel title="Universes and bias" eyebrow="survivorship is a property of the universe">
          <ul className="divide-y divide-line/60">
            {universes.map((universe) => (
              <li key={universe.universe} className="px-4 py-4">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-[12px] text-text">{universe.label}</p>
                  <Badge tone={universe.survivorship_bias ? "warn" : "neutral"}>
                    {universe.survivorship_bias ? "survivorship" : universe.asset_class}
                  </Badge>
                </div>
                <p className="mt-1 font-mono text-[10px] text-muted">
                  {universe.symbols.slice(0, 10).join(" · ")}
                </p>
                <p className="mt-2 text-[10px] leading-relaxed text-warn/90">{universe.warning}</p>
              </li>
            ))}
          </ul>
        </Panel>

        <Panel title="Cached preview" eyebrow="Parquet lake · read-only">
          <form onSubmit={loadPreview} className="flex gap-2 px-4 pt-4">
            <div className="relative flex-1">
              <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" />
              <input
                className="input pl-8 font-mono text-xs uppercase"
                value={symbol}
                onChange={(e) => setSymbol(e.target.value.toUpperCase())}
                maxLength={24}
              />
            </div>
            <Button type="submit" variant="secondary" loading={busy}>Load</Button>
          </form>
          <div className="px-4 pb-4 pt-3">
            {error && <Notice tone="warn">{error}</Notice>}
            {!preview && !error && (
              <p className="py-10 text-center text-[11px] text-muted">
                Previews are served from cache only. Seed the lake with a sprint or the seed script.
              </p>
            )}
            {preview && (
              <div className="max-h-[300px] overflow-auto rounded-lg border border-line/60">
                <table className="w-full border-collapse text-[10px]">
                  <thead className="sticky top-0 bg-graphite">
                    <tr>
                      {Object.keys(preview.rows[0] ?? {}).map((key) => (
                        <th key={key} className="table-head">{key}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {preview.rows.map((row, index) => (
                      <tr key={index} className="border-t border-line/40">
                        {Object.keys(preview.rows[0] ?? {}).map((key) => (
                          <td key={key} className="mono px-2 py-1.5 text-muted">
                            {typeof row[key] === "number" ? Number(row[key]).toFixed(2) : String(row[key] ?? "—")}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </Panel>
      </div>

      <Notice tone="warn" className="mt-3">
        <AlertTriangle size={12} className="mr-1.5 inline" />
        Free data is not clean data. Yahoo endpoints are personal-use only, Stooq is not dividend-adjusted,
        and current-universe backtests carry survivorship bias. Nullpoint records these warnings with the
        results rather than hiding them.
      </Notice>
    </div>
  );
}
