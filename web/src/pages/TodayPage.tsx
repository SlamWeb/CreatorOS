import { Link } from "react-router-dom";
import { useOverview } from "../api/hooks";
import { CreatorCard } from "../components/CreatorCard";
import { EmptyState, ErrorState, LoadingState } from "../components/PageState";
import { RunRow, SectionTitle, type RunRowData } from "../components/RunRow";

export function TodayPage() {
  const query = useOverview();
  if (query.isPending) return <LoadingState />;
  if (query.isError) return <ErrorState message={query.error.message} onRetry={() => void query.refetch()} />;
  const { counts, creators, needs_attention, producing, awaiting_approval, pending_operations } = query.data;
  const hasData = creators.length > 0;
  return <>
    <div className="page-heading"><div><p className="section-kicker">TODAY</p><h1>今天要做什么</h1><p className="page-subtitle">从账号和栏目出发，查看真实的选题与内容运行。</p></div><div className="heading-actions"><Link className="button button-primary" to="/creators">查看账号目录 <span>→</span></Link></div></div>
    <div className="metric-grid">
      <Metric label="运营账号" value={counts.active_creator_count} suffix={`/ ${counts.creator_count}`} />
      <Metric label="可运营栏目" value={counts.active_series_count} suffix={`/ ${counts.series_count}`} />
      <Metric label="生产中" value={counts.producing_count} tone={counts.producing_count > 0 ? "lavender" : undefined} />
      <Metric label="待批准" value={counts.awaiting_approval_count} tone={counts.awaiting_approval_count > 0 ? "gold" : undefined} />
    </div>
    {!hasData ? <div className="first-use-grid"><EmptyState title="还没有运营账号"><span>先创建一个账号和栏目，再把选题送进生产。</span><br /><Link className="text-link" to="/creators">创建第一个账号 →</Link></EmptyState><div className="next-card"><span className="card-label">NEXT</span><h3>准备好你的第一个栏目</h3><p>确定定位与受众，加入想做的选题。生产完成后，在这里检查图片并决定是否批准。</p><Link className="text-link" to="/creators">查看账号目录 →</Link></div></div> : <>
      <section className="content-section"><SectionTitle title="正在运营的账号" count={creators.length} link="/creators" /><div className="creator-grid">{creators.slice(0, 6).map((creator) => <CreatorCard key={creator.id} creator={creator} />)}</div></section>
      <section className="content-section"><SectionTitle title="运营队列" /><Link className="text-link" to="/?command=list">查看全部运营计划 →</Link><div className="queue-grid"><Queue title="待处理" hint="需要你下一步决定" runs={[...pending_operations.map((operation) => ({ id: operation.id, topic_title: operation.request_text, creator_name: "运营计划", series_name: operation.status, status: "queued", updated_at: operation.updated_at, href: `/?operation=${encodeURIComponent(operation.id)}` })), ...needs_attention]} empty="暂无需要处理的任务" /><Queue title="生产中" hint="离开页面也会保留状态" runs={producing} empty="没有正在生产的内容" /><Queue title="待批准" hint="检查后再进入下一步" runs={awaiting_approval} empty="暂无待批准内容" /></div></section>
    </>}
  </>;
}

function Metric({ label, value, suffix, tone }: { label: string; value: number; suffix?: string; tone?: string }) {
  return <div className={`metric-card ${tone ?? ""}`}><span>{label}</span><strong>{value}<small>{suffix}</small></strong></div>;
}

function Queue({ title, hint, runs, empty }: { title: string; hint: string; runs: RunRowData[]; empty: string }) {
  return <div className="queue-card"><div className="queue-head"><div><h3>{title}</h3><p>{hint}</p></div><span className="queue-count">{runs.length}</span></div>{runs.length ? runs.map((run) => <RunRow key={run.id} run={run} />) : <p className="queue-empty">{empty}</p>}</div>;
}
