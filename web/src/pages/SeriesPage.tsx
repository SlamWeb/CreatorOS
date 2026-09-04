import { Link, useParams, useSearchParams } from "react-router-dom";
import { useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useSeries, useTopics } from "../api/hooks";
import { ApiError, studioApi } from "../api/client";
import type { OperationPlanInput } from "../api/types";
import { EmptyState, ErrorState, LoadingState } from "../components/PageState";
import { StatusPill } from "../components/StatusPill";
import { BackLink } from "./CreatorDetailPage";

export function SeriesPage() {
  const { seriesId } = useParams();
  const seriesQuery = useSeries(seriesId);
  const topicsQuery = useTopics(seriesId);
  const [topicDraft, setTopicDraft] = useState("");
  const [, setParams] = useSearchParams();
  const queryClient = useQueryClient();
  const previewMutation = useMutation({
    retry: false,
    mutationFn: (input: { plan: OperationPlanInput; count: number }) => studioApi.previewOperation({ request_text: `为「${seriesQuery.data?.name ?? "当前栏目"}」添加 ${input.count} 个选题`, plan: input.plan, series_id: seriesId }),
    onSuccess: (operation) => { setTopicDraft(""); setParams(p => { p.set("operation", operation.id); return p; }); },
  });
  const runMutation = useMutation({
    mutationFn: async (input: { topicId: string; runId?: string; version?: number }) => {
      const run = input.runId ? { id: input.runId, version: input.version! } : await studioApi.startRun({ topic_id: input.topicId });
      return studioApi.executeRun(run.id, run.version);
    },
    onSettled: async () => {
      await queryClient.invalidateQueries({ queryKey: ["topics", seriesId] });
      await queryClient.invalidateQueries({ queryKey: ["series", seriesId] });
      await queryClient.invalidateQueries({ queryKey: ["overview"] });
      await queryClient.invalidateQueries({ queryKey: ["runs"] });
    },
  });
  if (seriesQuery.isPending || topicsQuery.isPending) return <LoadingState label="正在读取栏目与选题…" />;
  if (seriesQuery.isError) return <ErrorState message={seriesQuery.error.message} onRetry={() => void seriesQuery.refetch()} />;
  if (topicsQuery.isError) return <ErrorState message={topicsQuery.error.message} onRetry={() => void topicsQuery.refetch()} />;
  const series = seriesQuery.data;
  const topics = topicsQuery.data.items;
  const submitTopics = (event: FormEvent) => {
    event.preventDefault();
    const titles = topicDraft.split("\n").map((title) => title.trim()).filter(Boolean);
    if (!titles.length || !seriesId) return;
    const plan: OperationPlanInput = { schema_version: 1, operations: [{ action: "add_topics", series_id: seriesId, topics: titles.map((title) => ({ topic_id: `topic-${crypto.randomUUID().replaceAll("-", "").slice(0, 20)}`, title, source: "manual" })) }] };
    previewMutation.mutate({ plan, count: titles.length });
  };
  return <><BackLink to={`/creators/${series.creator_id}`} label="返回账号" /><div className="page-heading series-heading"><div><p className="section-kicker">SERIES</p><h1>{series.name}</h1><p className="page-subtitle">{series.description || "暂未填写栏目定位"}</p></div><StatusPill status={series.is_active ? "active" : "cancelled"} /></div><div className="series-summary"><div><span>受众</span><b>{series.audience || "未设置"}</b></div><div><span>生产 Skill</span><b>{series.skill_name}</b></div><div><span>可开始选题</span><b>{series.available_topic_count}</b></div></div><section className="content-section"><div className="section-title"><h2>选题队列 <span className="count-badge">{topicsQuery.data.page.total}</span></h2></div><form className="topic-form" onSubmit={submitTopics}><label>添加选题 <small>每行一个标题，先 Preview 再确认写入</small><textarea value={topicDraft} onChange={(event) => setTopicDraft(event.target.value)} placeholder="例如：AgentState 和 Messages 有什么区别？\nTool Calling 为什么需要 Schema？" rows={3} /></label>{previewMutation.isError ? <p className="form-error">{previewMutation.error.message}</p> : null}<button className="button button-primary" type="submit" disabled={!series.is_active || previewMutation.isPending || !topicDraft.trim()}>{previewMutation.isPending ? "生成 Preview…" : "生成 Preview"}</button><button className="button button-secondary" type="button" onClick={() => setParams(p => { p.delete("operation"); p.set("command", "new"); p.set("series", series.id); return p; })}>用一句话调整</button></form>{runMutation.isError ? <p className="form-error">{runMutation.error.message} {runMutation.error instanceof ApiError && runMutation.error.runId ? <Link to={`/runs/${runMutation.error.runId}`}>查看当前运行 →</Link> : null}</p> : null}{topics.length ? <div className="topic-list">{topics.map((topic) => <div className="topic-row" key={topic.id}><span className="topic-position">{String(topic.position).padStart(2, "0")}</span><div className="topic-copy"><h3>{topic.title}</h3><p>{topic.brief || "没有补充说明"}</p><small>{topic.source} · {topic.existing_run_id ? `已有运行 · ${topic.existing_run_status ?? "已创建"}` : "尚未生产"}</small></div><StatusPill status={topic.existing_run_status ?? topic.status} />{topic.available_actions.includes("start") || topic.available_actions.includes("resume") ? <button className="topic-run-button" title="会在后台调用 Codex，浏览器不会等待生产完成" type="button" disabled={runMutation.isPending} onClick={() => runMutation.mutate({ topicId: topic.id, runId: topic.existing_run_id ?? undefined, version: topic.existing_run_version ?? undefined })}>{runMutation.isPending ? "正在提交…" : topic.available_actions.includes("resume") ? "恢复生产" : "开始生产"}</button> : <Link className="topic-action" to={topic.existing_run_id ? `/runs/${topic.existing_run_id}` : "/runs"}>查看运行 →</Link>}</div>)}</div> : <EmptyState title="还没有选题">在上面的输入框逐行添加，确认后才会进入队列。</EmptyState>}</section><div className="notice-strip">开始生产会在后台调用 Codex，不会让浏览器等待；你可以离开页面，运行状态会自动刷新。</div></>;
}
