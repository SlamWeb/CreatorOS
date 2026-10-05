import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ApiError, studioApi } from "../api/client";
import type { SkillExtractionEvent, SkillExtractionEventPage } from "../api/types";

const statusLabels = { running: "进行中", completed: "已完成", failed: "失败", interrupted: "已中断" } as const;

function mergeEvents(current: SkillExtractionEvent[], incoming: SkillExtractionEvent[]) {
  const byId = new Map(current.map(item => [item.id, item]));
  for (const item of incoming) byId.set(item.id, item);
  return [...byId.values()].sort((a, b) => a.id - b.id);
}

function formatTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "时间未记录" : date.toLocaleString("zh-CN");
}

export function ExtractionActivity({ jobId, active }: { jobId: string; active: boolean }) {
  const cache = useQueryClient();
  const [items, setItems] = useState<SkillExtractionEvent[]>([]);
  const [streamId, setStreamId] = useState("");
  const [hasMore, setHasMore] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [olderError, setOlderError] = useState("");
  const streamRef = useRef("");
  const loadedOlderRef = useRef(false);
  const itemsRef = useRef<SkillExtractionEvent[]>([]);
  const wasActive = useRef(active);
  const latest = useQuery({
    queryKey: ["skill-extraction-events", jobId],
    queryFn: () => studioApi.skillExtractionEvents(jobId, 0, 50),
    retry: false,
    refetchInterval: active ? 2000 : false,
  });

  useEffect(() => {
    const page = latest.data;
    if (!page) return;
    const changedStream = Boolean(streamRef.current && streamRef.current !== page.stream_id);
    if (changedStream) {
      cache.removeQueries({ queryKey: ["skill-extraction-event", jobId] });
      loadedOlderRef.current = false;
      itemsRef.current = page.items;
      setItems(page.items);
    } else {
      const merged = mergeEvents(itemsRef.current, page.items);
      itemsRef.current = merged;
      setItems(merged);
    }
    streamRef.current = page.stream_id;
    setStreamId(page.stream_id);
    if (page.items[0]?.id === 1) setHasMore(false);
    else if (changedStream || !loadedOlderRef.current) setHasMore(page.has_more);
    setOlderError("");
  }, [cache, jobId, latest.data]);

  useEffect(() => {
    if (wasActive.current && !active) void latest.refetch();
    wasActive.current = active;
  }, [active, latest.refetch]);

  const loadOlder = async () => {
    if (!items.length || loadingOlder) return;
    setLoadingOlder(true);
    setOlderError("");
    const expectedStream = streamRef.current;
    try {
      const page: SkillExtractionEventPage = await studioApi.skillExtractionEvents(jobId, items[0].id, 50);
      if (page.stream_id !== expectedStream || streamRef.current !== expectedStream) return;
      const merged = mergeEvents(itemsRef.current, page.items);
      itemsRef.current = merged;
      setItems(merged);
      loadedOlderRef.current ||= page.items.some(item => item.id < items[0].id);
      setHasMore(page.items[0]?.id === 1 ? false : page.has_more);
    } catch (error) {
      setOlderError(error instanceof Error ? error.message : "较早活动读取失败。");
    } finally {
      setLoadingOlder(false);
    }
  };

  const streamChangedBeforeRender = Boolean(latest.data?.stream_id && streamId && latest.data.stream_id !== streamId);
  const visibleItems = streamChangedBeforeRender ? [] : items;
  const noHistory = latest.isError && latest.error instanceof ApiError && latest.error.status === 404;

  return <details open className="extraction-activity" style={{ borderTop: "1px solid var(--line)", paddingTop: 10 }}>
    <summary>Codex 公开活动{visibleItems.length ? ` · ${visibleItems.length} 条` : ""}</summary>
    <div style={{ display: "grid", gap: 10, marginTop: 10 }}>
      {latest.isPending && <p className="extraction-help" role="status">正在读取公开活动…</p>}
      {latest.isError && !noHistory && <div className="extraction-error" role="alert">
        <p>公开活动读取失败：{latest.error.message}</p>
        <button type="button" className="button button-secondary" onClick={() => void latest.refetch()}>重试</button>
      </div>}
      {(noHistory || (!latest.isPending && !latest.isError && visibleItems.length === 0))
        && <p className="extraction-help">此任务没有已记录的公开活动；旧任务可能未保存这类记录。</p>}
      {visibleItems.map(item => <ActivityItem key={`${streamId}-${item.id}`} jobId={jobId} streamId={streamId} item={item} />)}
      {hasMore && visibleItems.length > 0 && <div>
        <button type="button" className="button button-secondary" disabled={loadingOlder} onClick={() => void loadOlder()}>
          {loadingOlder ? "正在读取…" : "加载更早活动"}
        </button>
        {olderError && <p className="extraction-error" role="alert">{olderError}</p>}
      </div>}
    </div>
  </details>;
}

