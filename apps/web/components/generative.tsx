"use client";

import { AlertTriangle, CheckCircle2, CircleDashed, Info, XCircle } from "lucide-react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Badge } from "@/components/ui";
import type { ChatBlock } from "@/lib/types";

const PALETTE = ["#5FE3C0", "#7AA2FF", "#F2B34B", "#FF6B6B", "#99A1B3"];
const LINE = "#252A36";

const tooltipStyle = {
  background: "#101218",
  border: `1px solid ${LINE}`,
  borderRadius: 8,
  fontFamily: "var(--font-mono), monospace",
  fontSize: 11,
  color: "#E9ECF3",
  padding: "8px 10px",
};

function Frame({
  title,
  label,
  children,
}: {
  title?: string;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-line/80 bg-ink/40 p-3">
      <div className="mb-2 flex items-center gap-2">
        <span className="font-mono text-[9px] uppercase tracking-[0.12em] text-signal/80">{label}</span>
        {title && <span className="truncate text-[11px] font-medium text-text">{title}</span>}
      </div>
      {children}
    </div>
  );
}

function Metrics({ block }: { block: ChatBlock }) {
  const items = block.items ?? [];
  if (!items.length) return null;
  return (
    <Frame label="metrics" title={block.title}>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {items.map((item, index) => (
          <div key={`${item.label}-${index}`} className="rounded-lg border border-line/60 bg-slate/30 px-2.5 py-2">
            <p className="font-mono text-[9px] uppercase tracking-wider text-muted/70">{item.label}</p>
            <p className="mt-0.5 font-mono text-[15px] text-text">{item.value ?? "—"}</p>
            {item.hint && <p className="mt-0.5 text-[9px] text-muted/60">{item.hint}</p>}
          </div>
        ))}
      </div>
    </Frame>
  );
}

