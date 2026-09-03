import { useParams } from "react-router-dom";
import { useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useSeries, useTopics } from "../api/hooks";
import { studioApi } from "../api/client";
import type { OperationPlanInput, PendingOperationView } from "../api/types";
import { EmptyState, ErrorState, LoadingState } from "../components/PageState";
import { StatusPill } from "../components/StatusPill";
import { BackLink } from "./CreatorDetailPage";

export function SeriesPage() {
  const { seriesId } = useParams();
  const seriesQuery = useSeries(seriesId);
  const topicsQuery = useTopics(seriesId);
  const [topicDraft, setTopicDraft] = useState("");
  const [preview, setPreview] = useState<PendingOperationView | null>(null);
  const [previewTitles, setPreviewTitles] = useState<string[]>([]);
  const [success, setSuccess] = useState("");
  const queryClient = useQueryClient();
  const previewMutation = useMutation({
    mutationFn: (input: { plan: OperationPlanInput; count: number }) => studioApi.previewOperation({ request_text: `为「${seriesQuery.data?.name ?? "当前栏目"}」添加 ${input.count} 个选题`, plan: input.plan }),
    onSuccess: (operation) => { setPreview(operation); setSuccess(""); },
  });
  const confirmMutation = useMutation({
    mutationFn: () => studioApi.confirmOperation(preview?.id ?? "", { expected_version: preview?.version ?? 0, expected_revision: preview?.revision ?? 0, confirmation_token: preview?.confirmation_token ?? "" }),
    onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ["topics", seriesId] }); await queryClient.invalidateQueries({ queryKey: ["series", seriesId] }); await queryClient.invalidateQueries({ queryKey: ["overview"] }); setPreview(null); setTopicDraft(""); setPreviewTitles([]); setSuccess("选题已确认写入队列。"); },
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
    setPreviewTitles(titles);
    previewMutation.mutate({ plan, count: titles.length });
  };
  return <><BackLink to={`/creators/${series.creator_id}`} label="返回账号" /><div className="page-heading series-heading"><div><p className="section-kicker">SERIES</p><h1>{series.name}</h1><p className="page-subtitle">{series.description || "暂未填写栏目定位"}</p></div><StatusPill status={series.is_active ? "active" : "cancelled"} /></div><div className="series-summary"><div><span>受众</span><b>{series.audience || "未设置"}</b></div><div><span>生产 Skill</span><b>{series.skill_name}</b></div><div><span>可开始选题</span><b>{series.available_topic_count}</b></div></div><section className="content-section"><div className="section-title"><h2>选题队列 <span className="count-badge">{topicsQuery.data.page.total}</span></h2></div><form className="topic-form" onSubmit={submitTopics}><label>添加选题 <small>每行一个标题，先 Preview 再确认写入</small><textarea value={topicDraft} onChange={(event) => setTopicDraft(event.target.value)} placeholder="例如：AgentState 和 Messages 有什么区别？\nTool Calling 为什么需要 Schema？" rows={3} /></label>{previewMutation.isError ? <p className="form-error">{previewMutation.error.message}</p> : null}<button className="button button-primary" type="submit" disabled={!series.is_active || previewMutation.isPending || !topicDraft.trim()}>{previewMutation.isPending ? "生成 Preview…" : "生成 Preview"}</button></form>{success ? <p className="form-success">✓ {success}</p> : null}{preview ? <div className="preview-card"><div className="preview-card-head"><div><span className="card-label">PREVIEW · REVISION {preview.revision}</span><h3>确认把这 {previewTitles.length} 个选题加入队列？</h3></div><button className="icon-button" type="button" aria-label="关闭 Preview" onClick={() => setPreview(null)}>×</button></div><ol>{previewTitles.map((title) => <li key={title}>{title}</li>)}</ol><p className="preview-note">确认前数据库不会新增 Topic；确认后才会按当前顺序写入。</p>{confirmMutation.isError ? <p className="form-error">{confirmMutation.error.message}</p> : null}<div className="form-actions"><button className="button button-primary" type="button" disabled={confirmMutation.isPending} onClick={() => confirmMutation.mutate()}>{confirmMutation.isPending ? "确认写入…" : "确认写入队列"}</button><button className="button button-quiet" type="button" onClick={() => setPreview(null)}>先不写入</button></div></div> : null}{topics.length ? <div className="topic-list">{topics.map((topic) => <div className="topic-row" key={topic.id}><span className="topic-position">{String(topic.position).padStart(2, "0")}</span><div className="topic-copy"><h3>{topic.title}</h3><p>{topic.brief || "没有补充说明"}</p><small>{topic.source} · {topic.existing_run_id ? `已有运行 · ${topic.existing_run_status ?? "已创建"}` : "尚未生产"}</small></div><StatusPill status={topic.existing_run_status ?? topic.status} /><span className="topic-action">{topic.available_actions.includes("start") ? "可开始" : topic.available_actions.includes("resume") ? "可恢复" : "查看"}</span></div>)}</div> : <EmptyState title="还没有选题">在上面的输入框逐行添加，确认后才会进入队列。</EmptyState>}</section><div className="notice-strip">选题写入会复用统一的 PendingOperation Preview → 确认链路；当前不会调用模型或开始生产。</div></>;
}
