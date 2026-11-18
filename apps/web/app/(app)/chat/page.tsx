"use client";

import {
  Bot,
  Download,
  FileText,
  Loader2,
  Paperclip,
  Pencil,
  Plus,
  Search,
  Send,
  Trash2,
  UserRound,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { GenerativeBlocks } from "@/components/generative";
import { Badge, Button, Notice, Panel, PageHeader } from "@/components/ui";
import { api, getToken, streamChat, uploadChatFile, useApi } from "@/lib/api";
import { when } from "@/lib/format";
import type { ChatSource, ChatThread, ChatThreadDetail, ChatTurn } from "@/lib/types";

const MAX_THREADS = 50;

const EXAMPLES = [
  "Create me a robust Nasdaq trading strategy",
  "Backtest a low-volatility rotation over the last 10 years",
  "What would make that strategy fail?",
];

function Avatar({ assistant = false }: { assistant?: boolean }) {
  return (
    <div
      className={`grid h-8 w-8 shrink-0 place-items-center rounded-lg border ${
        assistant ? "border-signal/30 bg-signal/10 text-signal" : "border-line bg-slate text-muted"
      }`}
    >
      {assistant ? <Bot size={15} /> : <UserRound size={14} />}
    </div>
  );
}

function Sources({ sources }: { sources: ChatSource[] }) {
  if (!sources.length) return null;
  return (
    <div className="mt-2 rounded-lg border border-line/70 bg-slate/30 px-2.5 py-2">
      <p className="mb-1.5 font-mono text-[9px] uppercase tracking-[0.12em] text-muted/70">sources</p>
      <ul className="space-y-1">
        {sources.map((source) => (
          <li key={source.id} className="text-[10px] leading-relaxed text-muted">
            <span className="mr-1 font-mono text-signal/80">[{source.id}]</span>
            {source.url.startsWith("http") ? (
              <a href={source.url} target="_blank" rel="noreferrer" className="hover:text-text">
                {source.title || source.url}
              </a>
            ) : source.url ? (
              <Link href={source.url} className="hover:text-text">
                {source.title}
              </Link>
            ) : (
              <span>{source.title}</span>
            )}
            <span className="ml-1 text-muted/50">· {source.provider}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function ChatPage() {
  const { data: threads, loading: threadsLoading, reload: reloadThreads } = useApi<ChatThread[]>("/chats", []);
  const [signedIn, setSignedIn] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ChatThreadDetail | null>(null);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [creating, setCreating] = useState(false);
  const [useWeb, setUseWeb] = useState(true);
  const [useDeep, setUseDeep] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [threadQuery, setThreadQuery] = useState("");
  const [live, setLive] = useState<{ agent: string; agent_name: string; text: string } | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const visibleThreads = threads.filter((thread) => {
    const query = threadQuery.trim().toLowerCase();
    if (!query) return true;
    return `${thread.title} ${thread.last_message ?? ""}`.toLowerCase().includes(query);
  });
  const activeThread = detail?.thread ?? threads.find((thread) => thread.id === selectedId) ?? null;

  useEffect(() => {
    setSignedIn(Boolean(getToken()));
  }, []);

  useEffect(() => {
    if (!threads.length) {
      setSelectedId(null);
      return;
    }
    if (!selectedId || !threads.some((thread) => thread.id === selectedId)) {
      setSelectedId(threads[0].id);
    }
  }, [threads, selectedId]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    api<ChatThreadDetail>(`/chats/${selectedId}`)
      .then((payload) => {
        if (!cancelled) setDetail(payload);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Could not load conversation");
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [detail?.messages.length, sending, live?.text]);

  const refreshDetail = useCallback(async (id: string) => {
    try {
      setDetail(await api<ChatThreadDetail>(`/chats/${id}`));
    } catch {
      // Keep the original error; a second one adds nothing.
    }
  }, []);

  const newChat = () => {
    setSelectedId(null);
    setDetail(null);
    setDraft("");
    setError(null);
    setLive(null);
  };

  const createThread = async (): Promise<ChatThreadDetail | null> => {
    setCreating(true);
    try {
      const created = await api<ChatThreadDetail>("/chats", {
        method: "POST",
        body: JSON.stringify({ auto: true }),
      });
      setDetail(created);
      setSelectedId(created.thread.id);
      await reloadThreads();
      return created;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start a conversation");
      return null;
    } finally {
      setCreating(false);
    }
  };

  const removeThread = async (id: string) => {
    if (!window.confirm("Delete this conversation and all of its messages?")) return;
    try {
      await api(`/chats/${id}`, { method: "DELETE" });
      if (selectedId === id) {
        setSelectedId(null);
        setDetail(null);
      }
      await reloadThreads();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete conversation");
    }
  };

  const renameThread = async () => {
    if (!activeThread || !getToken()) return;
    const title = window.prompt("Rename this conversation", activeThread.title)?.trim();
    if (!title || title === activeThread.title) return;
    try {
      const updated = await api<ChatThread>(`/chats/${activeThread.id}`, {
        method: "PATCH",
        body: JSON.stringify({ title }),
      });
      setDetail((current) => (current ? { ...current, thread: { ...current.thread, ...updated } } : current));
      await reloadThreads();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not rename the conversation");
    }
  };

  const exportThread = () => {
    if (!detail) return;
    const lines = [
      `# ${detail.thread.title}`,
      "",
      `Exported from Nullpoint · ${new Date().toISOString()}`,
      "",
      ...detail.messages.map((message) => {
        const who = message.role === "user" ? "Operator" : message.agent_name;
        return `## ${who}${message.kind !== "message" ? ` (${message.kind})` : ""}\n\n${message.content}`;
      }),
      "",
      "Research discussion only; not investment advice.",
    ];
    const blob = new Blob([lines.join("\n")], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${detail.thread.title.replace(/[^a-z0-9]+/gi, "-").replace(/^-|-$/g, "") || "nullpoint-chat"}.md`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  const uploadFile = async (file: File) => {
    if (!getToken()) return;
    setUploading(true);
    setError(null);
    try {
      let threadId = detail?.thread.id;
      let current = detail;
      if (!threadId) {
        const created = await createThread();
        current = created;
        threadId = created?.thread.id;
      }
      if (!threadId) return;
      const result = await uploadChatFile(threadId, file);
      await refreshDetail(threadId);
      await reloadThreads();
      setDetail((prev) =>
        prev
          ? { ...prev, messages: prev.messages.some((m) => m.id === result.message.id) ? prev.messages : [...prev.messages, result.message] }
          : prev,
      );
      if (!current) setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not upload that file");
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const send = async (override?: string) => {
    const content = (override ?? draft).trim();
    if (!content || sending || !getToken()) return;
    setError(null);
    setSending(true);
    setLive(null);
    setDraft("");
    try {
      let threadId = detail?.thread.id;
      if (!threadId) {
        const created = await createThread();
        threadId = created?.thread.id;
      }
      if (!threadId) return;
      const optimistic: ChatTurn = {
        id: `pending-${Date.now()}`,
        thread_id: threadId,
        role: "user",
        agent: "orchestrator",
        agent_name: "You",
        kind: "message",
        ui: [],
        sources: [],
        content,
        model: null,
        tokens_in: 0,
        tokens_out: 0,
        cost_usd: 0,
        created_at: new Date().toISOString(),
      };
      setDetail((current) =>
        current ? { ...current, messages: [...current.messages, optimistic] } : current,
      );
      await streamChat(
        `/chats/${threadId}/messages/stream`,
        { content, use_web: useWeb, deep: useDeep },
        (event, data) => {
          if (event === "delta" && data?.text) {
            setLive((current) => {
              if (!current || current.agent !== data.agent) {
                return { agent: data.agent, agent_name: data.agent_name ?? data.agent, text: String(data.text) };
              }
              return { ...current, text: current.text + String(data.text) };
            });
          } else if (event === "message" && data?.id) {
            setDetail((current) => {
              if (!current || current.messages.some((message) => message.id === data.id)) return current;
              return { ...current, messages: [...current.messages, data as ChatTurn] };
            });
            setLive((current) => (current && current.agent === data.agent ? null : current));
          } else if (event === "error") {
            setError(data?.detail ?? "The desk could not reply");
          }
        },
      );
      await refreshDetail(threadId);
      await reloadThreads();
    } catch (err) {
      setError(err instanceof Error ? err.message : "The desk could not reply");
      if (detail?.thread.id) await refreshDetail(detail.thread.id);
    } finally {
      setSending(false);
      setLive(null);
    }
  };

  return (
    <div>
      <PageHeader
        eyebrow="Chat"
        title="Ask for a strategy. Get a measured one."
        description="A normal chat with the research desk. Ask for a strategy and the engine runs a real backtest before anyone comments on it; every number you see was measured on market data."
        actions={
          <Button variant="primary" onClick={() => void (getToken() ? createThread() : undefined)} loading={creating}>
            <Plus size={14} /> New chat
          </Button>
        }
      />

      {!signedIn ? (
        <Panel title="Sign in to chat" eyebrow="persistent conversations">
          <div className="px-5 py-8">
            <Notice tone="warn" className="mb-4">
              Conversations, memory and backtests are stored against your workspace, so chat needs an
              account. Nothing is shown while you are signed out.
            </Notice>
            <Link
              href="/login"
              className="focus-ring inline-flex rounded-lg bg-signal px-3.5 py-2 text-sm font-medium text-ink hover:bg-signal/90"
            >
              Sign in or create a workspace
            </Link>
          </div>
        </Panel>
      ) : (
        <div className="grid gap-3 lg:grid-cols-[280px_1fr]">
          <Panel className="flex min-h-0 flex-col lg:h-[calc(100vh-220px)]">
            <div className="flex items-center justify-between border-b border-line/70 px-4 py-3">
              <div>
                <p className="label">Conversations</p>
                <p className="mt-1 text-[12px] text-muted">
                  {threads.length} of {MAX_THREADS} most recent
                </p>
              </div>
              <Button variant="secondary" size="sm" onClick={() => void createThread()} loading={creating}>
                <Plus size={12} /> New
              </Button>
            </div>
            <div className="border-b border-line/60 px-3 py-2">
              <div className="flex items-center gap-1.5 rounded-md border border-line/70 bg-ink/40 px-2">
                <Search size={11} className="text-muted/60" />
                <input
                  value={threadQuery}
                  onChange={(event) => setThreadQuery(event.target.value)}
                  placeholder="Search conversations"
                  className="w-full bg-transparent py-1.5 text-[11px] text-text outline-none placeholder:text-muted/45"
                />
              </div>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto">
              {threadsLoading && (
                <div className="flex items-center gap-2 px-4 py-5 text-xs text-muted">
                  <Loader2 size={13} className="animate-spin" /> Loading conversations
                </div>
              )}
              <ul className="divide-y divide-line/60">
                {visibleThreads.map((thread) => {
                  const selected = thread.id === selectedId;
                  return (
                    <li key={thread.id} className="group relative">
                      <button
                        onClick={() => {
                          setSelectedId(thread.id);
                          setError(null);
                          setLive(null);
                        }}
                        className={`focus-ring w-full px-4 py-3 pr-10 text-left transition ${
                          selected ? "bg-slate/80" : "hover:bg-slate/40"
                        }`}
                      >
                        <div className="flex items-center gap-2">
                          <span className={`truncate text-[11.5px] font-medium ${selected ? "text-signal" : "text-text"}`}>
                            {thread.title}
                          </span>
                          <span className="ml-auto shrink-0 font-mono text-[9px] text-muted/60">
                            {when(thread.updated_at)}
                          </span>
                        </div>
                        <p className="mt-1 line-clamp-2 text-[10px] leading-relaxed text-muted/60">
                          {thread.last_message || "Empty conversation"}
                        </p>
                      </button>
                      <button
                        onClick={() => void removeThread(thread.id)}
                        className="focus-ring absolute right-2 top-2 rounded-md p-1 text-muted/50 opacity-0 transition hover:bg-danger/10 hover:text-danger group-hover:opacity-100"
                        title="Delete conversation"
                      >
                        <Trash2 size={12} />
                      </button>
                    </li>
                  );
                })}
              </ul>
              {!threadsLoading && visibleThreads.length === 0 && (
                <div className="px-4 py-8 text-center text-[11px] leading-relaxed text-muted">
                  {threads.length === 0
                    ? "No conversations yet. Ask something below to start one."
                    : "No conversations match that search."}
                </div>
              )}
            </div>
          </Panel>

          <Panel className="flex min-h-[68vh] flex-col lg:h-[calc(100vh-220px)]">
            <div className="flex items-center gap-3 border-b border-line/70 px-4 py-3">
              <div className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-signal/30 bg-signal/10 text-signal">
                <Bot size={16} />
              </div>
              <div className="min-w-0">
                <p className="truncate text-[13px] font-medium text-text">
                  {activeThread?.title ?? "New chat"}
                </p>
                <p className="truncate text-[10px] text-muted">
                  {activeThread ? `${activeThread.message_count} messages · research desk` : "Backtests run in the chat"}
                </p>
              </div>
              {activeThread && (
                <div className="ml-auto flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => void renameThread()}
                    className="focus-ring rounded-md border border-line/70 p-1.5 text-muted transition hover:text-text"
                    title="Rename conversation"
                  >
                    <Pencil size={12} />
                  </button>
                  <button
                    type="button"
                    onClick={exportThread}
                    className="focus-ring rounded-md border border-line/70 p-1.5 text-muted transition hover:text-text"
                    title="Export as Markdown"
                  >
                    <Download size={12} />
                  </button>
                </div>
              )}
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-5">
              {(!detail || detail.messages.length === 0) && (
                <div className="mx-auto flex h-full max-w-xl flex-col items-center justify-center py-10 text-center">
                  <div className="grid h-11 w-11 place-items-center rounded-full border border-signal/30 bg-signal/10 text-signal">
                    <Bot size={18} />
                  </div>
                  <p className="mt-4 text-sm font-medium text-text">What should we test?</p>
                  <p className="mt-1.5 max-w-md text-xs leading-relaxed text-muted">
                    Ask for a strategy and you get a real backtest: metrics, an equity curve, every gate and
                    a stored record. Ask a question and you get an answer.
                  </p>
                  <div className="mt-5 flex flex-col gap-1.5">
                    {EXAMPLES.map((prompt) => (
                      <button
                        key={prompt}
                        onClick={() => void send(prompt)}
                        disabled={sending}
                        className="focus-ring rounded-full border border-line bg-slate/50 px-3 py-1.5 text-[11px] text-muted transition hover:border-signal/30 hover:text-text disabled:opacity-60"
                      >
                        {prompt}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              <div className="mx-auto max-w-3xl space-y-4">
                {detail?.messages.map((message) => {
                  const mine = message.role === "user";
                  const isBacktest = message.kind === "backtest";
                  return (
                    <div key={message.id} className={`flex gap-2.5 ${mine ? "justify-end" : ""}`}>
                      {!mine && <Avatar assistant />}
                      <div className={`max-w-[88%] ${mine ? "order-first" : ""}`}>
                        {!mine && (
                          <div className="mb-1 flex flex-wrap items-center gap-1.5">
                            <p className="font-mono text-[9px] uppercase tracking-[0.12em] text-signal/80">
                              {message.agent_name}
                            </p>
                            {isBacktest && <Badge tone="signal">backtest</Badge>}
                            {message.kind === "attachment" && <Badge tone="neutral">file</Badge>}
                          </div>
                        )}
                        <div
                          className={`whitespace-pre-wrap rounded-xl border px-3.5 py-2.5 text-[12.5px] leading-relaxed ${
                            mine
                              ? "border-signal/25 bg-signal/10 text-text"
                              : isBacktest
                                ? "border-signal/25 bg-signal/5 text-text"
                                : "border-line/80 bg-ink/50 text-muted"
                          }`}
                        >
                          {message.content}
                        </div>
                        <GenerativeBlocks blocks={message.ui} />
                        <Sources sources={message.sources ?? []} />
                      </div>
                      {mine && <Avatar />}
                    </div>
                  );
                })}

                {sending && live?.text && (
                  <div className="flex gap-2.5">
                    <Avatar assistant />
                    <div className="max-w-[88%]">
                      <div className="mb-1 flex items-center gap-1.5">
                        <p className="font-mono text-[9px] uppercase tracking-[0.12em] text-signal/80">
                          {live.agent_name}
                        </p>
                        <Badge tone="signal">streaming</Badge>
                      </div>
                      <div className="whitespace-pre-wrap rounded-xl border border-signal/20 bg-signal/5 px-3.5 py-2.5 text-[12.5px] leading-relaxed text-text">
                        {live.text}
                        <span className="ml-0.5 inline-block h-3 w-1 animate-pulse bg-signal align-middle" />
                      </div>
                    </div>
                  </div>
                )}
                {sending && !live?.text && (
                  <div className="flex gap-2.5">
                    <Avatar assistant />
                    <div className="flex items-center gap-2 rounded-xl border border-line/80 bg-ink/50 px-3.5 py-2.5 text-[12px] text-muted">
                      <Loader2 size={13} className="animate-spin text-signal" />
                      Working — this includes a real backtest if you asked for a strategy.
                    </div>
                  </div>
                )}
                <div ref={bottomRef} />
              </div>
            </div>

            <div className="border-t border-line/70 p-3">
              {error && <Notice tone="danger" className="mb-2">{error}</Notice>}
              <div className="rounded-xl border border-line bg-ink/60 p-2 focus-within:border-signal/40">
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".pdf,.epub,.txt,.md,.markdown,.csv,.json"
                  className="hidden"
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    if (file) void uploadFile(file);
                  }}
                />
                <textarea
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                      void send();
                    }
                  }}
                  placeholder="Ask for a strategy or a question — e.g. Create me a robust Nasdaq trading strategy."
                  className="min-h-[54px] w-full resize-none bg-transparent px-1.5 py-1 text-[13px] leading-relaxed text-text outline-none placeholder:text-muted/50"
                  disabled={sending}
                />
                <div className="flex flex-wrap items-center gap-2 px-1">
                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    disabled={uploading || sending}
                    className="focus-ring inline-flex items-center gap-1 rounded-md border border-line/80 px-2 py-1 text-[10px] text-muted transition hover:border-signal/40 hover:text-text disabled:opacity-50"
                    title="Attach a PDF, EPUB, text or Markdown file"
                  >
                    {uploading ? <Loader2 size={11} className="animate-spin" /> : <Paperclip size={11} />}
                    File
                  </button>
                  <label className="flex items-center gap-1.5 font-mono text-[9px] uppercase tracking-wider text-muted/55">
                    <input
                      type="checkbox"
                      checked={useWeb}
                      onChange={(event) => setUseWeb(event.target.checked)}
                      className="h-3 w-3 accent-signal"
                    />
                    web
                  </label>
                  <label className="flex items-center gap-1.5 font-mono text-[9px] uppercase tracking-wider text-muted/55">
                    <input
                      type="checkbox"
                      checked={useDeep}
                      onChange={(event) => setUseDeep(event.target.checked)}
                      className="h-3 w-3 accent-signal"
                    />
                    deep
                  </label>
                  <Button
                    variant="primary"
                    size="sm"
                    className="ml-auto"
                    onClick={() => void send()}
                    loading={sending}
                    disabled={!draft.trim()}
                  >
                    <Send size={13} /> Send
                  </Button>
                </div>
              </div>
              <p className="mt-2 text-[10px] leading-relaxed text-muted/60">
                Backtests use real market data, count every trial and leave the sealed holdout untouched.
                Nothing here routes an order.
              </p>
            </div>
          </Panel>
        </div>
      )}
    </div>
  );
}
