"use client";

import { useEffect, useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const SIGNAL = "#5FE3C0";
const BEAM = "#7AA2FF";
const WARN = "#F2B34B";
const LINE = "#252A36";

function useMounted() {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  return mounted;
}

const tooltipStyle = {
  background: "#101218",
  border: "1px solid #252A36",
  borderRadius: 8,
  fontFamily: "var(--font-mono), monospace",
  fontSize: 11,
  color: "#E9ECF3",
  padding: "8px 10px",
};

export function EquityChart({
  data,
  height = 260,
  benchmark,
  color = SIGNAL,
}: {
  data: { date: string; equity: number; benchmark?: number }[];
  height?: number;
  benchmark?: boolean;
  color?: string;
}) {
  const mounted = useMounted();
  if (!mounted) return <div style={{ height }} className="animate-pulse rounded-lg bg-slate/40" />;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 10, right: 8, bottom: 0, left: -18 }}>
        <defs>
          <linearGradient id="equityFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.22} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke={LINE} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="date" tick={{ fontSize: 10 }} tickLine={false} axisLine={false} minTickGap={45} />
        <YAxis
          tick={{ fontSize: 10 }}
          tickLine={false}
          axisLine={false}
          domain={["auto", "auto"]}
          width={48}
        />
        <Tooltip
          contentStyle={tooltipStyle}
          labelStyle={{ color: "#99A1B3", marginBottom: 4 }}
          formatter={(value: number) => [`${Number(value).toFixed(2)}`, "index"]}
        />
        <Area type="monotone" dataKey="equity" stroke={color} strokeWidth={1.6} fill="url(#equityFill)" dot={false} />
        {benchmark && (
          <Line type="monotone" dataKey="benchmark" stroke={BEAM} strokeWidth={1.2} dot={false} strokeDasharray="4 4" />
        )}
      </AreaChart>
    </ResponsiveContainer>
  );
}

export function Sparkline({ values, color = SIGNAL }: { values: number[]; color?: string }) {
  const mounted = useMounted();
  const data = values.map((value, index) => ({ index, value }));
  if (!mounted) return <div className="h-10 w-24 animate-pulse rounded bg-slate/40" />;
  return (
    <ResponsiveContainer width="100%" height={40}>
      <LineChart data={data} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
        <Line type="monotone" dataKey="value" stroke={color} strokeWidth={1.4} dot={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function WeightBars({ weights }: { weights: Record<string, number> }) {
  const mounted = useMounted();
  const data = Object.entries(weights).map(([name, value]) => ({
    name: name.length > 24 ? `${name.slice(0, 22)}…` : name,
    value: Number((value * 100).toFixed(1)),
  }));
  if (!mounted) return <div className="h-48 animate-pulse rounded bg-slate/40" />;
  return (
    <ResponsiveContainer width="100%" height={Math.max(140, data.length * 44)}>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 24, bottom: 4, left: 8 }}>
        <CartesianGrid stroke={LINE} horizontal={false} strokeDasharray="3 3" />
        <XAxis type="number" tick={{ fontSize: 10 }} tickLine={false} axisLine={false} unit="%" />
        <YAxis type="category" dataKey="name" tick={{ fontSize: 10 }} tickLine={false} axisLine={false} width={150} />
        <Tooltip contentStyle={tooltipStyle} formatter={(v: number) => [`${v}%`, "weight"]} />
        <Bar dataKey="value" radius={[0, 3, 3, 0]} barSize={14}>
          {data.map((_, index) => (
            <Cell key={index} fill={[SIGNAL, BEAM, WARN, "#FF6B6B", "#99A1B3"][index % 5]} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

export function DrawdownChart({ data, height = 120 }: { data: { date: string; drawdown: number }[]; height?: number }) {
  const mounted = useMounted();
  if (!mounted) return <div style={{ height }} className="animate-pulse rounded bg-slate/40" />;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -20 }}>
        <defs>
          <linearGradient id="ddFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#FF6B6B" stopOpacity={0} />
            <stop offset="100%" stopColor="#FF6B6B" stopOpacity={0.25} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke={LINE} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="date" tick={{ fontSize: 10 }} tickLine={false} axisLine={false} minTickGap={60} />
        <YAxis tick={{ fontSize: 10 }} tickLine={false} axisLine={false} width={48} />
        <Tooltip contentStyle={tooltipStyle} formatter={(v: number) => [`${(Number(v) * 100).toFixed(1)}%`, "drawdown"]} />
        <ReferenceLine y={0} stroke={LINE} />
        <Area type="monotone" dataKey="drawdown" stroke="#FF6B6B" strokeWidth={1.2} fill="url(#ddFill)" dot={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}
