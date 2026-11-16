"use client";

import { BookOpen, Brain, FileUp, Search, Upload } from "lucide-react";
import { FormEvent, useRef, useState } from "react";

import { Badge, Button, DataStamp, Notice, PageHeader, Panel } from "@/components/ui";
import { api, getToken, uploadKnowledge, useApi } from "@/lib/api";
import type { KnowledgeHit, KnowledgeStats, MemoryItem } from "@/lib/types";

const EMPTY: KnowledgeStats = {
  documents: 0,
  chunks: 0,
  library_chunks: 0,
  embedding_models: [],
  voyage_configured: false,
};

export default function KnowledgePage() {
  const { data: stats, demo, reload } = useApi<KnowledgeStats>("/knowledge", EMPTY);
  const { data: memory } = useApi<{ items: MemoryItem[] }>("/memory", { items: [] });
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [tags, setTags] = useState("book,research");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "signal" | "danger" | "warn"; text: string } | null>(null);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<KnowledgeHit[]>([]);
  const [searching, setSearching] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const upload = async (event: FormEvent) => {
    event.preventDefault();
    if (!file) {
      setMessage({ tone: "warn", text: "Choose a PDF, EPUB, TXT or Markdown file first." });
      return;
    }
    if (!getToken()) {
      setMessage({ tone: "warn", text: "Sign in to add documents to your desk." });
      return;
    }
    setBusy(true);
    setMessage(null);
    const form = new FormData();
    form.set("file", file);
    form.set("title", title || file.name.replace(/\.[^.]+$/, ""));
    form.set("tags", tags);
    try {
      const result = await uploadKnowledge(form);
      setMessage({
        tone: "signal",
        text: `Indexed ${result.chunks ?? 0} chunks with ${result.model ?? "embeddings"}${
          result.fallback ? " (local fallback)" : ""
        }.`,
      });
      setFile(null);
      setTitle("");
      if (inputRef.current) inputRef.current.value = "";
      await reload();
    } catch (err) {
      setMessage({ tone: "danger", text: err instanceof Error ? err.message : "Upload failed" });
    } finally {
      setBusy(false);
    }
  };

  const search = async (event?: FormEvent) => {
    event?.preventDefault();
    if (query.trim().length < 2) return;
    if (!getToken()) {
      setMessage({ tone: "warn", text: "Sign in to search the desk library." });
      return;
    }
    setSearching(true);
    setMessage(null);
    try {
      const result = await api<{ hits: KnowledgeHit[] }>(
        `/knowledge/search?q=${encodeURIComponent(query.trim())}&limit=8`,
      );
      setHits(result.hits);
    } catch (err) {
      setMessage({ tone: "danger", text: err instanceof Error ? err.message : "Search failed" });
    } finally {
      setSearching(false);
    }
  };

  return (
    <div>
      <PageHeader
        eyebrow="Desk library"
        title="Knowledge"
        description="Your private documents plus the shared investment library, embedded and retrieved with Voyage. Upload books you have the right to use; originals are parsed in memory and are not retained."
        actions={<DataStamp demo={demo} />}
      />

      <div className="grid gap-3 xl:grid-cols-[1.15fr_1fr]">
        <Panel title="Add a document" eyebrow="PDF · EPUB · TXT · Markdown">
          <form onSubmit={upload} className="space-y-4 px-4 py-4">
            <div className="rounded-xl border border-dashed border-line bg-ink/30 px-4 py-5 text-center">
              <BookOpen size={20} className="mx-auto text-signal" />
              <p className="mt-2 text-[12px] font-medium text-text">
                Drop in a book, paper or research note
              </p>
              <p className="mt-1 text-[10px] leading-relaxed text-muted">
                Text is extracted and chunked. For PDFs and EPUBs the uploaded file itself is never kept.
              </p>
              <input
                ref={inputRef}
                type="file"
                accept=".pdf,.epub,.txt,.md,.markdown"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                className="mx-auto mt-3 block max-w-full text-[10px] text-muted file:mr-3 file:rounded-md file:border file:border-line file:bg-slate file:px-2.5 file:py-1.5 file:text-[10px] file:text-text"
              />
              {file && <p className="mt-2 truncate font-mono text-[10px] text-signal">{file.name}</p>}
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block">
                <span className="label">Title</span>
                <input
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  placeholder="e.g. Expected Returns — notes"
                  className="input mt-1.5 text-[12px]"
                />
              </label>
              <label className="block">
                <span className="label">Tags</span>
                <input
                  value={tags}
                  onChange={(event) => setTags(event.target.value)}
                  placeholder="value, momentum, risk"
                  className="input mt-1.5 text-[12px]"
                />
              </label>
            </div>

            {message && <Notice tone={message.tone}>{message.text}</Notice>}
            <div className="flex items-center justify-between gap-3">
              <p className="max-w-[65%] text-[10px] leading-relaxed text-muted/65">
                Up to 12 MB and 500k extracted characters per file. Files above ~45k characters
                use fast local embeddings so the upload still finishes; add a Voyage payment method
                to embed whole books with Voyage. Only upload material you have the right to use.
              </p>
              <Button type="submit" variant="primary" loading={busy} disabled={!file}>
                <Upload size={13} /> Index document
              </Button>
            </div>
          </form>
        </Panel>

        <div className="space-y-3">
          <Panel title="Library status" eyebrow="embedding coverage">
            <div className="grid grid-cols-2 divide-x divide-y divide-line/60">
              {[
                ["Documents", stats.documents],
                ["Chunks", stats.chunks],
                ["Shared-library chunks", stats.library_chunks],
                ["Embedding", stats.embedding_models.join(", ") || "local fallback"],
              ].map(([label, value]) => (
                <div key={String(label)} className="px-4 py-4">
                  <p className="label">{label}</p>
                  <p className="mono mt-1.5 text-lg text-text">{String(value)}</p>
                </div>
              ))}
            </div>
            <div className="flex items-center justify-between border-t border-line/60 px-4 py-3">
              <span className="text-[11px] text-muted">Voyage embeddings</span>
              <Badge tone={stats.voyage_configured ? "signal" : "warn"}>
                {stats.voyage_configured ? "active" : "local fallback"}
              </Badge>
            </div>
          </Panel>

          <Panel title="Search the library" eyebrow="semantic retrieval">
            <form onSubmit={search} className="flex gap-2 border-b border-line/60 px-4 py-3">
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="What does the desk know about drawdown control?"
                className="input flex-1 text-[12px]"
              />
              <Button type="submit" size="sm" variant="secondary" loading={searching}>
                <Search size={13} />
              </Button>
            </form>
            <div className="max-h-[330px] overflow-y-auto">
              {hits.length === 0 ? (
                <p className="px-4 py-5 text-[11px] text-muted">
                  Search returns the closest passages across your documents and the shared library.
                </p>
              ) : (
                <ul className="divide-y divide-line/60">
                  {hits.map((hit) => (
                    <li key={hit.id} className="px-4 py-3">
                      <div className="flex items-start justify-between gap-3">
                        <p className="text-[11px] font-medium text-text">{hit.title}</p>
                        <span className="mono shrink-0 text-[9px] text-signal">{hit.score.toFixed(3)}</span>
                      </div>
                      <p className="mt-1 line-clamp-3 text-[10px] leading-relaxed text-muted">{hit.snippet}</p>
                      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                        {hit.source && <Badge tone="neutral">{hit.source}</Badge>}
                        {hit.tags.slice(0, 4).map((tag) => (
                          <span key={tag} className="text-[9px] text-muted/60">#{tag}</span>
                        ))}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </Panel>

          <Panel title="Desk memory" eyebrow="recalled automatically">
            {memory.items.length === 0 ? (
              <div className="px-4 py-5">
                <Brain size={18} className="text-signal" />
                <p className="mt-2 text-[11px] text-muted">
                  Room conclusions become durable lessons. They are recalled by every later chat and
                  room on this desk.
                </p>
              </div>
            ) : (
              <ul className="max-h-[280px] divide-y divide-line/60 overflow-y-auto">
                {memory.items.slice(0, 6).map((item) => (
                  <li key={item.id} className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <Badge tone="beam">{item.kind.replace("_", " ")}</Badge>
                      <span className="ml-auto text-[9px] text-muted/60">
                        {item.created_at ? new Date(item.created_at).toLocaleDateString() : ""}
                      </span>
                    </div>
                    <p className="mt-1.5 text-[11px] font-medium text-text">{item.title}</p>
                    <p className="mt-1 line-clamp-3 text-[10px] leading-relaxed text-muted">{item.body}</p>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>
      </div>

      <div className="mt-3 rounded-xl border border-line/70 bg-slate/20 px-4 py-3">
        <p className="flex items-center gap-2 text-[11px] text-muted">
          <FileUp size={12} className="text-signal" />
          The shared library is read-only for every workspace. Uploads stay private to your desk.
        </p>
      </div>
    </div>
  );
}
