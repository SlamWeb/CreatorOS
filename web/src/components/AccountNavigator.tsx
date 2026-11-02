import { useEffect, useState } from "react";
import { Check, ChevronsUpDown, Folder, LayoutGrid, Plus, Search, Trash2 } from "lucide-react";
import type { CreatorView, SeriesView } from "../api/types";
import { UnseenDot } from "./TaskInbox";
import { WorkspaceDialog } from "./WorkspaceDialog";
import "./account-navigator.css";

export function AccountNavigator({ accounts, account, series, selectedSeriesId, unassigned, onSelectAccount, onSelectSeries,
  onOverview, onCreateAccount, onCreateSeries, hasUnseen, confirmingDelete, onConfirmDelete, onDelete, deletePending }: {
  accounts: CreatorView[]; account?: CreatorView; series: SeriesView[]; selectedSeriesId: string | null; unassigned: SeriesView[];
  onSelectAccount: (id: string) => void; onSelectSeries: (id: string) => void; onOverview: () => void;
  onCreateAccount: () => void; onCreateSeries: () => void; hasUnseen: (id: string) => boolean;
  confirmingDelete: boolean; onConfirmDelete: () => void; onDelete: () => void; deletePending: boolean;
}) {
  const [switching, setSwitching] = useState(false);
  const [search, setSearch] = useState("");
  const [seriesSearch, setSeriesSearch] = useState("");
  useEffect(() => { setSeriesSearch(""); }, [account?.id, series[0]?.creator_id]);
  const matchingAccounts = accounts.filter(item => item.display_name.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  const matchingSeries = series.filter(item => item.name.toLocaleLowerCase().includes(seriesSearch.trim().toLocaleLowerCase()));
  const title = account?.display_name ?? (unassigned.length && series.some(item => item.creator_id === null) ? "未分配栏目" : "选择账号");
  return <>
    <div className="account-nav-switch-row">
      <button type="button" className="account-nav-switch" aria-label="切换账号" aria-haspopup="dialog" aria-expanded={switching}
        onClick={() => { setSearch(""); setSwitching(true); }}>
        <span className="account-nav-avatar" aria-hidden="true">{account?.display_name.slice(0, 1) ?? <LayoutGrid size={16} />}</span>
        <span className="account-nav-name" title={title}>{title}</span><ChevronsUpDown size={15} aria-hidden="true" />
      </button>
    </div>
    <button type="button" className={`account-nav-overview${!selectedSeriesId && account ? " is-current" : ""}`}
      aria-current={!selectedSeriesId && account ? "page" : undefined} onClick={onOverview}>
      <LayoutGrid size={16} aria-hidden="true" /><span>账号概览</span>
    </button>
    <div className="account-nav-section-head"><h2>栏目</h2>
      <button type="button" className="workspace-icon-button" aria-label="新建栏目" title="新建栏目" onClick={onCreateSeries}><Plus size={17} /></button>
    </div>
    {series.length > 7 && <label className="account-nav-search"><Search size={15} aria-hidden="true" />
      <input type="search" aria-label="搜索当前账号栏目" placeholder="搜索栏目" value={seriesSearch} onChange={event => setSeriesSearch(event.target.value)} />
    </label>}
    <nav className="account-nav-series" aria-label="当前账号的栏目">
      {matchingSeries.map(item => <button type="button" key={item.id} className={`rail-series${selectedSeriesId === item.id ? " active" : ""}`}
        aria-current={selectedSeriesId === item.id ? "page" : undefined} aria-label={item.name} onClick={() => onSelectSeries(item.id)}>
        <Folder size={15} aria-hidden="true" /><span title={item.name}>{item.name}</span>{hasUnseen(item.id) && <UnseenDot label="栏目有未查看内容" />}
      </button>)}
      {!matchingSeries.length && <p className="account-nav-empty">{seriesSearch ? "没有匹配的栏目" : "还没有栏目"}</p>}
    </nav>
    {switching && <WorkspaceDialog title="切换账号" className="account-switch-dialog" onClose={() => setSwitching(false)}>
      <label className="account-nav-search"><Search size={16} aria-hidden="true" />
        <input type="search" aria-label="搜索账号" placeholder="搜索账号" data-initial-focus value={search} onChange={event => setSearch(event.target.value)} />
      </label>
      <div className="account-switch-list">
        {matchingAccounts.map(item => <button type="button" key={item.id} aria-label={`切换到账号 ${item.display_name}`}
          aria-current={account?.id === item.id ? "true" : undefined} onClick={() => { onSelectAccount(item.id); setSwitching(false); }}>
          <span className="account-nav-avatar" aria-hidden="true">{item.display_name.slice(0, 1)}</span><span>{item.display_name}</span>
          {account?.id === item.id && <Check size={16} aria-hidden="true" />}
        </button>)}
        {!matchingAccounts.length && <p className="account-nav-empty">{accounts.length ? "没有匹配的账号" : "还没有账号"}</p>}
      </div>
      <footer className="account-switch-footer">
        {!!unassigned.length && <button type="button" onClick={() => { onSelectSeries(unassigned[0].id); setSwitching(false); }}><Folder size={16} />未分配栏目</button>}
        <button type="button" onClick={() => { setSwitching(false); requestAnimationFrame(onCreateAccount); }}><Plus size={16} />新建账号</button>
        {account && !series.length && <button type="button" className="action-danger" disabled={deletePending}
          onClick={() => confirmingDelete ? onDelete() : onConfirmDelete()}><Trash2 size={15} />{deletePending ? "删除中…" : confirmingDelete ? "确认删除账号" : "删除当前账号"}</button>}
      </footer>
    </WorkspaceDialog>}
  </>;
}
