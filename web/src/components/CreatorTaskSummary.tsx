import { Link } from "react-router-dom";
import { useEffect, useId, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { studioApi } from "../api/client";
import { formatDate } from "./StatusPill";
import "./creator-task-summary.css";

const kindLabels = { research: "调研", production: "生产", discussion: "讨论", production_task: "制作", skill_iteration: "Skill迭代" } as const;
const statusLabels: Record<string, string> = {
  queued: "待开始", running: "进行中", researching: "调研中", producing: "生产中", validating: "验收中",
  completed: "已完成", approved: "已批准", ready: "已就绪", stale: "已过期", awaiting_approval: "待批准", failed: "失败", interrupted: "中断", cancelled: "已取消",
  waiting: "等待材料", needs_attention: "待核对",
};

export function CreatorTaskSummary({ creatorId, seriesId }: { creatorId: string; seriesId?: string | null }) {
  const listId = useId();
  const scope = `${creatorId}:${seriesId ?? "all"}`;
  const [expandedScope, setExpandedScope] = useState<string | null>(null);
  const expanded = expandedScope === scope;
  useEffect(() => setExpandedScope(null), [scope]);
  const tasks = useQuery({
    queryKey: ["creator-tasks", creatorId, seriesId ?? null],
    queryFn: () => studioApi.creatorTasks(creatorId, seriesId ?? undefined),
    retry: false,
    refetchInterval: query => query.state.data?.summary.active ? 3_000 : false,
  });
  if (tasks.isPending) return null;
  if (tasks.isError) return <p className="creator-task-error" role="status">任务状态暂不可读。<button type="button" className="text-link" onClick={() => void tasks.refetch()}>重新读取</button></p>;
  const data = tasks.data;
  const summary = [
    data.summary.active ? `进行中 ${data.summary.active}` : "",
    data.summary.awaiting_approval ? `待批准 ${data.summary.awaiting_approval}` : "",
    data.summary.needs_attention ? `待核对 ${data.summary.needs_attention}` : "",
  ].filter(Boolean);
  const items = data.items.slice(0, 5);
  if (!items.length && !summary.length && !data.summary.failed) return null;
  return <section className="creator-task-summary" aria-label="任务">
    <header><h2>任务</h2>{summary.length ? <p>{summary.join(" · ")}</p> : null}
      {!!data.summary.failed && <span className="creator-task-failure" role="status">失败 {data.summary.failed}</span>}
      {!summary.length && !data.summary.failed && <p>最近 {items.length} 项</p>}
      <button type="button" className="creator-task-toggle" aria-expanded={expanded} aria-controls={listId}
        onClick={() => setExpandedScope(expanded ? null : scope)}>{expanded ? "收起任务" : "查看任务"}</button>
    </header>
    <div id={listId} hidden={!expanded}>{items.length ? <ul>{items.map(item => {
      const activity = item.last_activity_at ?? item.updated_at;
      const executing = ["running", "researching", "producing", "validating"].includes(item.status);
      const content = <><span className="creator-task-kind">{item.task_kind === "video" ? "视频制作" : kindLabels[item.kind]}</span><span className="creator-task-title">{item.title}</span><span className={`creator-task-status creator-task-status-${item.status}`}><span className={executing ? "task-running-text" : undefined}>{statusLabels[item.status] ?? item.status}</span></span><time>{formatDate(activity)}</time></>;
      return <li key={`${item.kind}:${item.id}`}>{item.url?.startsWith("/") && !item.url.startsWith("//") ? <Link to={item.url}>{content}</Link> : <div>{content}</div>}
        {item.collection_url && <Link className="creator-task-collection" to={item.collection_url}>素材集：{item.collection_name ?? "查看素材"}</Link>}</li>;
    })}</ul> : <p className="creator-task-error">暂无可展示的最近记录。</p>}</div>
  </section>;
}
