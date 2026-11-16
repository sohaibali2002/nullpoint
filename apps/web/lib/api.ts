"use client";

import { useCallback, useEffect, useRef, useState } from "react";

const TOKEN_KEY = "np_token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null) {
  if (typeof window === "undefined") return;
  if (token) window.localStorage.setItem(TOKEN_KEY, token);
  else window.localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init.headers as Record<string, string> | undefined),
  };
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(`/api${path}`, { ...init, headers, cache: "no-store" });
  const text = await response.text();
  let body: any = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = { detail: text };
  }
  if (!response.ok) {
    const detail =
      typeof body?.detail === "string"
        ? body.detail
        : Array.isArray(body?.detail)
          ? body.detail.map((d: any) => d.msg ?? JSON.stringify(d)).join("; ")
          : `Request failed (${response.status})`;
    throw new ApiError(response.status, detail);
  }
  return body as T;
}

interface ApiState {
  loading: boolean;
  demo: boolean;
  error: string | null;
}

export async function uploadKnowledge(form: FormData): Promise<any> {
  const token = getToken();
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch("/api/knowledge/upload", { method: "POST", headers, body: form });
  const text = await response.text();
  let body: any = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = { detail: text };
  }
  if (!response.ok) {
    throw new ApiError(response.status, body?.detail ?? `Upload failed (${response.status})`);
  }
  return body;
}

export async function streamChat(
  path: string,
  payload: Record<string, unknown>,
  onEvent: (event: string, data: any) => void,
): Promise<void> {
  const token = getToken();
  const response = await fetch(`/api${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(payload),
    cache: "no-store",
  });
  if (!response.ok) {
    const text = await response.text();
    let detail = text || `Request failed (${response.status})`;
    try {
      detail = JSON.parse(text)?.detail ?? detail;
    } catch {
      // Keep the raw response when it is not JSON.
    }
    throw new ApiError(response.status, String(detail));
  }
  if (!response.body) return;
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      let event = "message";
      const dataLines: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7).trim();
        else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
      }
      if (dataLines.length) {
        try {
          onEvent(event, JSON.parse(dataLines.join("\n")));
        } catch {
          onEvent(event, { detail: dataLines.join("\n") });
        }
      }
      boundary = buffer.indexOf("\n\n");
    }
  }
}

export function useApi<T>(path: string | null, fallback: T) {
  const [data, setData] = useState<T>(fallback);
  const [state, setState] = useState<ApiState>({ loading: true, demo: false, error: null });
  const fallbackRef = useRef(fallback);
  fallbackRef.current = fallback;

  const load = useCallback(async () => {
    if (!path) {
      setData(fallbackRef.current);
      setState({ loading: false, demo: true, error: null });
      return;
    }
    const token = getToken();
    if (!token) {
      setData(fallbackRef.current);
      setState({ loading: false, demo: true, error: null });
      return;
    }
    setState((s) => ({ ...s, loading: true }));
    try {
      const payload = await api<T>(path);
      setData(payload);
      setState({ loading: false, demo: false, error: null });
    } catch (error) {
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        setData(fallbackRef.current);
        setState({ loading: false, demo: true, error: null });
        return;
      }
      setState({
        loading: false,
        demo: false,
        error: error instanceof Error ? error.message : "Request failed",
      });
    }
  }, [path]);

  useEffect(() => {
    void load();
  }, [load]);

  return { data, ...state, reload: load };
}

export function useLiveMessages(sprintId: string | null, fallback: any[]) {
  const [messages, setMessages] = useState<any[]>(fallback);
  const [demo, setDemo] = useState(true);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    if (!sprintId || !getToken()) {
      setMessages(fallback);
      setDemo(true);
      return;
    }
    let cancelled = false;
    const poll = async () => {
      try {
        const payload = await api<{ messages: any[] }>(`/sprints/${sprintId}/messages`);
        if (cancelled) return;
        setMessages(payload.messages);
        setDemo(false);
      } catch {
        if (!cancelled) setDemo(true);
      }
    };
    void poll();
    const timer = setInterval(() => {
      if (!running) void poll();
    }, 2500);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [sprintId, fallback, running]);

  return { messages, demo, setRunning };
}
