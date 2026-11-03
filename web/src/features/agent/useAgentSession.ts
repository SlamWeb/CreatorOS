import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiUrl, request } from "../../api/client";
import { applySessionPatch, newestChatSession, type ChatSession, type ChatSessionPatch } from "../../components/agentTranscript";

export const AGENT_SESSIONS_BASE = "/api/agent/sessions";

// A session is keyed by scope and id. Snapshots, polling and the live stream all
// merge through the same cache so older HTTP replies cannot replace newer events.
export function useAgentSession(active: boolean, scopeKey: string, id: string | null) {
  const cache = useQueryClient();
  const [connected, setConnected] = useState(false);
  const [streamReconnect, setStreamReconnect] = useState(0);
  const session = useQuery({ queryKey: ["agent-session", scopeKey, id], queryFn: async () => {
    const next = await request<ChatSession>(`${AGENT_SESSIONS_BASE}/${id}`, { cache: "no-store" });
    return newestChatSession(cache.getQueryData<ChatSession>(["agent-session", scopeKey, id]), next);
  }, enabled: active && !!id,
  refetchInterval: q => active && !connected && q.state.data?.status === "running" ? 2000 : false,
  refetchOnWindowFocus: !connected });

  useEffect(() => {
    setConnected(false);
    if (!active || !id || session.data?.status !== "running") return;
    let live = true;
    let recovering = false;
    let reconnectTimer: number | undefined;
    const source = new EventSource(apiUrl(`${AGENT_SESSIONS_BASE}/${id}/events`));
    const resync = () => {
      if (!live || recovering) return;
      recovering = true;
      source.close(); setConnected(false);
      // A missing base cannot be patched by guessing. Read an authoritative
      // snapshot, then reconnect; polling remains read-only if this fails.
      void request<ChatSession>(`${AGENT_SESSIONS_BASE}/${id}`, { cache: "no-store" }).then(next => {
        if (live && next.id === id) cache.setQueryData<ChatSession>(["agent-session", scopeKey, id], old => newestChatSession(old, next));
      }).catch(() => { /* Disconnected polling retains the current view. */ })
        .finally(() => { if (live) reconnectTimer = window.setTimeout(() => setStreamReconnect(value => value + 1), 1000); });
    };
    source.onopen = () => { if (live && !recovering) setConnected(true); };
    source.onerror = () => { if (live) setConnected(false); };
    source.addEventListener("snapshot", event => {
      if (!live || recovering) return;
      try {
        const next = JSON.parse((event as MessageEvent).data) as ChatSession;
        if (next.id !== id || !Array.isArray(next.entries)) { resync(); return; }
        cache.setQueryData<ChatSession>(["agent-session", scopeKey, id], old => newestChatSession(old, next));
        setConnected(true);
      } catch { resync(); }
    });
    source.addEventListener("patch", event => {
      if (!live || recovering) return;
      try {
        const patch = JSON.parse((event as MessageEvent).data) as ChatSessionPatch;
        const merged = applySessionPatch(cache.getQueryData<ChatSession>(["agent-session", scopeKey, id]), patch);
        if (merged.status === "resync") { resync(); return; }
        if (merged.status === "applied") cache.setQueryData(["agent-session", scopeKey, id], merged.session);
        setConnected(true);
      } catch { resync(); }
    });
    return () => { live = false; source.close(); window.clearTimeout(reconnectTimer); };
  }, [active, id, scopeKey, session.data?.status, streamReconnect, cache]);

  return { session, connected };
}