function Table({ block }: { block: ChatBlock }) {
  const columns = block.columns ?? [];
  const rows = block.rows ?? [];
  if (!columns.length || !rows.length) return null;
  return (
    <Frame label="table" title={block.title}>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-left">
          <thead>
            <tr>
              {columns.map((column) => (
                <th
                  key={column}
                  className="border-b border-line/70 pb-1.5 pr-3 font-mono text-[9px] uppercase tracking-wider text-muted/70"
                >
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {row.map((cell, cellIndex) => (
                  <td key={cellIndex} className="border-b border-line/40 py-1.5 pr-3 font-mono text-[11px] text-muted">
                    {String(cell)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Frame>
  );
}

function Checklist({ block }: { block: ChatBlock }) {
  const items = block.items ?? [];
  if (!items.length) return null;
  return (
    <Frame label="next checks" title={block.title}>
      <ul className="space-y-1.5">
        {items.map((item, index) => (
          <li key={`${item.label}-${index}`} className="flex gap-2">
            <CircleDashed size={12} className="mt-0.5 shrink-0 text-signal/70" />
            <div>
              <p className="text-[11.5px] font-medium text-text">{item.label}</p>
              {item.detail && <p className="mt-0.5 text-[10.5px] leading-relaxed text-muted">{item.detail}</p>}
            </div>
          </li>
        ))}
      </ul>
    </Frame>
  );
}

function Chart({ block }: { block: ChatBlock }) {
  const series = (block.series ?? []).filter((item) => item.points?.length);
  if (!series.length) return null;
  const length = Math.max(...series.map((item) => item.points.length));
  const data = Array.from({ length }, (_, index) => {
    const row: Record<string, string | number> = {
      x: String(series[0].points[index]?.[0] ?? index),
    };
    series.forEach((item, seriesIndex) => {
      row[`s${seriesIndex}`] = Number(item.points[index]?.[1] ?? 0);
    });
    return row;
  });
  return (
    <Frame label="chart" title={block.title}>
      <ResponsiveContainer width="100%" height={200}>
        <LineChart data={data} margin={{ top: 6, right: 8, bottom: 0, left: -22 }}>
          <CartesianGrid stroke={LINE} strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="x" tick={{ fontSize: 9 }} tickLine={false} axisLine={false} minTickGap={40} />
          <YAxis tick={{ fontSize: 9 }} tickLine={false} axisLine={false} width={46} />
          <Tooltip contentStyle={tooltipStyle} labelStyle={{ color: "#99A1B3", marginBottom: 4 }} />
          {series.map((item, index) => (
            <Line
              key={`${item.label ?? "series"}-${index}`}
              type="monotone"
              dataKey={`s${index}`}
              name={item.label ?? `series ${index + 1}`}
              stroke={PALETTE[index % PALETTE.length]}
              strokeWidth={1.5}
              dot={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
      {series.length > 1 && (
        <div className="mt-1 flex flex-wrap gap-2">
          {series.map((item, index) => (
            <span key={`${item.label}-legend-${index}`} className="inline-flex items-center gap-1 font-mono text-[9px] text-muted">
              <span className="h-1.5 w-1.5 rounded-full" style={{ background: PALETTE[index % PALETTE.length] }} />
              {item.label ?? `series ${index + 1}`}
            </span>
          ))}
        </div>
      )}
    </Frame>
  );
}

function Gates({ block }: { block: ChatBlock }) {
  const items = block.items ?? [];
  if (!items.length) return null;
  return (
    <Frame label="gates" title={block.title}>
      <ul className="divide-y divide-line/40">
        {items.map((item, index) => {
          const status = (item.status ?? (item.value === "pass" ? "pass" : "fail")).toLowerCase();
          const tone =
            status === "pass" ? "text-signal" : status === "skip" ? "text-warn" : "text-danger";
          return (
            <li key={`${item.label}-${index}`} className="flex items-center gap-2 py-1.5">
              {status === "pass" ? (
                <CheckCircle2 size={11} className={tone} />
              ) : status === "skip" ? (
                <CircleDashed size={11} className={tone} />
              ) : (
                <XCircle size={11} className={tone} />
              )}
              <span className="font-mono text-[10.5px] text-text">{item.label.replaceAll("_", " ")}</span>
              {item.detail && <span className="truncate text-[10px] text-muted/70">{item.detail}</span>}
              <Badge tone={status === "pass" ? "signal" : status === "skip" ? "warn" : "danger"} className="ml-auto">
                {status}
              </Badge>
            </li>
          );
        })}
      </ul>
    </Frame>
  );
}

function Callout({ block }: { block: ChatBlock }) {
  const tone = (block.tone ?? "info").toLowerCase();
  const styles =
    tone === "danger"
      ? "border-danger/30 bg-danger/10 text-danger"
      : tone === "warn"
        ? "border-warn/30 bg-warn/10 text-warn"
        : "border-beam/30 bg-beam/10 text-beam";
  const Icon = tone === "danger" ? XCircle : tone === "warn" ? AlertTriangle : Info;
  return (
    <div className={`rounded-xl border px-3 py-2.5 ${styles}`}>
      <p className="flex items-center gap-1.5 font-mono text-[9px] uppercase tracking-[0.12em]">
        <Icon size={11} /> {block.title ?? "note"}
      </p>
      {block.body && <p className="mt-1 text-[11.5px] leading-relaxed text-muted">{block.body}</p>}
    </div>
  );
}

export function GenerativeBlock({ block }: { block: ChatBlock }) {
  switch (block.type) {
    case "metrics":
      return <Metrics block={block} />;
    case "table":
      return <Table block={block} />;
    case "checklist":
      return <Checklist block={block} />;
    case "chart":
      return <Chart block={block} />;
    case "gates":
      return <Gates block={block} />;
    case "callout":
      return <Callout block={block} />;
    default:
      return null;
  }
}

/** Renders the UI specs an agent authored. Unknown types are ignored, never dumped as JSON. */
export function GenerativeBlocks({ blocks }: { blocks?: ChatBlock[] }) {
  const renderable = (blocks ?? []).filter((block) =>
    ["metrics", "table", "checklist", "chart", "gates", "callout"].includes(block.type),
  );
  if (!renderable.length) return null;
  return (
    <div className="mt-2 space-y-2">
      {renderable.map((block, index) => (
        <GenerativeBlock key={`${block.type}-${index}`} block={block} />
      ))}
    </div>
  );
}
