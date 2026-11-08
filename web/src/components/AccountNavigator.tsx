import { useEffect, useMemo, useState } from "react";
import { useQueries, useQuery } from "@tanstack/react-query";
import { Check, ChevronsUpDown, GalleryVerticalEnd, LayoutGrid, Plus, Search, Trash2 } from "lucide-react";
import { apiUrl, request, studioApi } from "../api/client";
import type { CreatorDirectoryItem, PageResponse, SeriesView, TopicView } from "../api/types";
import { UnseenDot } from "./TaskInbox";
import { WorkspaceDialog } from "./WorkspaceDialog";
import "./account-navigator.css";

export function AccountNavigator({ accounts, accountsLoading, accountsIncomplete, onRetryAccounts, account, series, selectedSeriesId, unassigned, onSelectAccount, onSelectSeries,
  onCreateAccount, onCreateSeries, createSeriesDisabled, hasUnseen, confirmingDelete, onConfirmDelete, onDelete, deletePending,
  shownView, onViewChange, showBgm }: {
  accounts: CreatorDirectoryItem[]; accountsLoading: boolean; accountsIncomplete: boolean; onRetryAccounts: () => void;
  account?: CreatorDirectoryItem; series: SeriesView[]; selectedSeriesId: string | null; unassigned: SeriesView[];
  onSelectAccount: (id: string) => void; onSelectSeries: (id: string) => void;
  onCreateAccount: () => void; onCreateSeries: () => void; createSeriesDisabled: boolean; hasUnseen: (id: string) => boolean;
  confirmingDelete: boolean; onConfirmDelete: () => void; onDelete: () => void; deletePending: boolean;
  shownView: string; onViewChange: (view: string) => void; showBgm: boolean;
}) {
  const [switching, setSwitching] = useState(false);
  const [search, setSearch] = useState("");
  const [seriesSearch, setSeriesSearch] = useState("");
  const recent = useQuery({ queryKey: ["runs", "account-overview", account?.id],
    queryFn: () => studioApi.runsPage(0, account!.id), enabled: !!account, staleTime: 60_000, retry: false });
  const covers = useMemo(() => {
    const bySeries = new Map<string, string>();
    for (const run of [...(recent.data?.items ?? [])].sort((a, b) => b.updated_at.localeCompare(a.updated_at))) {
      if (run.creator_id === account?.id && run.cover_url && !bySeries.has(run.series_id)) bySeries.set(run.series_id, run.cover_url);
    }
    return bySeries;
  }, [account?.id, recent.data]);
  // Recent account runs may all belong to one busy series. Fetch a bounded
  // fallback only for visible series that are missing a recent cover.
  const missing = series.filter(item => !covers.has(item.id)).slice(0, 8);
  const fallbackCovers = useQueries({ queries: missing.map(item => ({
    queryKey: ["series-cover", item.id],
    queryFn: () => request<PageResponse<TopicView>>(`/api/series/${encodeURIComponent(item.id)}/topic-library?state=all&offset=0&limit=20&order=created`),
    enabled: !!account && !recent.isPending, staleTime: 60_000, retry: false,
  })) });
  const displayCovers = new Map(covers);
  missing.forEach((item, index) => {
    const cover = fallbackCovers[index]?.data?.items.find(topic => topic.cover_url)?.cover_url;
    if (cover) displayCovers.set(item.id, cover);
  });
  useEffect(() => { setSeriesSearch(""); }, [account?.id, series[0]?.creator_id]);
  const matchingAccounts = accounts.filter(item => item.display_name.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  const matchingSeries = series.filter(item => item.name.toLocaleLowerCase().includes(seriesSearch.trim().toLocaleLowerCase()));
  const title = account?.display_name ?? (unassigned.length && series.some(item => item.creator_id === null) ? "未分配栏目" : "选择账号");
  const views = [{ value: "data", label: "数据" },
    ...(showBgm ? [{ value: "bgm", label: "BGM" }] : []), { value: "skills", label: "可用 Skill" }];
  const TitleTag = selectedSeriesId && ["content", "materials"].includes(shownView) ? "div" : "h1";
  return <>
    <div className="account-studio-head">
      <TitleTag className="account-studio-title" aria-label={title}><button type="button" className="account-nav-switch" aria-label="切换账号" aria-haspopup="dialog" aria-expanded={switching}
        onClick={() => { setSearch(""); setSwitching(true); }}>
        <span className="account-nav-avatar" aria-hidden="true">{account?.display_name.slice(0, 1) ?? <LayoutGrid size={16} />}</span>
        <span className="account-nav-name" title={title}>{title}</span><ChevronsUpDown size={16} aria-hidden="true" />
      </button></TitleTag>
      {account && <nav className="account-resource-actions" aria-label="账号工作区">{views.map(view =>
        <button key={view.value} type="button" aria-pressed={shownView === view.value} onClick={() => onViewChange(view.value)}>{view.label}</button>)}</nav>}
    </div>
    {(account || series.length > 0) && <div className="account-series-navigation">
      <nav className="account-nav-series" aria-label="当前账号的栏目">
        {matchingSeries.map(item => <button type="button" key={item.id} className={`rail-series${selectedSeriesId === item.id && ["content", "materials"].includes(shownView) ? " active" : ""}`}
          aria-current={selectedSeriesId === item.id && ["content", "materials"].includes(shownView) ? "page" : undefined} aria-label={item.name} onClick={() => onSelectSeries(item.id)}>
          <span className="account-series-thumb" aria-hidden="true"><GalleryVerticalEnd size={18} />{displayCovers.has(item.id) && <img src={apiUrl(displayCovers.get(item.id)!)} alt="" width={48} height={48} loading="lazy" onError={event => { event.currentTarget.hidden = true; }} />}</span>
          <span className="account-series-name" title={item.name}>{item.name}</span>{hasUnseen(item.id) && <UnseenDot label="栏目有未查看内容" />}
        </button>)}
        <button type="button" className="account-series-create" aria-label="新建栏目" disabled={createSeriesDisabled} onClick={onCreateSeries}><Plus size={15} aria-hidden="true" />新建栏目</button>
      </nav>
      {series.length > 7 && <label className="account-nav-search account-series-search"><Search size={15} aria-hidden="true" />
        <input type="search" aria-label="搜索当前账号栏目" placeholder="搜索栏目…" value={seriesSearch} onChange={event => setSeriesSearch(event.target.value)} />
      </label>}
      {!matchingSeries.length && seriesSearch && <p className="account-nav-empty">没有匹配的栏目</p>}
    </div>}
    {switching && <WorkspaceDialog title="切换账号" className="account-switch-dialog" onClose={() => setSwitching(false)}>
      <label className="account-nav-search"><Search size={16} aria-hidden="true" />
        <input type="search" aria-label="搜索账号" placeholder="搜索账号…" data-initial-focus value={search} onChange={event => setSearch(event.target.value)} />
      </label>
      <div className="account-switch-list">
        {matchingAccounts.map(item => <button type="button" key={item.id} aria-label={`切换到账号 ${item.display_name}`}
          aria-current={account?.id === item.id ? "true" : undefined} onClick={() => { onSelectAccount(item.id); setSwitching(false); }}>
          <span className="account-nav-avatar" aria-hidden="true">{item.display_name.slice(0, 1)}</span><span>{item.display_name}</span>
          {account?.id === item.id && <Check size={16} aria-hidden="true" />}
        </button>)}
        {accountsLoading && <p className="account-nav-empty" role="status">正在更新账号列表…</p>}
        {accountsIncomplete && <div className="account-nav-empty"><p>账号列表读取失败</p><button type="button" onClick={onRetryAccounts}>重试加载账号</button></div>}
        {!matchingAccounts.length && !accountsLoading && !accountsIncomplete && <p className="account-nav-empty">{accounts.length ? "没有匹配的账号" : "还没有账号"}</p>}
      </div>
      <footer className="account-switch-footer">
        {!!unassigned.length && <button type="button" onClick={() => { onSelectSeries(unassigned[0].id); setSwitching(false); }}><GalleryVerticalEnd size={16} aria-hidden="true" />未分配栏目</button>}
        <button type="button" onClick={() => { setSwitching(false); requestAnimationFrame(onCreateAccount); }}><Plus size={16} aria-hidden="true" />新建账号</button>
        {account && !series.length && <button type="button" className="action-danger" disabled={deletePending}
          onClick={() => confirmingDelete ? onDelete() : onConfirmDelete()}><Trash2 size={15} aria-hidden="true" />{deletePending ? "删除中…" : confirmingDelete ? "确认删除账号" : "删除当前账号"}</button>}
      </footer>
    </WorkspaceDialog>}
  </>;
}
