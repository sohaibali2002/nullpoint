"use client";

import { Lightbulb, Plus } from "lucide-react";
import { useState } from "react";

import { Badge, Button, DataStamp, Notice, PageHeader, Panel } from "@/components/ui";
import { api, getToken, useApi } from "@/lib/api";
import { when } from "@/lib/format";
import { mockIdeas } from "@/lib/mock";
import type { Idea } from "@/lib/types";

export default function IdeasPage() {
  const { data: ideas, demo, reload } = useApi<Idea[]>("/ideas", mockIdeas);
  const [title, setTitle] = useState("");
  const [hypothesis, setHypothesis] = useState("");
  const [family, setFamily] = useState("cross_sectional");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    setNotice(null);
    if (!getToken()) {
      setError("Demo mode: signing in lets the librarian embed and deduplicate your idea.");
      return;
    }
    setBusy(true);
    try {
      const result = await api<Idea & { duplicate_warning: boolean; similarity: number }>("/ideas", {
        method: "POST",
        body: JSON.stringify({ title, hypothesis, family, tags: [family] }),
      });
      setNotice(
        result.duplicate_warning
          ? `Stored, but flagged as a near-duplicate at ${(result.similarity * 100).toFixed(0)}% similarity.`
          : "Stored. The ideator will see it before generating new hypotheses.",
      );
      setTitle("");
      setHypothesis("");
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not record idea");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <PageHeader
        eyebrow="Hypothesis queue"
        title="Idea lab"
        description="Human and agent hypotheses enter the same queue. The librarian embeds every idea and flags near-duplicates before a single backtest is spent."
        actions={<DataStamp demo={demo} />}
      />

      <div className="grid gap-3 xl:grid-cols-[1fr_1.7fr]">
        <Panel title="Record an idea" eyebrow="falsifiable or it isn't an idea">
          <form onSubmit={submit} className="space-y-4 px-5 py-5">
            <label className="block">
              <span className="label">Title</span>
              <input
                className="input mt-1.5"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="Trend-filtered sector momentum"
                required
                minLength={4}
              />
            </label>
            <label className="block">
              <span className="label">Hypothesis</span>
              <textarea
                className="input mt-1.5 min-h-[110px] resize-y"
                value={hypothesis}
                onChange={(e) => setHypothesis(e.target.value)}
                placeholder="State the mechanism and what would prove it false."
                required
                minLength={10}
              />
            </label>
            <label className="block">
              <span className="label">Family</span>
              <select className="input mt-1.5" value={family} onChange={(e) => setFamily(e.target.value)}>
                {["cross_sectional", "trend", "mean_reversion", "defensive", "regime", "seasonality", "event"].map(
                  (option) => (
                    <option key={option} value={option} className="bg-graphite">
                      {option.replaceAll("_", " ")}
                    </option>
                  ),
                )}
              </select>
            </label>
            {error && <Notice tone="danger">{error}</Notice>}
            {notice && <Notice tone="signal">{notice}</Notice>}
            <Button type="submit" variant="primary" loading={busy} className="w-full">
              <Plus size={14} /> Add to queue
            </Button>
          </form>
        </Panel>

        <Panel title="Queue" eyebrow={`${ideas.length} hypotheses`}>
          <ul className="divide-y divide-line/60">
            {ideas.map((idea) => (
              <li key={idea.id} className="px-4 py-4">
                <div className="flex items-start gap-3">
                  <span className="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-md border border-line bg-ink/50">
                    <Lightbulb size={12} className="text-warn" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="text-[13px] text-text">{idea.title}</p>
                      <Badge tone={idea.status === "duplicate" ? "warn" : idea.status === "rejected" ? "danger" : "neutral"}>
                        {idea.status}
                      </Badge>
                      <Badge>{idea.origin}</Badge>
                    </div>
                    <p className="mt-1.5 text-[11px] leading-relaxed text-muted">{idea.hypothesis}</p>
                    <div className="mt-2 flex flex-wrap items-center gap-2 font-mono text-[9px] text-muted/70">
                      <span>{idea.family.replaceAll("_", " ")}</span>
                      <span>·</span>
                      <span>{when(idea.created_at)}</span>
                      {idea.dedupe_score && (
                        <>
                          <span>·</span>
                          <span className="text-warn">{(idea.dedupe_score * 100).toFixed(0)}% similar</span>
                        </>
                      )}
                    </div>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </Panel>
      </div>
    </div>
  );
}
