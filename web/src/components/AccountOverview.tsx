import { useQuery } from "@tanstack/react-query";
import { GalleryVerticalEnd, Plus } from "lucide-react";
import { Link, useLocation } from "react-router-dom";
import { apiUrl, studioApi } from "../api/client";
import type { SeriesView } from "../api/types";
import { StatusPill } from "./StatusPill";
import "./account-overview.css";

export function AccountOverview({ creatorId, series, onSelect, onCreate, hasUnseen }: {
  creatorId: string | null; series: SeriesView[]; onSelect: (id: string) => void; onCreate: () => void;
  hasUnseen: (id: string) => boolean;
}) {
  const location = useLocation();
  const recent = useQuery({ queryKey: ["runs", "account-overview", creatorId],
    queryFn: () => studioApi.runsPage(0, creatorId!), enabled: !!creatorId, retry: false,
    refetchInterval: query => query.state.data?.items.some(run => ["queued", "producing", "validating"].includes(run.status)) ? 5000 : false });
  const visibleSeries = new Set(series.map(item => item.id));
  const seenTopics = new Set<string>();
  const works = (recent.data?.items ?? []).filter(run => run.creator_id === creatorId && visibleSeries.has(run.series_id) && run.cover_url)
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at) || b.id.localeCompare(a.id))
    .filter(run => { if (seenTopics.has(run.topic_id)) return false; seenTopics.add(run.topic_id); return true; }).slice(0, 6);

  return <div className="account-overview">
    <section className="account-overview-columns" aria-label="栏目">
      <header className="account-overview-section-head"><h2>栏目</h2></header>
      {series.length ? <div className="account-column-list">{series.map(item => <button type="button" key={item.id}
        onClick={() => onSelect(item.id)} aria-label={`进入栏目 ${item.name}`}>
        <GalleryVerticalEnd size={18} aria-hidden="true" /><span>{item.name}</span>
        {hasUnseen(item.id) && <span className="account-column-dot" aria-label="栏目有未查看内容" />}
      </button>)}</div> : <div className="account-overview-empty">
        <GalleryVerticalEnd size={24} aria-hidden="true" /><p>{creatorId ? "这个账号还没有栏目" : "还没有账号"}</p>
        <button type="button" className="button button-primary" onClick={onCreate}><Plus size={15} aria-hidden="true" />{creatorId ? "新建栏目" : "新建账号"}</button>
      </div>}
    </section>
    <section className="account-recent-works" aria-label="近期作品">
      <header className="account-overview-section-head"><h2>近期作品</h2></header>
      {recent.isFetching && !recent.data && creatorId && <p className="account-overview-note" role="status">正在读取作品…</p>}
      {recent.isError && <div className="account-overview-note form-error" role="alert">{recent.error.message}
        <button type="button" className="button button-quiet" onClick={() => void recent.refetch()}>重新读取</button></div>}
      {!recent.isError && (!creatorId || (!recent.isPending && !works.length)) && <p className="account-overview-note">还没有作品。进入栏目，添加选题后开始制作。</p>}
      {!!works.length && <div className="account-recent-grid">{works.map(run => <article className="account-work" key={run.id}>
        <Link aria-label={`查看内容 ${run.topic_title}`} to={`/runs/${encodeURIComponent(run.id)}?return=${encodeURIComponent(location.pathname + location.search)}`}>
          <div className="account-work-cover"><img src={apiUrl(run.cover_url!)} alt={run.topic_title} loading="lazy" /></div>
          <div className="account-work-copy"><h3>{run.topic_title}</h3><p>{run.series_name}</p></div>
        </Link>
        <div className="account-work-status"><StatusPill status={run.status} />{(run.card_count ?? 0) > 1 && <span>{run.card_count} 张</span>}</div>
      </article>)}</div>}
    </section>
  </div>;
}