function ActivityItem({ jobId, streamId, item }: { jobId: string; streamId: string; item: SkillExtractionEvent }) {
  const [expanded, setExpanded] = useState(false);
  const full = useQuery({
    queryKey: ["skill-extraction-event", jobId, streamId, item.id, item.updated_at],
    queryFn: () => studioApi.skillExtractionEvent(jobId, item.id, streamId),
    enabled: expanded && item.truncated,
    retry: false,
  });
  const text = item.truncated && expanded ? full.data?.text ?? item.text : item.text;
  const waitingForFullText = expanded && item.truncated && full.isPending;
  const label = item.kind === "message" ? "公开消息" : item.kind === "tool" ? "工具活动" : item.kind === "error" ? "错误" : "状态";
  const status = statusLabels[item.status];

  return <article style={{ border: "1px solid var(--line)", borderRadius: 8, padding: 10, minWidth: 0 }}>
    <div className="extraction-status">
      <strong>{item.title || label}</strong><span>{label} · {status}</span><span>{formatTime(item.updated_at || item.at)}</span>
    </div>
    {item.redacted && <p className="extraction-help">已对敏感内容脱敏。</p>}
    {(item.item_type || item.item_id) && <details>
      <summary className="extraction-help">技术标识</summary>
      <pre>{[item.item_type, item.item_id].filter(Boolean).join(" · ")}</pre>
    </details>}
    {item.kind === "message" ? <div className="extraction-activity-message">
      <Markdown remarkPlugins={[remarkGfm]} skipHtml components={{
        img: ({ alt }) => <span>{alt ?? "图片未加载"}</span>,
        a: ({ href, children }) => href ? <a href={href} target="_blank" rel="noopener noreferrer">{children}</a> : <>{children}</>,
      }}>{text || "（没有可显示的正文）"}</Markdown>
      {item.truncated && <button type="button" className="button button-secondary" aria-expanded={expanded}
        onClick={() => setExpanded(value => !value)}>{expanded ? "收起全文" : "展开全文"}</button>}
      {waitingForFullText && <span className="extraction-help" role="status">正在读取完整消息…</span>}
      {expanded && full.isError && <div className="extraction-error" role="alert">完整消息读取失败：{full.error.message}
        <button type="button" className="button button-secondary" onClick={() => void full.refetch()}>重试</button></div>}
    </div> : <>
      <button type="button" className="button button-secondary" aria-expanded={expanded}
        onClick={() => setExpanded(value => !value)}>{expanded ? "收起详情" : `查看${item.kind === "tool" ? "工具输入 / 结果" : "详情"}`}</button>
      {expanded && <>
        {waitingForFullText && <p className="extraction-help" role="status">正在读取完整内容…</p>}
        <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: 420, overflow: "auto" }}>{text || "（没有可显示的正文）"}</pre>
        {expanded && full.isError && <div className="extraction-error" role="alert">完整内容读取失败：{full.error.message}
          <button type="button" className="button button-secondary" onClick={() => void full.refetch()}>重试</button></div>}
      </>}
    </>}
    {expanded && full.data?.truncated && <p className="extraction-help">正文达到保存上限，已显示保存范围；不是完整原始日志。</p>}
  </article>;
}
