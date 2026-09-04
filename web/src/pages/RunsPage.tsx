import { Link, useParams } from "react-router-dom";
import { useRun, useRuns } from "../api/hooks";
import { RunInspector } from "../components/RunInspector";
import { EmptyState, ErrorState, LoadingState } from "../components/PageState";
import { formatDate, StatusPill } from "../components/StatusPill";

export function RunsPage() {
  const query = useRuns();
  if (query.isPending) return <LoadingState />;
  if (query.isError) return <ErrorState message={query.error.message} onRetry={() => void query.refetch()} />;
  const runs = query.data.items;
  return <><div className="page-heading"><div><p className="section-kicker">RUNS</p><h1>运行记录</h1><p className="page-subtitle">每次内容生产的真实状态、版本和失败原因都在这里。</p></div><span className="toolbar-note">共 {query.data.page.total} 次运行</span></div>{runs.length ? <div className="runs-list">{runs.map((run) => <Link to={`/runs/${run.id}`} className="run-card" key={run.id}><div className="run-card-main"><p className="section-kicker">{run.creator_name} · {run.series_name}</p><h3>{run.topic_title}</h3><p className="run-card-meta">版本 {run.active_revision_number} · 更新于 {formatDate(run.updated_at)}</p></div><StatusPill status={run.status} /><span className="row-arrow">→</span></Link>)}</div> : <EmptyState title="还没有运行记录">当栏目里有了选题并开始生产，运行记录会在这里出现。<br /><Link className="text-link" to="/creators">先查看账号目录 →</Link></EmptyState>}</>;
}

export function RunDetailPage() {
  const { runId } = useParams();
  const query = useRun(runId);
  if (query.isPending) return <LoadingState />;
  if (query.isError) return <ErrorState message={query.error.message} onRetry={() => void query.refetch()} />;
  const run = query.data;
  return <RunInspector key={run.id} run={run} />;
}

export { BackLink } from "./CreatorDetailPage";
