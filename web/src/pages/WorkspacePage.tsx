import { useEffect, useRef, useState } from "react";
import { Navigate, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCreators } from "../api/hooks";
import { studioApi } from "../api/client";
import type { ProducerSkillItem, TopicView } from "../api/types";
import { ErrorState, LoadingState } from "../components/PageState";
import { useAgentChat } from "../features/agent";
import { SkillInspector } from "../components/SkillInspector";
import { CreatorTaskSummary } from "../components/CreatorTaskSummary";
import { AccountAnalytics } from "../components/AccountAnalytics";
import { MediaCollections } from "../components/MediaCollections";
import { CreatorSkillLibrary } from "../components/SkillLibrary";
import { useWorkInbox } from "../components/TaskInbox";
import { AccountNavigator } from "../components/AccountNavigator";
import { AccountOverview } from "../components/AccountOverview";
import { WorkspaceDialog } from "../components/WorkspaceDialog";
import { SeriesWorkspace } from "../features/series/SeriesWorkspace";
import { useWorkspaceNavigation } from "../features/workspace/useWorkspaceNavigation";
import { refreshWorkspace } from "../features/workspace/workspaceData";
import "./workspace.css";

export function WorkspaceRedirect() {
  const { seriesId } = useParams();
  const [params] = useSearchParams();
  const next = new URLSearchParams(params);
  if (seriesId) next.set("series", seriesId);
  return <Navigate to={`/?${next.toString()}`} replace />;
}

