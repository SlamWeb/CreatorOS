import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { apiUrl, studioApi } from "./client";
import type { RunEventView } from "./types";

export function useRunEvents(runId: string) {
  const queryClient = useQueryClient();
  const [events, setEvents] = useState<RunEventView[]>([]);
  const [connection, setConnection] = useState("正在连接进度…");
  useEffect(() => {
    let disposed = false;
    let cursor = 0;
    let polling = false;
    setEvents([]);
    setConnection("正在连接进度…");
    const merge = (items: RunEventView[]) => {
      if (disposed || !items.length) return;
      cursor = Math.max(cursor, ...items.map((item) => item.id));
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
        let after = cursor;
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
    void poll();
    const source = new EventSource(apiUrl(`/api/runs/${encodeURIComponent(runId)}/events/stream`));
    source.addEventListener("snapshot", () => {
      if (!disposed) { setConnection("进度已连接"); refresh(); }
    });
    source.addEventListener("run_event", (message) => {
      if (disposed) return;
      try {
        const event = JSON.parse((message as MessageEvent).data) as RunEventView;
        if (event.run_id !== runId || event.id <= cursor) return;
        merge([event]);
        refresh();
      } catch { void poll(); }
    });
    source.onerror = () => { if (!disposed) setConnection("进度重连中 · 轮询仍在更新"); };
    const timer = window.setInterval(() => { if (document.visibilityState === "visible") void poll(); }, 10_000);
    return () => { disposed = true; source.close(); window.clearInterval(timer); };
  }, [queryClient, runId]);
  return { events, connection };
}
