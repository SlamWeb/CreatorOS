import { Link } from "react-router-dom";
import { useCreators } from "../api/hooks";
import { CreatorCard } from "../components/CreatorCard";
import { EmptyState, ErrorState, LoadingState } from "../components/PageState";

export function CreatorsPage() {
  const query = useCreators();
  if (query.isPending) return <LoadingState />;
  if (query.isError) return <ErrorState message={query.error.message} onRetry={() => void query.refetch()} />;
  const { items, page } = query.data;
  return <>
    <div className="page-heading"><div><p className="section-kicker">CREATORS</p><h1>账号目录</h1><p className="page-subtitle">每个账号都有自己的栏目、选题队列和内容运行。</p></div><button className="button button-quiet" disabled title="创建功能将在 S3 接入">创建账号 · S3</button></div>
    {items.length ? <><div className="directory-toolbar"><span>已配置 {page.total} 个账号</span><span className="toolbar-note">数据来自本地 Studio API</span></div><div className="creator-grid creator-grid-wide">{items.map((creator) => <CreatorCard key={creator.id} creator={creator} />)}</div></> : <EmptyState title="还没有账号"><span>账号目录为空。创建功能将在下一阶段接入，届时从这里开始设置你的第一个内容矩阵。</span><br /><Link className="text-link" to="/">回到今日 →</Link></EmptyState>}
  </>;
}
