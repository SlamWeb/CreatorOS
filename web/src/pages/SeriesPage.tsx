import { useParams } from "react-router-dom";
import { useSeries, useTopics } from "../api/hooks";
import { EmptyState, ErrorState, LoadingState } from "../components/PageState";
import { StatusPill } from "../components/StatusPill";
import { BackLink } from "./CreatorDetailPage";

export function SeriesPage() {
  const { seriesId } = useParams();
  const seriesQuery = useSeries(seriesId);
  const topicsQuery = useTopics(seriesId);
  if (seriesQuery.isPending || topicsQuery.isPending) return <LoadingState label="正在读取栏目与选题…" />;
  if (seriesQuery.isError) return <ErrorState message={seriesQuery.error.message} onRetry={() => void seriesQuery.refetch()} />;
  if (topicsQuery.isError) return <ErrorState message={topicsQuery.error.message} onRetry={() => void topicsQuery.refetch()} />;
  const series = seriesQuery.data;
  const topics = topicsQuery.data.items;
  return <><BackLink to={`/creators/${series.creator_id}`} label="返回账号" /><div className="page-heading series-heading"><div><p className="section-kicker">SERIES</p><h1>{series.name}</h1><p className="page-subtitle">{series.description || "暂未填写栏目定位"}</p></div><StatusPill status={series.is_active ? "active" : "cancelled"} /></div><div className="series-summary"><div><span>受众</span><b>{series.audience || "未设置"}</b></div><div><span>生产 Skill</span><b>{series.skill_name}</b></div><div><span>可开始选题</span><b>{series.available_topic_count}</b></div></div><section className="content-section"><div className="section-title"><h2>选题队列 <span className="count-badge">{topicsQuery.data.page.total}</span></h2><button className="button button-quiet" disabled title="创建选题将在 S3 接入">添加选题 · S3</button></div>{topics.length ? <div className="topic-list">{topics.map((topic) => <div className="topic-row" key={topic.id}><span className="topic-position">{String(topic.position).padStart(2, "0")}</span><div className="topic-copy"><h3>{topic.title}</h3><p>{topic.brief || "没有补充说明"}</p><small>{topic.source} · {topic.existing_run_id ? `已有运行 · ${topic.existing_run_status ?? "已创建"}` : "尚未生产"}</small></div><StatusPill status={topic.existing_run_status ?? topic.status} /><span className="topic-action">{topic.available_actions.includes("start") ? "可开始" : topic.available_actions.includes("resume") ? "可恢复" : "查看"}</span></div>)}</div> : <EmptyState title="还没有选题">添加选题后，队列会从这里进入内容生产。</EmptyState>}</section><div className="notice-strip">创建、调序和开始生产将在 S3/S4 接入；当前页面只展示真实数据，不会偷偷调用模型。</div></>;
}
