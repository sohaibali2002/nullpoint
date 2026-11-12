"use client";

import { ArrowRight } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Logo } from "@/components/logo";
import { Button, Notice } from "@/components/ui";
import { api, setToken } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("quant@example.com");
  const [password, setPassword] = useState("correct-horse-battery");
  const [orgName, setOrgName] = useState("Skeptical Capital");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const payload =
        mode === "register"
          ? await api<{ access_token: string }>("/auth/register", {
              method: "POST",
              body: JSON.stringify({ email, password, org_name: orgName }),
            })
          : await api<{ access_token: string }>("/auth/login", {
              method: "POST",
              body: JSON.stringify({ email, password }),
            });
      setToken(payload.access_token);
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not sign in");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="grid min-h-screen md:grid-cols-2">
      <div className="relative hidden overflow-hidden border-r border-line/70 md:block">
        <div className="grid-bg absolute inset-0 opacity-30" />
        <div className="absolute -left-24 top-1/3 h-96 w-96 rounded-full bg-signal/10 blur-[100px]" />
        <div className="relative flex h-full flex-col justify-between p-10">
          <Logo />
          <div>
            <p className="label">The lab door</p>
            <h1 className="mt-4 max-w-sm text-4xl font-semibold leading-[1.05] tracking-tight">
              Ten agents. One rule.
            </h1>
            <p className="mt-4 max-w-sm text-sm leading-relaxed text-muted">
              Nothing ships until it beats the null. Sign in to run real sprints against your own
              universe, or continue with the deterministic demo.
            </p>
            <div className="mt-8 space-y-2 font-mono text-[11px] text-muted">
              <p><span className="text-signal">✓</span> sealed holdout, hashed before agents start</p>
              <p><span className="text-signal">✓</span> global trial counter, org-wide</p>
              <p><span className="text-signal">✓</span> explicit gates, rejections preserved</p>
              <p><span className="text-danger">×</span> no live order routing, by construction</p>
            </div>
          </div>
          <p className="text-[10px] text-muted/70">Research only. Not investment advice.</p>
        </div>
      </div>

      <div className="flex items-center justify-center px-5 py-14">
        <div className="w-full max-w-sm">
          <div className="mb-8 md:hidden">
            <Logo />
          </div>
          <p className="label">{mode === "login" ? "Welcome back" : "Create a workspace"}</p>
          <h2 className="mt-2 text-2xl font-semibold tracking-tight">
            {mode === "login" ? "Sign in to Nullpoint" : "Start a research desk"}
          </h2>

          <div className="mt-6 flex rounded-lg border border-line bg-ink/50 p-1">
            {(["login", "register"] as const).map((option) => (
              <button
                key={option}
                onClick={() => {
                  setMode(option);
                  setError(null);
                }}
                className={`flex-1 rounded-md px-3 py-1.5 text-xs capitalize transition ${
                  mode === option ? "bg-slate text-text" : "text-muted hover:text-text"
                }`}
              >
                {option === "login" ? "Sign in" : "Register"}
              </button>
            ))}
          </div>

          <form onSubmit={submit} className="mt-5 space-y-4">
            {mode === "register" && (
              <label className="block">
                <span className="label">Organisation</span>
                <input
                  className="input mt-1.5"
                  value={orgName}
                  onChange={(e) => setOrgName(e.target.value)}
                  required
                  minLength={2}
                />
              </label>
            )}
            <label className="block">
              <span className="label">Email</span>
              <input
                type="email"
                className="input mt-1.5"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </label>
            <label className="block">
              <span className="label">Password</span>
              <input
                type="password"
                className="input mt-1.5"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                minLength={8}
              />
            </label>
            {error && <Notice tone="danger">{error}</Notice>}
            <Button type="submit" variant="primary" loading={loading} className="w-full">
              {mode === "login" ? "Sign in" : "Create workspace"}
              <ArrowRight size={15} />
            </Button>
          </form>

          <div className="my-6 flex items-center gap-3">
            <div className="h-px flex-1 bg-line" />
            <span className="font-mono text-[10px] uppercase tracking-wider text-muted">or</span>
            <div className="h-px flex-1 bg-line" />
          </div>

          <Link
            href="/dashboard"
            className="focus-ring flex w-full items-center justify-center rounded-lg border border-line bg-slate/60 px-3.5 py-2.5 text-sm text-text hover:border-muted/40"
          >
            Continue with demo data
          </Link>
          <p className="mt-4 text-center text-[11px] leading-relaxed text-muted">
            Demo mode is deterministic and labelled everywhere. No account, no market data.
          </p>
        </div>
      </div>
    </div>
  );
}
