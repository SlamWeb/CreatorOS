import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { apiUrl, studioApi } from "./client";
import type { RunEventView } from "./types";

export function useRunEvents(runId: string, active: boolean) {
  const queryClient = useQueryClient();
  const [events, setEvents] = useState<RunEventView[]>([]);
  const [connection, setConnection] = useState("历史记录读取中…");
  const cursorRef = useRef(0);
  const runIdRef = useRef(runId);
  useEffect(() => {
    if (runIdRef.current !== runId) {
      runIdRef.current = runId;
      cursorRef.current = 0;
      setEvents([]);
    }
    let disposed = false;
    let polling = false;
    let source: EventSource | null = null;
    let timer: number | null = null;
    setConnection(active ? "状态连接中…" : "历史记录读取中…");
    const merge = (items: RunEventView[]) => {
      if (disposed || !items.length) return;
      cursorRef.current = Math.max(cursorRef.current, ...items.map((item) => item.id));
      setEvents((previous) => [...new Map([...previous, ...items].map((item) => [item.id, item])).values()].sort((a, b) => a.id - b.id));
    };
    const refresh = () => {
      void queryClient.invalidateQueries({ queryKey: ["run", runId] });
      void queryClient.invalidateQueries({ queryKey: ["overview"] });
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
    };
    const poll = async () => {
      if (polling) return;
      polling = true;
      try {
        let after = cursorRef.current;
        do {
          const batch = await studioApi.events(runId, after);
          if (disposed) return;
          merge(batch.items);
          after = batch.next_after_id;
          if (batch.items.length < 100) break;
        } while (!disposed);
      } catch { /* The run query already exposes API failures; keep the timeline. */ }
      finally { polling = false; }
    };
    void (async () => {
      await poll();
      if (disposed) return;
      if (!active) { setConnection("历史记录已读取"); return; }
      // The history request starts at zero. Subscribe after its cursor so events
      // created during the request are replayed without losing older history.
      source = new EventSource(apiUrl(`/api/runs/${encodeURIComponent(runId)}/events/stream?after_id=${cursorRef.current}`));
      source.addEventListener("snapshot", () => {
        if (!disposed) { setConnection("状态连接正常"); refresh(); }
      });
      source.addEventListener("run_event", (message) => {
        if (disposed) return;
        try {
          const event = JSON.parse((message as MessageEvent).data) as RunEventView;
          if (event.run_id !== runId || event.id <= cursorRef.current) return;
          merge([event]);
          refresh();
        } catch { void poll(); }
      });
      source.onerror = () => { if (!disposed) setConnection("状态重连中"); };
      timer = window.setInterval(() => { if (document.visibilityState === "visible") void poll(); }, 10_000);
    })();
    return () => {
      disposed = true;
      source?.close();
      if (timer !== null) window.clearInterval(timer);
    };
  }, [active, queryClient, runId]);
  return { events, connection };
}
