import clsx from "clsx";
import type { ButtonHTMLAttributes, ReactNode } from "react";

export function cn(...values: Array<string | false | null | undefined>) {
  return clsx(values);
}

export function Panel({
  children,
  className,
  title,
  eyebrow,
  action,
}: {
  children: ReactNode;
  className?: string;
  title?: string;
  eyebrow?: string;
  action?: ReactNode;
}) {
  return (
    <section className={cn("card", className)}>
      {(title || action) && (
        <header className="flex items-start justify-between gap-4 border-b border-line/70 px-5 py-4">
          <div>
            {eyebrow && <p className="label mb-1">{eyebrow}</p>}
            {title && <h2 className="text-[15px] font-medium tracking-tight text-text">{title}</h2>}
          </div>
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md";
  loading?: boolean;
};

export function Button({
  variant = "secondary",
  size = "md",
  loading,
  className,
  children,
  disabled,
  ...props
}: ButtonProps) {
  return (
    <button
      {...props}
      disabled={disabled || loading}
      className={cn(
        "focus-ring inline-flex items-center justify-center gap-2 rounded-lg font-medium transition disabled:cursor-not-allowed disabled:opacity-50",
        size === "sm" ? "px-2.5 py-1.5 text-xs" : "px-3.5 py-2 text-sm",
        variant === "primary" && "bg-signal text-ink hover:bg-signal/90",
        variant === "secondary" && "border border-line bg-slate/70 text-text hover:border-muted/40 hover:bg-slate",
        variant === "ghost" && "text-muted hover:bg-slate/70 hover:text-text",
        variant === "danger" && "border border-danger/40 bg-danger/10 text-danger hover:bg-danger/20",
        className,
      )}
    >
      {loading && <span className="h-3 w-3 animate-spin rounded-full border border-current border-t-transparent" />}
      {children}
    </button>
  );
}

const TONES: Record<string, string> = {
  neutral: "border-line bg-slate/80 text-muted",
  signal: "border-signal/30 bg-signal/10 text-signal",
  beam: "border-beam/30 bg-beam/10 text-beam",
  warn: "border-warn/30 bg-warn/10 text-warn",
  danger: "border-danger/30 bg-danger/10 text-danger",
};

export function Badge({
  children,
  tone = "neutral",
  className,
}: {
  children: ReactNode;
  tone?: keyof typeof TONES | string;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-[0.1em]",
        TONES[tone] ?? TONES.neutral,
        className,
      )}
    >
      {children}
    </span>
  );
}

const STATUS_TONE: Record<string, string> = {
  idea: "neutral",
  backtested: "beam",
  validated: "signal",
  candidate: "signal",
  paper: "warn",
  live_ready: "danger",
  rejected: "danger",
  retired: "neutral",
  completed: "signal",
  running: "beam",
  queued: "warn",
  failed: "danger",
  killed: "danger",
  stopped: "neutral",
};

export function StatusBadge({ status }: { status: string }) {
  return <Badge tone={STATUS_TONE[status] ?? "neutral"}>{status.replaceAll("_", " ")}</Badge>;
}

export function Stat({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: "signal" | "warn" | "danger";
}) {
  return (
    <div className="px-5 py-4">
      <p className="label">{label}</p>
      <p
        className={cn(
          "mono mt-2 text-2xl font-medium tracking-tight",
          tone === "signal" && "text-signal",
          tone === "warn" && "text-warn",
          tone === "danger" && "text-danger",
          !tone && "text-text",
        )}
      >
        {value}
      </p>
      {hint && <p className="mt-1 text-xs leading-relaxed text-muted">{hint}</p>}
    </div>
  );
}

export function Notice({
  children,
  tone = "warn",
  className,
}: {
  children: ReactNode;
  tone?: "warn" | "danger" | "signal" | "beam";
  className?: string;
}) {
  return (
    <div
      className={cn(
        "rounded-lg border px-3.5 py-3 text-xs leading-relaxed",
        TONES[tone],
        className,
      )}
    >
      {children}
    </div>
  );
}

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow: string;
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-col justify-between gap-4 md:flex-row md:items-end">
      <div>
        <p className="label mb-2">{eyebrow}</p>
        <h1 className="text-2xl font-semibold tracking-tight text-text md:text-[26px]">{title}</h1>
        {description && <p className="mt-2 max-w-2xl text-sm leading-relaxed text-muted">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Progress({ value, tone = "signal" }: { value: number; tone?: "signal" | "warn" }) {
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-line">
      <div
        className={cn("h-full rounded-full", tone === "signal" ? "bg-signal" : "bg-warn")}
        style={{ width: `${Math.min(100, Math.max(0, value * 100))}%` }}
      />
    </div>
  );
}

export function EmptyState({ title, body }: { title: string; body: string }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-16 text-center">
      <div className="mb-4 grid h-10 w-10 place-items-center rounded-full border border-line bg-slate/60">
        <span className="h-2 w-2 rounded-full bg-muted" />
      </div>
      <p className="text-sm font-medium text-text">{title}</p>
      <p className="mt-1 max-w-sm text-xs leading-relaxed text-muted">{body}</p>
    </div>
  );
}

export function DataStamp({ demo, source }: { demo: boolean; source?: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.12em] text-muted">
      <span className={cn("h-1.5 w-1.5 rounded-full", demo ? "bg-warn" : "bg-signal")} />
      {demo ? "demo data" : source ?? "live api"}
    </span>
  );
}
