"use client";

import {
  BookOpen,
  Bot,
  Globe2,
  Loader2,
  MessageSquarePlus,
  Plus,
  Send,
  Sparkles,
  Trash2,
  UserRound,
  Users,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { Badge, Button, Notice, Panel, PageHeader } from "@/components/ui";
import { api, getToken, streamChat, useApi } from "@/lib/api";
import { when } from "@/lib/format";
import type {
  Agent,
  ChatBlock,
  ChatSource,
  ChatThread,
  ChatThreadDetail,
  ChatTurn,
} from "@/lib/types";

const DEFAULT_ROOM_AGENTS = ["ideator", "data_engineer", "skeptic", "risk_analyst"];

function Avatar({ assistant = false, room = false }: { assistant?: boolean; room?: boolean }) {
  return (
    <div
      className={`grid h-8 w-8 shrink-0 place-items-center rounded-lg border ${
        assistant ? "border-signal/30 bg-signal/10 text-signal" : "border-line bg-slate text-muted"
      }`}
    >
      {room ? <Users size={15} /> : assistant ? <Bot size={15} /> : <UserRound size={14} />}
    </div>
  );
}

function Brief({ block }: { block: ChatBlock }) {
  if (!block.sections?.length) return null;
  return (
    <div className="rounded-xl border border-signal/25 bg-signal/5 p-3.5">
      <div className="mb-3 flex items-center gap-2">
        <Sparkles size={12} className="text-signal" />
        <p className="font-mono text-[9px] uppercase tracking-[0.12em] text-signal">research brief</p>
      </div>
      <div className="grid gap-2.5 sm:grid-cols-2">
        {block.sections.map((section) => (
          <div key={section.heading} className="rounded-lg border border-line/60 bg-ink/25 px-3 py-2.5">
            <p className="font-mono text-[9px] uppercase tracking-wider text-muted/70">{section.heading}</p>
            <p className="mt-1 text-[11px] leading-relaxed text-muted">{section.body}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

function Sources({ sources }: { sources: ChatSource[] }) {
  if (!sources.length) return null;
  return (
    <div className="mt-2 rounded-lg border border-line/70 bg-slate/30 px-2.5 py-2">
      <p className="mb-1.5 flex items-center gap-1.5 font-mono text-[9px] uppercase tracking-[0.12em] text-muted/70">
        <Globe2 size={10} /> sources
      </p>
      <ul className="space-y-1">
        {sources.map((source) => (
          <li key={source.id} className="text-[10px] leading-relaxed text-muted">
            <span className="mr-1 font-mono text-signal/80">[{source.id}]</span>
            <a
              href={source.url.startsWith("http") ? source.url : undefined}
              target="_blank"
              rel="noreferrer"
              className="underline decoration-line/80 underline-offset-2 hover:text-text"
            >
              {source.title || source.url}
            </a>
            <span className="ml-1 text-muted/50">· {source.provider}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function ChatPage() {
  const { data: threads, loading: threadsLoading, reload: reloadThreads } = useApi<ChatThread[]>("/chats", []);
  const { data: agentData } = useApi<{ agents: Agent[] }>("/agents", { agents: [] });
  const [signedIn, setSignedIn] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ChatThreadDetail | null>(null);
  const [choosing, setChoosing] = useState(false);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [creating, setCreating] = useState<string | null>(null);
  const [roomAgents, setRoomAgents] = useState<string[]>(DEFAULT_ROOM_AGENTS);
  const [useWeb, setUseWeb] = useState(true);
  const bottomRef = useRef<HTMLDivElement>(null);

  const agents = agentData.agents ?? [];
  const roomOptions = agents.filter((agent) => agent.key !== "orchestrator");
  const validRoomAgents = roomAgents.filter((key) => roomOptions.some((agent) => agent.key === key));
  const activeThread = detail?.thread ?? threads.find((thread) => thread.id === selectedId) ?? null;
  const isRoom = activeThread?.mode === "room" || activeThread?.mode === "auto";
  const isAuto = activeThread?.mode === "auto";
  const activeAgent = agents.find((agent) => agent.key === activeThread?.agent);
  const agentLabel = (key: string) =>
    agents.find((agent) => agent.key === key)?.name ?? key.replaceAll("_", " ");

  useEffect(() => {
    setSignedIn(Boolean(getToken()));
  }, []);

  useEffect(() => {
    if (!threads.length) {
      if (signedIn && !threadsLoading) setChoosing(true);
      return;
    }
    if (choosing) return;
    if (!selectedId || !threads.some((thread) => thread.id === selectedId)) {
      setSelectedId(threads[0].id);
    }
  }, [threads, selectedId, choosing, signedIn, threadsLoading]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    setError(null);
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
  }, [detail?.messages.length, sending]);

  const openCreated = async (created: ChatThreadDetail) => {
    setDetail(created);
    setSelectedId(created.thread.id);
    setChoosing(false);
    await reloadThreads();
  };

  const startChat = async (agent: string) => {
    if (!getToken()) return;
    setCreating(agent);
    setError(null);
    try {
      const created = await api<ChatThreadDetail>("/chats", {
        method: "POST",
        body: JSON.stringify({ agent }),
      });
      await openCreated(created);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start conversation");
    } finally {
      setCreating(null);
    }
  };

  const startAuto = async () => {
    if (!getToken()) return;
    setCreating("auto");
    setError(null);
    try {
      const created = await api<ChatThreadDetail>("/chats", {
        method: "POST",
        body: JSON.stringify({ auto: true }),
      });
      await openCreated(created);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start auto research");
    } finally {
      setCreating(null);
    }
  };

  const startRoom = async () => {
    if (!getToken() || validRoomAgents.length < 2) return;
    setCreating("room");
    setError(null);
    try {
      const created = await api<ChatThreadDetail>("/chats", {
        method: "POST",
        body: JSON.stringify({ agents: validRoomAgents }),
      });
      await openCreated(created);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not open the research room");
    } finally {
      setCreating(null);
    }
  };

  const toggleRoomAgent = (key: string) => {
    setRoomAgents((current) => {
      if (current.includes(key)) return current.filter((item) => item !== key);
      if (current.length >= 5) return current;
      return [...current, key];
    });
  };

  const openThread = (id: string) => {
    setSelectedId(id);
    setChoosing(false);
    setError(null);
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

  const refreshDetail = useCallback(async (id: string) => {
    try {
      setDetail(await api<ChatThreadDetail>(`/chats/${id}`));
    } catch {
      // The original error is more useful than a second retrieval error.
    }
  }, []);

  const send = async () => {
    const content = draft.trim();
    if (!detail || !content || sending || !getToken()) return;
    const optimistic: ChatTurn = {
      id: `pending-${Date.now()}`,
      thread_id: detail.thread.id,
      role: "user",
      agent: detail.thread.agent,
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
    setDraft("");
    setError(null);
    setSending(true);
    setDetail((current) => (current ? { ...current, messages: [...current.messages, optimistic] } : current));
    try {
      await streamChat(
        `/chats/${detail.thread.id}/messages/stream`,
        { content, use_web: useWeb },
        (event, data) => {
          if (event === "message" && data?.id) {
            setDetail((current) => {
              if (!current || current.messages.some((message) => message.id === data.id)) return current;
              return { ...current, messages: [...current.messages, data as ChatTurn] };
            });
          } else if (event === "error") {
            setError(data?.detail ?? "The agent could not reply");
          }
        },
      );
      await refreshDetail(detail.thread.id);
      await reloadThreads();
    } catch (err) {
      setError(err instanceof Error ? err.message : "The agent could not reply");
      await refreshDetail(detail.thread.id);
    } finally {
      setSending(false);
    }
  };

  const newChatButton = (
    <Button
      variant="secondary"
      onClick={() => {
        setChoosing(true);
        setSelectedId(null);
        setDetail(null);
        setError(null);
      }}
    >
      <Plus size={14} /> New chat
    </Button>
  );

  return (
    <div>
      <PageHeader
        eyebrow="Research floor"
        title="Agent chat"
        description="Talk one-to-one, or open a research room where specialists see the same evidence, argue with each other, and leave one cited synthesis. A room is still not evidence: the sprint gates decide what survives."
        actions={newChatButton}
      />

      {!signedIn ? (
        <Panel title="Sign in to talk to the agents" eyebrow="persistent conversations">
          <div className="px-5 py-8">
            <Notice tone="warn" className="mb-4">
              Agent chat needs an account so the thread, memory and knowledge context stay attached to
              your desk. The public demo pages remain available without one.
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
                <p className="mt-1 text-[12px] text-muted">{threads.length} saved</p>
              </div>
              <Badge tone="signal">live</Badge>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto">
              {threadsLoading && (
                <div className="flex items-center gap-2 px-4 py-5 text-xs text-muted">
                  <Loader2 size={13} className="animate-spin" /> Loading conversations
                </div>
              )}
              <ul className="divide-y divide-line/60">
                {threads.map((thread) => {
                  const selected = thread.id === selectedId;
                  return (
                    <li key={thread.id} className="group relative">
                      <button
                        onClick={() => openThread(thread.id)}
                        className={`focus-ring w-full px-4 py-3.5 pr-10 text-left transition ${
                          selected ? "bg-slate/80" : "hover:bg-slate/40"
                        }`}
                      >
                        <div className="flex items-center gap-2">
                          <span className={`text-[11px] font-medium ${selected ? "text-signal" : "text-text"}`}>
                            {thread.agent_name}
                          </span>
                          {(thread.mode === "room" || thread.mode === "auto") && (
                            <Badge tone="beam">{thread.mode === "auto" ? "auto" : "room"}</Badge>
                          )}
                          <span className="ml-auto font-mono text-[9px] text-muted/60">
                            {when(thread.updated_at)}
                          </span>
                        </div>
                        <p className="mt-1 truncate text-[11px] text-muted">{thread.title}</p>
                        <p className="mt-1 line-clamp-2 text-[10px] leading-relaxed text-muted/60">
                          {thread.last_message || "Start the conversation"}
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
              {!threadsLoading && threads.length === 0 && (
                <div className="px-4 py-8 text-center text-[11px] text-muted">
                  No conversations yet. Open a room or pick an agent.
                </div>
              )}
            </div>
          </Panel>

          <Panel className="flex min-h-[68vh] flex-col lg:h-[calc(100vh-220px)]">
            {choosing || !activeThread ? (
              <div className="flex min-h-0 flex-1 flex-col overflow-y-auto px-5 py-7">
                <div className="mx-auto w-full max-w-2xl">
                  <p className="label">New conversation</p>
                  <h2 className="mt-2 text-xl font-semibold tracking-tight text-text">
                    Put the desk in one room.
                  </h2>
                  <p className="mt-2 text-sm leading-relaxed text-muted">
                    A research room retrieves one shared evidence bundle, then lets each specialist
                    respond to the previous speaker. The orchestrator closes with the disagreements,
                    citations and the next falsification test.
                  </p>
                  {error && <Notice tone="danger" className="mt-4">{error}</Notice>}

                  <div className="mt-5 rounded-xl border border-beam/25 bg-beam/5 p-4">
                    <div className="flex items-center gap-2">
                      <span className="grid h-8 w-8 place-items-center rounded-lg border border-beam/30 bg-ink/30 text-beam">
                        <Sparkles size={15} />
                      </span>
                      <div>
                        <p className="text-[13px] font-medium text-text">Auto research</p>
                        <p className="text-[10px] text-muted">
                          One general chat · the orchestrator spawns the right specialists
                        </p>
                      </div>
                      <Button
                        variant="secondary"
                        size="sm"
                        className="ml-auto"
                        onClick={() => void startAuto()}
                        loading={creating === "auto"}
                      >
                        Open auto
                      </Button>
                    </div>
                    <p className="mt-2 text-[10px] leading-relaxed text-muted/70">
                      Just ask. The orchestrator chooses the team, retrieves shared evidence, and
                      brings back the debate plus a synthesis.
                    </p>
                  </div>

                  <div className="mt-3 rounded-xl border border-signal/25 bg-signal/10 p-4">
                    <div className="flex items-center gap-2">
                      <span className="grid h-8 w-8 place-items-center rounded-lg border border-signal/30 bg-ink/30 text-signal">
                        <Users size={15} />
                      </span>
                      <div>
                        <p className="text-[13px] font-medium text-text">Research room</p>
                        <p className="text-[10px] text-muted">2–5 specialists · shared evidence · cited synthesis</p>
                      </div>
                      <Badge tone="signal" className="ml-auto">multi-agent</Badge>
                    </div>
                    <div className="mt-3 flex flex-wrap gap-1.5">
                      {roomOptions.map((agent) => {
                        const selected = validRoomAgents.includes(agent.key);
                        return (
                          <button
                            key={agent.key}
                            onClick={() => toggleRoomAgent(agent.key)}
                            className={`focus-ring rounded-full border px-2.5 py-1 text-[10px] transition ${
                              selected
                                ? "border-signal/50 bg-signal/15 text-signal"
                                : "border-line bg-ink/30 text-muted hover:border-muted/40 hover:text-text"
                            }`}
                          >
                            {agent.name}
                          </button>
                        );
                      })}
                    </div>
                    <div className="mt-4 flex flex-col gap-2 border-t border-signal/15 pt-3 sm:flex-row sm:items-center sm:justify-between">
                      <label className="flex items-center gap-2 text-[10px] text-muted">
                        <input
                          type="checkbox"
                          checked={useWeb}
                          onChange={(event) => setUseWeb(event.target.checked)}
                          className="h-3 w-3 accent-signal"
                        />
                        Fresh Exa + Firecrawl evidence
                      </label>
                      <Button
                        variant="primary"
                        size="sm"
                        onClick={() => void startRoom()}
                        loading={creating === "room"}
                        disabled={validRoomAgents.length < 2}
                      >
                        <Users size={13} /> Open room
                      </Button>
                    </div>
                  </div>

                  <p className="label mt-6">Or talk to one specialist</p>
                  {agents.length === 0 ? (
                    <Notice tone="warn" className="mt-3">
                      The agent roster is still loading. If this persists, refresh the page.
                    </Notice>
                  ) : (
                    <div className="mt-3 grid gap-2 sm:grid-cols-2">
                      {agents.map((agent) => (
                        <button
                          key={agent.key}
                          onClick={() => void startChat(agent.key)}
                          disabled={Boolean(creating)}
                          className="focus-ring group flex min-h-[104px] flex-col rounded-xl border border-line bg-ink/40 p-3.5 text-left transition hover:border-signal/40 hover:bg-slate/60 disabled:opacity-60"
                        >
                          <div className="flex items-center gap-2">
                            <span className="grid h-7 w-7 place-items-center rounded-md border border-line bg-slate text-signal">
                              {creating === agent.key ? (
                                <Loader2 size={13} className="animate-spin" />
                              ) : (
                                <Bot size={13} />
                              )}
                            </span>
                            <span className="text-[12px] font-medium text-text">{agent.name}</span>
                          </div>
                          <p className="mt-2 line-clamp-3 text-[11px] leading-relaxed text-muted">
                            {agent.purpose}
                          </p>
                          <span className="mt-auto pt-2 font-mono text-[9px] uppercase tracking-wider text-muted/50">
                            {agent.model_slot} model
                          </span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <>
                <div className="flex items-center gap-3 border-b border-line/70 px-4 py-3">
                  <div
                    className={`grid h-9 w-9 shrink-0 place-items-center rounded-lg border ${
                      isRoom
                        ? "border-beam/30 bg-beam/10 text-beam"
                        : "border-signal/30 bg-signal/10 text-signal"
                    }`}
                  >
                    {isRoom ? <Users size={16} /> : <MessageSquarePlus size={16} />}
                  </div>
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <p className="truncate text-[13px] font-medium text-text">{activeThread.agent_name}</p>
                      <Badge tone="neutral">{activeThread.message_count} messages</Badge>
                      {isRoom && <Badge tone="beam">{activeThread.participant_names.length} specialists</Badge>}
                    </div>
                    <p className="truncate text-[10px] text-muted">
                      {isRoom ? activeThread.participant_names.join(" · ") : activeThread.title}
                    </p>
                  </div>
                  <div className="ml-auto hidden max-w-[45%] text-right text-[10px] leading-relaxed text-muted/60 md:block">
                    {isRoom ? activeThread.purpose : (activeAgent?.purpose ?? activeThread.purpose)}
                  </div>
                </div>

                <div className="min-h-0 flex-1 overflow-y-auto px-4 py-5">
                  {detail?.messages.length === 0 && (
                    <div className="mx-auto flex h-full max-w-lg flex-col items-center justify-center py-10 text-center">
                      <div className="grid h-11 w-11 place-items-center rounded-full border border-signal/30 bg-signal/10 text-signal">
                        {isRoom ? <Users size={18} /> : <Bot size={18} />}
                      </div>
                      <p className="mt-4 text-sm font-medium text-text">
                        {isRoom ? "Give the room a question worth arguing about" : `Start a conversation with the ${activeThread.agent_name.toLowerCase()}`}
                      </p>
                      <p className="mt-1.5 max-w-md text-xs leading-relaxed text-muted">
                        {isAuto
                          ? "Ask normally. The orchestrator will choose the specialists, gather shared evidence, and bring back the debate plus a synthesis."
                          : isRoom
                            ? "The specialists will share one evidence bundle, react to each other in sequence, and finish with a synthesis you can replay later."
                            : "Ask about a hypothesis, a data problem, a risk, or what would falsify an idea. No hidden execution and no live-trading hooks."}
                      </p>
                      <div className="mt-5 flex flex-wrap justify-center gap-1.5">
                        {(isRoom
                          ? [
                              "Compare momentum and mean reversion for a crowded mega-cap.",
                              "What would falsify an insider-selling signal?",
                              "Where is a backtest most likely to fool us?",
                            ]
                          : [
                              "What makes a strategy worth testing?",
                              "How would you falsify this idea?",
                              "Where is the data likely to mislead us?",
                            ]
                        ).map((prompt) => (
                          <button
                            key={prompt}
                            onClick={() => setDraft(prompt)}
                            className="focus-ring rounded-full border border-line bg-slate/50 px-2.5 py-1 text-[10px] text-muted transition hover:border-signal/30 hover:text-text"
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
                      const evidence = message.kind === "evidence";
                      const synthesis = message.kind === "synthesis";
                      const brief = message.ui?.find((block) => block.type === "brief");
                      return (
                        <div key={message.id} className={`flex gap-2.5 ${mine ? "justify-end" : ""}`}>
                          {!mine && <Avatar assistant room={isRoom && synthesis} />}
                          <div className={`max-w-[88%] ${mine ? "order-first" : ""}`}>
                            {!mine && (
                              <div className="mb-1 flex flex-wrap items-center gap-1.5">
                                <p className="font-mono text-[9px] uppercase tracking-[0.12em] text-signal/80">
                                  {message.agent_name || agentLabel(message.agent)}
                                </p>
                                {(evidence || synthesis) && (
                                  <Badge tone={evidence ? "neutral" : "signal"}>
                                    {evidence ? "evidence" : "synthesis"}
                                  </Badge>
                                )}
                              </div>
                            )}
                            {brief ? (
                              <Brief block={brief} />
                            ) : (
                              <div
                                className={`whitespace-pre-wrap rounded-xl border px-3.5 py-2.5 text-[12.5px] leading-relaxed ${
                                  mine
                                    ? "border-signal/25 bg-signal/10 text-text"
                                    : evidence
                                      ? "border-line/80 bg-slate/35 text-muted"
                                      : synthesis
                                        ? "border-signal/25 bg-signal/5 text-text"
                                        : "border-line/80 bg-ink/50 text-muted"
                                }`}
                              >
                                {evidence && (
                                  <span className="mb-1 flex items-center gap-1.5 font-mono text-[9px] uppercase tracking-wider text-muted/70">
                                    <BookOpen size={10} /> librarian evidence
                                  </span>
                                )}
                                {message.content}
                              </div>
                            )}
                            <Sources sources={message.sources ?? []} />
                            {!mine && (message.model || message.tokens_out) && !evidence && (
                              <p className="mt-1 font-mono text-[9px] text-muted/45">
                                {message.model ?? "model"} · {message.tokens_in ?? 0} in /{" "}
                                {message.tokens_out ?? 0} out
                              </p>
                            )}
                          </div>
                          {mine && <Avatar />}
                        </div>
                      );
                    })}
                    {sending && (
                      <div className="flex gap-2.5">
                        <Avatar assistant room={isRoom} />
                        <div className="flex items-center gap-2 rounded-xl border border-line/80 bg-ink/50 px-3.5 py-2.5 text-[12px] text-muted">
                          <Loader2 size={13} className="animate-spin text-signal" />
                          {isRoom
                            ? `The room is examining the evidence${useWeb ? " and the web" : ""}…`
                            : `${activeThread.agent_name} is thinking`}
                        </div>
                      </div>
                    )}
                    <div ref={bottomRef} />
                  </div>
                </div>

                <div className="border-t border-line/70 p-3">
                  {error && <Notice tone="danger" className="mb-2">{error}</Notice>}
                  <div className="rounded-xl border border-line bg-ink/60 p-2 focus-within:border-signal/40">
                    <textarea
                      value={draft}
                      onChange={(event) => setDraft(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" && !event.shiftKey) {
                          event.preventDefault();
                          void send();
                        }
                      }}
                      placeholder={isRoom ? "Ask the room…" : `Message the ${activeThread.agent_name.toLowerCase()}…`}
                      className="min-h-[54px] w-full resize-none bg-transparent px-1.5 py-1 text-[13px] leading-relaxed text-text outline-none placeholder:text-muted/50"
                      disabled={sending}
                    />
                    <div className="flex items-center justify-between gap-2 px-1">
                      {isRoom ? (
                        <label className="flex items-center gap-2 font-mono text-[9px] uppercase tracking-wider text-muted/55">
                          <input
                            type="checkbox"
                            checked={useWeb}
                            onChange={(event) => setUseWeb(event.target.checked)}
                            className="h-3 w-3 accent-signal"
                          />
                          Exa + Firecrawl evidence
                        </label>
                      ) : (
                        <p className="font-mono text-[9px] uppercase tracking-wider text-muted/50">
                          Enter to send · Shift+Enter for a new line
                        </p>
                      )}
                      <Button
                        variant="primary"
                        size="sm"
                        onClick={() => void send()}
                        loading={sending}
                        disabled={!draft.trim()}
                      >
                        <Send size={13} /> Send
                      </Button>
                    </div>
                  </div>
                  <p className="mt-2 text-[10px] leading-relaxed text-muted/60">
                    {isRoom
                      ? "Room messages are visible and replayable. Sources are evidence, not a backtest; no live order routing exists."
                      : "Chat is research discussion, not evidence. Backtests must still clear the sprint gates; no live order routing exists."}
                  </p>
                </div>
              </>
            )}
          </Panel>
        </div>
      )}
    </div>
  );
}
