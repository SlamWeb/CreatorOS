import { Link, useParams } from "react-router-dom";
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useCreator } from "../api/hooks";
import { studioApi } from "../api/client";
import { EmptyState, ErrorState, LoadingState } from "../components/PageState";
import { platformLabel, StatusPill } from "../components/StatusPill";

export function CreatorDetailPage() {
  const { creatorId } = useParams();
  const [formOpen, setFormOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [audience, setAudience] = useState("");
  const queryClient = useQueryClient();
  const createMutation = useMutation({
    mutationFn: () => studioApi.createSeries(creatorId ?? "", { name, description, audience }),
    onSuccess: async (series) => { await queryClient.invalidateQueries({ queryKey: ["creator", creatorId] }); await queryClient.invalidateQueries({ queryKey: ["overview"] }); window.location.assign(`/series/${series.id}`); },
  });
  const query = useCreator(creatorId);
  if (query.isPending) return <LoadingState />;
  if (query.isError) return <ErrorState message={query.error.message} onRetry={() => void query.refetch()} />;
  const creator = query.data;
  return <><BackLink to="/creators" label="账号目录" /><div className="detail-heading"><div className="avatar avatar-large">{creator.display_name.slice(0, 1)}</div><div><p className="section-kicker">ACCOUNT</p><h1>{creator.display_name}</h1><p className="page-subtitle">{platformLabel(creator.platform)}{creator.account_handle ? ` · ${creator.account_handle}` : ""} · {creator.timezone}</p></div><StatusPill status={creator.is_active ? "active" : "cancelled"} /></div><div className="detail-meta-row"><span><b>{creator.series.length}</b> 个栏目</span><span>每日上限 <b>{creator.daily_content_limit ?? "未设置"}</b></span><button className="button button-primary" onClick={() => setFormOpen((value) => !value)}>{formOpen ? "收起" : "创建栏目"}</button></div>{formOpen ? <form className="inline-form" onSubmit={(event) => { event.preventDefault(); createMutation.mutate(); }}><div className="form-heading"><div><span className="card-label">NEW SERIES</span><h2>定义一个内容栏目</h2></div><span className="form-note">Skill 固定为 knowledge-to-carousel</span></div><div className="form-grid"><label>栏目名称<input value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：Agent 每日一题" required maxLength={120} /></label><label>目标受众<input value={audience} onChange={(event) => setAudience(event.target.value)} placeholder="例如：准备面试的开发者" maxLength={4000} /></label></div><label>栏目定位<textarea value={description} onChange={(event) => setDescription(event.target.value)} placeholder="这个栏目持续讲清什么问题？" rows={3} maxLength={10000} /></label>{createMutation.isError ? <p className="form-error">{createMutation.error.message}</p> : null}<div className="form-actions"><button className="button button-primary" type="submit" disabled={createMutation.isPending}>{createMutation.isPending ? "保存中…" : "保存栏目"}</button><button className="button button-quiet" type="button" onClick={() => setFormOpen(false)}>取消</button></div></form> : null}<section className="content-section"><div className="section-title"><h2>内容栏目 <span className="count-badge">{creator.series.length}</span></h2></div>{creator.series.length ? <div className="series-detail-grid">{creator.series.map((series) => <Link to={`/series/${series.id}`} className="series-card" key={series.id}><div className="series-card-head"><span className="series-icon">✦</span><StatusPill status={series.is_active ? "active" : "cancelled"} /></div><h3>{series.name}</h3><p>{series.description || "暂未填写栏目定位"}</p><div className="series-card-foot"><span>{series.topic_count} 个选题</span><span>{series.available_topic_count} 可开始</span><span>打开 →</span></div></Link>)}</div> : <EmptyState title="这个账号还没有栏目">用上方的“创建栏目”开始定义它。</EmptyState>}</section></>;
}

export function BackLink({ to, label }: { to: string; label: string }) { return <Link className="back-link" to={to}>← {label}</Link>; }
