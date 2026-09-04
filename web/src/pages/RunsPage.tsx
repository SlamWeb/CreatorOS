import { Link, useParams } from "react-router-dom";
import { useRun, useRuns } from "../api/hooks";
import { RunControls } from "../components/RunControls";
import { EmptyState, ErrorState, LoadingState } from "../components/PageState";
import { formatDate, StatusPill } from "../components/StatusPill";
import type { RunDetail } from "../api/types";
import { BackLink } from "./CreatorDetailPage";

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
  return <><BackLink to="/runs" label="运行记录" /><div className="page-heading"><div><p className="section-kicker">RUN DETAIL</p><h1>{run.topic_title}</h1><p className="page-subtitle">{run.creator_name} · {run.series_name}</p></div><StatusPill status={run.status} /></div><RunControls run={run} /><div className="run-detail-grid"><div className="detail-panel"><span className="card-label">CURRENT VERSION</span><h2>Revision {run.active_revision_number}</h2><dl className="detail-list"><dt>运行状态</dt><dd><StatusPill status={run.status} /></dd><dt>最后更新</dt><dd>{formatDate(run.updated_at)}</dd><dt>错误摘要</dt><dd>{run.error_message ?? "—"}</dd></dl></div><div className="detail-panel"><span className="card-label">REVISIONS & ATTEMPTS</span>{run.revisions.length ? run.revisions.map((revision) => <Revision key={revision.id} revision={revision} />) : <p className="muted">暂无版本记录。</p>}</div></div><div className="notice-strip">生产在本机后台继续，离开页面不会取消任务。图片预览、批准和返工入口尚未开放。</div></>;
}

function Revision({ revision }: { revision: RunDetail["revisions"][number] }) { return <div className="revision-block"><div className="revision-head"><b>Revision {revision.revision_number}</b><span>{revision.artifact_available ? "有产物" : "暂无产物"}</span></div>{revision.attempts.map((attempt) => <div className="attempt-row" key={attempt.id}><span>Attempt {attempt.attempt_number}</span><StatusPill status={attempt.status} /><span>{formatDate(attempt.started_at)}</span></div>)}</div>; }

export { BackLink } from "./CreatorDetailPage";