export function WorkspacePage() {
  const client = useQueryClient();
  const inbox = useWorkInbox();
  const creators = useCreators();
  const seriesAll = useQuery({ queryKey: ["series-all"], queryFn: studioApi.seriesAll,
    refetchInterval: q => q.state.data?.some(s => ["queued", "producing", "validating"].includes(s.latest_run_status ?? "")) ? 3000 : false });
  const skills = useQuery({ queryKey: ["producer-skills"], queryFn: studioApi.producerSkills });
  const [accountDraft, setAccountDraft] = useState<{ name: string; handle: string } | null>(null);
  const [seriesDraft, setSeriesDraft] = useState<{ name: string; creatorId: string | null } | null>(null);
  const chat = useAgentChat();
  const [inspectedSkill, setInspectedSkill] = useState<ProducerSkillItem | null>(null);
  const viewedSkill = useRef<string | null>(null);
  const { params, setParams, scopedCreatorId, account, series, accountSeries, treeOpen, setTreeOpen,
    confirmingAccount, setConfirmingAccount, selectAccount, selectSeries, completeAssignment, accountDeleted, seriesDeleted } =
    useWorkspaceNavigation(creators.data?.items, seriesAll.data);
  useEffect(() => {
    if (!inspectedSkill) { viewedSkill.current = null; return; }
    if (inbox.data && viewedSkill.current !== inspectedSkill.id) {
      viewedSkill.current = inspectedSkill.id;
      inbox.markObjectSeen("skill", inspectedSkill.id);
    }
  }, [inspectedSkill?.id, inbox.data]);
  const refresh = () => refreshWorkspace(client);

  const createAccount = useMutation({
    retry: false,
    mutationFn: (input: { name: string; handle: string }) => studioApi.createCreator({ display_name: input.name, account_handle: input.handle || undefined }),
    onSuccess: async result => {
      setAccountDraft(null);
      await refresh();
      selectAccount(result.id, true);
      if (chat.isOpen) chat.open({ creatorId: result.id, accountName: result.display_name });
    },
  });

  const deleteAccount = useMutation({
    retry: false,
    mutationFn: (id: string) => studioApi.deleteCreator(id),
    onSuccess: async (_result, id) => {
      await refresh();
      accountDeleted(id);
    },
    onError: () => setConfirmingAccount(null),
  });

  const createSeries = useMutation({
    retry: false,
    mutationFn: (input: { name: string; creatorId: string | null }) => studioApi.composeSeries(
      { name: input.name, description: "", audience: "", creator_id: input.creatorId, request_id: crypto.randomUUID().replaceAll("-", "") },
    ),
    onSuccess: async (result) => { setSeriesDraft(null); await refresh(); selectSeries(result.series.id, result.series.creator_id); },
  });

  if (creators.isPending || seriesAll.isPending) return <LoadingState label="正在读取…" />;
  if (creators.isError || seriesAll.isError) {
    const error = creators.error ?? seriesAll.error;
    return <ErrorState message={error?.message ?? "读取失败"} onRetry={() => void Promise.all([creators.refetch(), seriesAll.refetch()])} />;
  }

  const accounts = creators.data.items;
  const allSeries = seriesAll.data;
  const seriesId = series?.id ?? null;

  const discuss = (title: string, topic?: TopicView) => {
    chat.open({ creatorId: account?.id ?? null, accountName: account?.display_name, draftSeed: `我们讨论一下选题「${title}」。`,
      ...(topic ? { contextTarget: { series_id: topic.series_id, topic_id: topic.id,
        ...(topic.existing_run_id ? { run_id: topic.existing_run_id } : {}) }, contextTitle: title } : {}) });
  };
  const beginNewAccount = () => { createAccount.reset(); setAccountDraft({ name: "", handle: "" }); };
  const beginNewSeries = () => {
    if (!accounts.length) { beginNewAccount(); return; }
    createSeries.reset();
    setSeriesDraft({ name: "", creatorId: scopedCreatorId });
  };

  return <div className={`workspace${chat.isOpen ? " chat-open" : ""}`}>
    <button type="button" className="workspace-tree-toggle" aria-expanded={treeOpen} onClick={() => setTreeOpen(!treeOpen)}>账号与栏目 · {series?.name ?? account?.display_name ?? "选择账号"}</button>
    <aside className={`workspace-rail${treeOpen ? " is-open" : ""}`} aria-label="账号与栏目">
      <AccountNavigator accounts={accounts} account={account} series={accountSeries} selectedSeriesId={seriesId}
        unassigned={allSeries.filter(item => item.creator_id === null)}
        onSelectAccount={selectAccount} onSelectSeries={id => selectSeries(id)}
        onOverview={() => account ? selectAccount(account.id, true) : accountSeries[0] && selectSeries(accountSeries[0].id)}
        onCreateAccount={beginNewAccount} onCreateSeries={beginNewSeries}
        hasUnseen={id => inbox.hasUnseen("series", id)} confirmingDelete={confirmingAccount === account?.id}
        onConfirmDelete={() => setConfirmingAccount(account?.id ?? null)}
        onDelete={() => account && deleteAccount.mutate(account.id)} deletePending={deleteAccount.isPending} />
      {deleteAccount.isError && <p className="form-error" role="alert">{deleteAccount.error.message}</p>}
    </aside>
    <main className="workspace-main">
      {params.has("series") && !series && <p className="form-error" role="alert">栏目不存在或不属于当前账号，请从左侧重新选择。</p>}
      {params.has("creator") && !account && <p className="form-error" role="alert">账号不存在，请从左侧重新选择。</p>}
      {account && !series && !["data", "materials", "skills"].includes(params.get("view") ?? "") && <CreatorTaskSummary key={account.id} creatorId={account.id} />}
      {!series && (!params.has("creator") || account) && <>
      <header className="workspace-head"><div><h1>{account?.display_name ?? "账号与栏目"}</h1></div>
        {!!accountSeries.length && <button type="button" className="button button-secondary" onClick={beginNewSeries}>新建栏目</button>}
      </header>
      {account && <nav className="workspace-data-tabs" aria-label="账号工作区">{[{value:"content",label:"内容"},{value:"data",label:"数据"},{value:"materials",label:"素材"},{value:"skills",label:"Skill 库"}].map(tab => <button key={tab.value} type="button" aria-pressed={(["data","materials","skills"].includes(params.get("view") ?? "") ? params.get("view") : "content") === tab.value}
        onClick={() => setParams(previous => { const next = new URLSearchParams(previous); if(tab.value !== "content") next.set("view",tab.value); else next.delete("view"); return next; })}>{tab.label}</button>)}</nav>}
      {account && params.get("view") === "skills" ? <CreatorSkillLibrary key={account.id} creatorId={account.id} onInspect={setInspectedSkill} /> : account && params.get("view") === "materials" ? <MediaCollections key={account.id} creatorId={account.id} /> : account && params.get("view") === "data" ? <AccountAnalytics key={account.id} creatorId={account.id} /> : <>
      <AccountOverview key={account?.id ?? "unassigned"} creatorId={account?.id ?? null} series={accountSeries}
        onSelect={id => selectSeries(id)} hasUnseen={id => inbox.hasUnseen("series", id)}
        onCreate={beginNewSeries} />
      </>}
      </>}
      {series && <SeriesWorkspace key={series.id} series={series}
        accounts={accounts.map(a => ({ id: a.id, name: a.display_name }))}
        skills={skills.data?.items ?? []} onInspectSkill={setInspectedSkill}
        onDiscuss={discuss}
        onSelected={completeAssignment}
        onDeleted={seriesDeleted} />}
    </main>
    {accountDraft && <WorkspaceDialog title="新建账号" busy={createAccount.isPending} onClose={() => setAccountDraft(null)}>
      <form className="workspace-create-form" onSubmit={event => {
        event.preventDefault();
        if (accountDraft.name.trim() && !createAccount.isPending) createAccount.mutate({ name: accountDraft.name.trim(), handle: accountDraft.handle.trim() });
      }}>
        <label>账号名称<input required maxLength={120} data-initial-focus value={accountDraft.name} aria-label="新账号名称"
          disabled={createAccount.isPending} onChange={event => setAccountDraft({ ...accountDraft, name: event.target.value })} /></label>
        <label>账号标识（可选）<input maxLength={160} value={accountDraft.handle} aria-label="新账号标识"
          disabled={createAccount.isPending} onChange={event => setAccountDraft({ ...accountDraft, handle: event.target.value })} /></label>
        {createAccount.isError && <p className="form-error" role="alert">{createAccount.error.message}</p>}
        <div className="workspace-dialog-actions"><button className="button button-secondary" type="button" disabled={createAccount.isPending} onClick={() => setAccountDraft(null)}>取消</button>
          <button className="button button-primary" disabled={createAccount.isPending || !accountDraft.name.trim()}>{createAccount.isPending ? "创建中…" : "创建"}</button></div>
      </form>
    </WorkspaceDialog>}
    {seriesDraft && <WorkspaceDialog title="新建栏目" busy={createSeries.isPending} onClose={() => setSeriesDraft(null)}>
      <p className="workspace-create-owner">所属账号：<strong>{accounts.find(item => item.id === seriesDraft.creatorId)?.display_name ?? "未分配"}</strong></p>
      <form className="workspace-create-form" onSubmit={event => {
        event.preventDefault();
        if (seriesDraft.name.trim() && !createSeries.isPending) createSeries.mutate({ name: seriesDraft.name.trim(), creatorId: seriesDraft.creatorId });
      }}>
        <label>栏目名称<input required maxLength={120} data-initial-focus value={seriesDraft.name} aria-label="新栏目名称"
          disabled={createSeries.isPending} onChange={event => setSeriesDraft({ ...seriesDraft, name: event.target.value })} /></label>
        {createSeries.isError && <p className="form-error" role="alert">{createSeries.error.message}</p>}
        <div className="workspace-dialog-actions"><button className="button button-secondary" type="button" disabled={createSeries.isPending} onClick={() => setSeriesDraft(null)}>取消</button>
          <button className="button button-primary" disabled={createSeries.isPending || !seriesDraft.name.trim()}>{createSeries.isPending ? "创建中…" : "创建"}</button></div>
      </form>
    </WorkspaceDialog>}
    {inspectedSkill && <SkillInspector skill={inspectedSkill} onClose={() => setInspectedSkill(null)}
      editContext={{ creatorId: account?.id, seriesId: series?.id }}
      onAgentEdit={account ? prompt => {
        setInspectedSkill(null); chat.open({ creatorId: account.id, accountName: account.display_name, draftSeed: prompt });
      } : undefined} />}
  </div>;
}
