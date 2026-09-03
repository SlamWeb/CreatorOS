import { Link, useParams } from "react-router-dom";
import { useCreator } from "../api/hooks";
import { EmptyState, ErrorState, LoadingState } from "../components/PageState";
import { platformLabel, StatusPill } from "../components/StatusPill";

export function CreatorDetailPage() {
  const { creatorId } = useParams();
  const query = useCreator(creatorId);
  if (query.isPending) return <LoadingState />;
  if (query.isError) return <ErrorState message={query.error.message} onRetry={() => void query.refetch()} />;
  const creator = query.data;
  return <><BackLink to="/creators" label="账号目录" /><div className="detail-heading"><div className="avatar avatar-large">{creator.display_name.slice(0, 1)}</div><div><p className="section-kicker">ACCOUNT</p><h1>{creator.display_name}</h1><p className="page-subtitle">{platformLabel(creator.platform)}{creator.account_handle ? ` · ${creator.account_handle}` : ""} · {creator.timezone}</p></div><StatusPill status={creator.is_active ? "active" : "cancelled"} /></div><div className="detail-meta-row"><span><b>{creator.series.length}</b> 个栏目</span><span>每日上限 <b>{creator.daily_content_limit ?? "未设置"}</b></span><button className="button button-quiet" disabled title="编辑功能将在后续阶段">编辑账号 · 后续阶段</button></div><section className="content-section"><div className="section-title"><h2>内容栏目 <span className="count-badge">{creator.series.length}</span></h2></div>{creator.series.length ? <div className="series-detail-grid">{creator.series.map((series) => <Link to={`/series/${series.id}`} className="series-card" key={series.id}><div className="series-card-head"><span className="series-icon">✦</span><StatusPill status={series.is_active ? "active" : "cancelled"} /></div><h3>{series.name}</h3><p>{series.description || "暂未填写栏目定位"}</p><div className="series-card-foot"><span>{series.topic_count} 个选题</span><span>{series.available_topic_count} 可开始</span><span>打开 →</span></div></Link>)}</div> : <EmptyState title="这个账号还没有栏目">栏目会在下一阶段从这里创建。</EmptyState>}</section></>;
}

export function BackLink({ to, label }: { to: string; label: string }) { return <Link className="back-link" to={to}>← {label}</Link>; }
