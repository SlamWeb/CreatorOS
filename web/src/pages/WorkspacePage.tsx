import { useEffect, useMemo, useRef, useState } from "react";
import { Navigate, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, studioApi } from "../api/client";
import type { ProducerSkillItem, TopicView } from "../api/types";
import { ErrorState, LoadingState } from "../components/PageState";
import { useAgentChat } from "../features/agent";
import { SkillInspector } from "../components/SkillInspector";
import { CreatorTaskSummary } from "../components/CreatorTaskSummary";
import { AccountAnalytics } from "../components/AccountAnalytics";
import { MediaCollections } from "../components/MediaCollections";
import { BgmPool, QA_BGM_CREATOR } from "../components/BgmPool";
import { CreatorSkillLibrary } from "../components/SkillLibrary";
import { useWorkInbox } from "../components/TaskInbox";
import { AccountNavigator } from "../components/AccountNavigator";
import { AccountOverview } from "../components/AccountOverview";
import { WorkspaceDialog } from "../components/WorkspaceDialog";
import { SeriesWorkspace } from "../features/series/SeriesWorkspace";
import { useSeriesOperationNavigation } from "../features/series/useSeriesOperationNavigation";
import { useWorkspaceNavigation } from "../features/workspace/useWorkspaceNavigation";
import { useCreatorDirectory } from "../api/useCreatorDirectory";
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
  const creators = useCreatorDirectory();
  const seriesAll = useQuery({ queryKey: ["series-all"], queryFn: studioApi.seriesAll,
    refetchInterval: q => q.state.data?.some(s => ["queued", "producing", "validating"].includes(s.latest_run_status ?? "")) ? 3000 : false });
  const [routeParams] = useSearchParams();
  const routeSeries = seriesAll.data?.find(item => item.id === routeParams.get("series"));
  const routeCreatorId = routeParams.get("creator") ?? routeSeries?.creator_id
    ?? (!routeParams.has("series") ? creators.items[0]?.id ?? null : null);
  const targetCreator = useQuery({ queryKey: ["creator", routeCreatorId], queryFn: () => studioApi.creator(routeCreatorId!),
    enabled: !!routeCreatorId, staleTime: 5_000, retry: false });
  const accountVerified = !routeCreatorId || (targetCreator.isSuccess && targetCreator.data.id === routeCreatorId);
  const accountMissing = !!routeCreatorId && targetCreator.isError && targetCreator.error instanceof ApiError && targetCreator.error.status === 404;
  const accounts = useMemo(() => {
    if (!routeCreatorId) return creators.items;
    const target = accountVerified && targetCreator.data?.id === routeCreatorId ? targetCreator.data : undefined;
    const others = creators.items.filter(item => item.id !== routeCreatorId);
    if (!target) return others;
    return creators.items.some(item => item.id === routeCreatorId)
      ? creators.items.map(item => item.id === routeCreatorId ? target : item)
      : [...others, target];
  }, [creators.items, routeCreatorId, targetCreator.data, accountVerified]);
  const skills = useQuery({ queryKey: ["producer-skills"], queryFn: studioApi.producerSkills });
  const [accountDraft, setAccountDraft] = useState<{ name: string; handle: string } | null>(null);
  const [seriesDraft, setSeriesDraft] = useState<{ name: string; creatorId: string | null } | null>(null);
  const chat = useAgentChat();
  const [inspectedSkill, setInspectedSkill] = useState<{
    skill: ProducerSkillItem; creatorId?: string; seriesId?: string; accountName?: string;
  } | null>(null);
  const viewedSkill = useRef<string | null>(null);
  const { params, setParams, scopedCreatorId, account, series, accountSeries,
    confirmingAccount, setConfirmingAccount, selectAccount, selectSeries, completeAssignment, accountDeleted, seriesDeleted } =
    useWorkspaceNavigation(creators.data ? accounts : undefined, seriesAll.data, accountVerified);
  useSeriesOperationNavigation(completeAssignment, seriesDeleted);
  useEffect(() => {
    if (!inspectedSkill) { viewedSkill.current = null; return; }
    if (inbox.data && viewedSkill.current !== inspectedSkill.skill.id) {
      viewedSkill.current = inspectedSkill.skill.id;
      inbox.markObjectSeen("skill", inspectedSkill.skill.id);
    }
  }, [inspectedSkill?.skill.id, inbox.data]);
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

  const workspaceLoading = creators.isPending || seriesAll.isPending || (!!routeCreatorId && targetCreator.isPending);
  const workspaceError = (creators.isError && !creators.data) || seriesAll.isError
    ? { message: (creators.error ?? seriesAll.error)?.message ?? "读取失败", retry: () => void Promise.all([creators.refetch(), seriesAll.refetch()]) }
    : routeCreatorId && targetCreator.isError && (!(targetCreator.error instanceof ApiError) || targetCreator.error.status !== 404)
      ? { message: `读取当前账号失败：${targetCreator.error.message}`, retry: () => void targetCreator.refetch() }
      : routeCreatorId && targetCreator.isSuccess && !accountVerified
        ? { message: "读取当前账号失败：返回账号与请求不一致。", retry: () => void targetCreator.refetch() }
        : null;
  if (workspaceLoading && !inspectedSkill) return <LoadingState label="正在读取…" />;
  if (workspaceError && !inspectedSkill) return <ErrorState message={workspaceError.message} onRetry={workspaceError.retry} />;

  const allSeries = seriesAll.data ?? [];
  const seriesId = series?.id ?? null;
  const requestedView = params.get("view") ?? "content";
  const shownView = !["content", "data", "materials", "skills", "bgm"].includes(requestedView) || requestedView === "bgm" && account?.id !== QA_BGM_CREATOR ? "content" : requestedView;
  const scopeUnavailable = !!inspectedSkill?.creatorId && (workspaceLoading || !!workspaceError || creators.isError
    || !accountVerified || account?.id !== inspectedSkill.creatorId);
  const inspectSkill = (skill: ProducerSkillItem) => setInspectedSkill({ skill, creatorId: account?.id,
    seriesId: shownView === "content" ? series?.id : undefined, accountName: account?.display_name });

  const discuss = (title: string, topic?: TopicView) => {
    chat.open({ creatorId: account?.id ?? null, accountName: account?.display_name, draftSeed: `我们讨论一下选题「${title}」。`,
      ...(topic ? { contextTarget: { series_id: topic.series_id, topic_id: topic.id,
        ...(topic.existing_run_id ? { run_id: topic.existing_run_id } : {}) }, contextTitle: title } : {}) });
  };
  const beginNewAccount = () => { createAccount.reset(); setAccountDraft({ name: "", handle: "" }); };
  const beginNewSeries = () => {
    if (routeCreatorId && !accountVerified) return;
    if (!accounts.length) { beginNewAccount(); return; }
    createSeries.reset();
    setSeriesDraft({ name: "", creatorId: scopedCreatorId });
  };

  return <div className={`workspace${chat.isOpen ? " chat-open" : ""}`}>
    {workspaceLoading ? <LoadingState label="正在读取…" /> : workspaceError
      ? <ErrorState message={workspaceError.message} onRetry={workspaceError.retry} /> : <>
    <div className="workspace-main">
      <AccountNavigator accounts={accounts} account={account} series={accountSeries} selectedSeriesId={seriesId}
        accountsLoading={creators.isFetching} accountsIncomplete={creators.isError} onRetryAccounts={() => void creators.refetch()}
        unassigned={allSeries.filter(item => item.creator_id === null)} onSelectAccount={selectAccount} onSelectSeries={id => selectSeries(id)}
        onOverview={() => account ? selectAccount(account.id, true) : accountSeries[0] && selectSeries(accountSeries[0].id)}
        onCreateAccount={beginNewAccount} onCreateSeries={beginNewSeries} createSeriesDisabled={!!routeCreatorId && !accountVerified}
        hasUnseen={id => inbox.hasUnseen("series", id)} confirmingDelete={confirmingAccount === account?.id}
        onConfirmDelete={() => setConfirmingAccount(account?.id ?? null)} onDelete={() => account && deleteAccount.mutate(account.id)} deletePending={deleteAccount.isPending}
        shownView={shownView} showBgm={account?.id === QA_BGM_CREATOR}
        onViewChange={view => setParams(previous => { const next = new URLSearchParams(previous); if (view === "content") next.delete("view"); else next.set("view", view); return next; })} />
      {deleteAccount.isError && <p className="form-error" role="alert">{deleteAccount.error.message}</p>}
      {params.has("series") && !series && <p className="form-error" role="alert">栏目不存在或不属于当前账号，请重新选择栏目。</p>}
      {(accountMissing || params.has("creator") && !account) && <p className="form-error" role="alert">账号不存在，请从账号切换中重新选择。</p>}
      {account && !series && shownView === "content" && <CreatorTaskSummary key={`tasks:${account.id}`} creatorId={account.id} />}
      {account && shownView === "bgm" && account.id === QA_BGM_CREATOR ? <BgmPool key={account.id} creatorId={account.id} /> :
        account && shownView === "skills" ? <CreatorSkillLibrary key={account.id} creatorId={account.id} onInspect={inspectSkill} /> :
        account && shownView === "materials" ? <MediaCollections key={account.id} creatorId={account.id} /> :
        account && shownView === "data" ? <AccountAnalytics key={account.id} creatorId={account.id} /> :
        !series && !accountMissing && (!params.has("creator") || account) ? <AccountOverview key={account?.id ?? "unassigned"} creatorId={account?.id ?? null} series={accountSeries} onSelect={id => selectSeries(id)} hasUnseen={id => inbox.hasUnseen("series", id)} onCreate={beginNewSeries} /> : null}
      {series && shownView === "content" && (!routeCreatorId || account) && <SeriesWorkspace key={series.id} series={series}
        accounts={accounts.map(a => ({ id: a.id, name: a.display_name }))}
        skills={skills.data?.items ?? []} onInspectSkill={inspectSkill} onDiscuss={discuss} />}
    </div>
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
    </>}
    {inspectedSkill && <SkillInspector key={`${inspectedSkill.creatorId ?? "library"}:${inspectedSkill.skill.id}`} skill={inspectedSkill.skill} onClose={() => setInspectedSkill(null)}
      editContext={{ creatorId: inspectedSkill.creatorId, seriesId: inspectedSkill.seriesId }} scopeUnavailable={scopeUnavailable}
      onScopeRetry={routeCreatorId === inspectedSkill.creatorId ? () => void targetCreator.refetch() : undefined}
      onAgentEdit={!scopeUnavailable && inspectedSkill.creatorId ? prompt => {
        setInspectedSkill(null); chat.open({ creatorId: inspectedSkill.creatorId!, accountName: inspectedSkill.accountName, draftSeed: prompt });
      } : undefined} />}
  </div>;
}
