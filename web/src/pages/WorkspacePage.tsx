import { useEffect, useId, useRef, useState } from "react";
import { Plus, Settings2 } from "lucide-react";
import { Link, Navigate, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCreators } from "../api/hooks";
import { request, studioApi } from "../api/client";
import type { ProducerSkillItem, SeriesView, TopicView, SkillLibraryView } from "../api/types";
import { ErrorState, LoadingState } from "../components/PageState";
import { TopicLibrary } from "../components/TopicLibrary";
import { useAgentChat } from "../components/AgentChatHost";
import { SkillInspector } from "../components/SkillInspector";
import { SkillReferenceButton } from "../components/SkillReferenceButton";
import { CreatorTaskSummary } from "../components/CreatorTaskSummary";
import { AccountAnalytics } from "../components/AccountAnalytics";
import { MediaCollections } from "../components/MediaCollections";
import { BgmPool, QA_BGM_CREATOR } from "../components/BgmPool";
import { CreatorSkillLibrary, SeriesSkillPool } from "../components/SkillLibrary";
import { useWorkInbox } from "../components/TaskInbox";
import { AccountNavigator } from "../components/AccountNavigator";
import { AccountOverview } from "../components/AccountOverview";
import { WorkspaceDialog } from "../components/WorkspaceDialog";
import "./workspace.css";

export function WorkspaceRedirect() {
  const { seriesId } = useParams();
  const [params] = useSearchParams();
  const next = new URLSearchParams(params);
  if (seriesId) next.set("series", seriesId);
  return <Navigate to={`/?${next.toString()}`} replace />;
}

export function WorkspacePage() {
  const [params, setParams] = useSearchParams();
  const client = useQueryClient();
  const inbox = useWorkInbox();
  const visitedSeries = useRef<string | null>(null);
  const creators = useCreators();
  const seriesAll = useQuery({ queryKey: ["series-all"], queryFn: studioApi.seriesAll,
    refetchInterval: q => q.state.data?.some(s => ["queued", "producing", "validating"].includes(s.latest_run_status ?? "")) ? 3000 : false });
  const skills = useQuery({ queryKey: ["producer-skills"], queryFn: studioApi.producerSkills });
  const [accountDraft, setAccountDraft] = useState<{ name: string; handle: string } | null>(null);
  const [seriesDraft, setSeriesDraft] = useState<{ name: string; creatorId: string | null } | null>(null);
  const [confirmingAccount, setConfirmingAccount] = useState<string | null>(null);
  const [treeOpen, setTreeOpen] = useState(false);
  const chat = useAgentChat();
  const [inspectedSkill, setInspectedSkill] = useState<ProducerSkillItem | null>(null);
  const viewedSkill = useRef<string | null>(null);
  useEffect(() => {
    const id = params.get("series");
    if (!id) { visitedSeries.current = null; return; }
    const selected = seriesAll.data?.find(item => item.id === id);
    if (!selected || !inbox.data || (params.get("creator") && params.get("creator") !== selected.creator_id)
        || visitedSeries.current === id) return;
    visitedSeries.current = id;
    inbox.markObjectSeen("series", id);
  }, [params, seriesAll.data, inbox.data]);
  useEffect(() => {
    if (!inspectedSkill) { viewedSkill.current = null; return; }
    if (inbox.data && viewedSkill.current !== inspectedSkill.id) {
      viewedSkill.current = inspectedSkill.id;
      inbox.markObjectSeen("skill", inspectedSkill.id);
    }
  }, [inspectedSkill?.id, inbox.data]);
  useEffect(() => {
    const items = creators.data?.items ?? [];
    const selectedSeries = seriesAll.data?.find(item => item.id === params.get("series"));
    const id = params.get("creator") ?? selectedSeries?.creator_id ?? (params.has("series") ? null : items[0]?.id ?? null);
    const current = items.find(item => item.id === id);
    if (!current) return;
    if (params.has("skill_edit")) chat.open({ creatorId: current.id, accountName: current.display_name });
    else chat.setDefault({ creatorId: current.id, accountName: current.display_name });
  }, [params, creators.data, seriesAll.data, chat.isOpen, chat.open, chat.setDefault]);

  const refresh = async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: ["series-all"] }),
      client.invalidateQueries({ queryKey: ["creators"] }),
      client.invalidateQueries({ queryKey: ["overview"] }),
    ]);
  };

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
      setConfirmingAccount(null);
      await refresh();
      setParams(previous => {
        if (previous.get("creator") !== id) return previous;
        const next = new URLSearchParams(previous);
        for (const key of ["creator", "series", "view", "topic", "collection", "collection_topic"]) next.delete(key);
        return next;
      });
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

  const assign = useMutation({
    retry: false,
    mutationFn: (input: { series: SeriesView; creatorId: string | null }) => studioApi.assignSeries(input.series.id, {
      creator_id: input.creatorId, expected_revision: input.series.revision,
      request_id: crypto.randomUUID().replaceAll("-", ""),
    }),
    onSuccess: async (result) => { await refresh(); selectSeries(result.series.id, result.series.creator_id); },
    onError: refresh,
  });

  const deleteSeries = useMutation({
    retry: false,
    mutationFn: (item: SeriesView) => studioApi.deleteSeries(item.id, {
      expected_revision: item.revision, request_id: crypto.randomUUID().replaceAll("-", ""),
    }),
    onSuccess: async result => {
      await refresh();
      setParams(previous => {
        if (previous.get("series") !== result.id) return previous;
        const next = new URLSearchParams(previous);
        next.delete("series");
        for (const key of ["topics", "offset", "select", "research", "operation", "topic"]) next.delete(key);
        return next;
      });
    },
  });

  if (creators.isPending || seriesAll.isPending) return <LoadingState label="正在读取…" />;
  if (creators.isError || seriesAll.isError) {
    const error = creators.error ?? seriesAll.error;
    return <ErrorState message={error?.message ?? "读取失败"} onRetry={() => void Promise.all([creators.refetch(), seriesAll.refetch()])} />;
  }

  const accounts = creators.data.items;
  const allSeries = seriesAll.data;
  const requestedSeries = allSeries.find(s => s.id === params.get("series")) ?? null;
  const scopedCreatorId = params.get("creator") ?? requestedSeries?.creator_id ?? (params.has("series") ? null : accounts[0]?.id ?? null);
  const account = accounts.find(a => a.id === scopedCreatorId);
  const series = requestedSeries && (!params.get("creator") || requestedSeries.creator_id === scopedCreatorId) ? requestedSeries : null;
  const seriesId = series?.id ?? null;
  const accountSeries = allSeries.filter(s => s.creator_id === scopedCreatorId);

  function selectSeries(id: string, owner?: string | null, keepFilter = false) {
    setTreeOpen(false);
    inbox.markObjectSeen("series", id);
    const target = allSeries.find(item => item.id === id);
    const creator = accounts.find(item => item.id === (owner === undefined ? target?.creator_id : owner));
    if (chat.isOpen && creator) chat.open({ creatorId: creator.id, accountName: creator.display_name });
    setParams(previous => {
      const next = new URLSearchParams(previous);
      next.set("series", id);
      const selected = allSeries.find(item => item.id === id);
      const creatorId = owner === undefined ? selected?.creator_id : owner;
      if (creatorId) next.set("creator", creatorId);
      else next.delete("creator");
      for (const key of ["offset", "select", "research", "operation", "topic", "view", "collection", "collection_topic"]) next.delete(key);
      if (!keepFilter) next.delete("topics");
      return next;
    });
  }

  function selectAccount(id: string, resetView = false) {
    setTreeOpen(false);
    setConfirmingAccount(null);
    const selected = accounts.find(item => item.id === id);
    if (chat.isOpen && selected) chat.open({ creatorId: selected.id, accountName: selected.display_name });
    setParams(previous => {
      const next = new URLSearchParams(previous); next.set("creator", id);
      if (resetView) next.delete("view");
      if (id !== QA_BGM_CREATOR && next.get("view") === "bgm") next.delete("view");
      for (const key of ["series", "topics", "offset", "select", "research", "operation", "topic", "chat", "collection", "collection_topic"]) next.delete(key);
      return next;
    });
  }
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
      {account && !series && !["data", "materials", "skills", "bgm"].includes(params.get("view") ?? "") && <CreatorTaskSummary key={account.id} creatorId={account.id} />}
      {!series && (!params.has("creator") || account) && <>
      <header className="workspace-head"><div><h1>{account?.display_name ?? "账号与栏目"}</h1></div>
        {!!accountSeries.length && <button type="button" className="button button-secondary" onClick={beginNewSeries}>新建栏目</button>}
      </header>
      {account && <nav className="workspace-data-tabs" aria-label="账号工作区">{[{value:"content",label:"内容"},{value:"data",label:"数据"},{value:"materials",label:"素材"},...(account.id === QA_BGM_CREATOR ? [{value:"bgm",label:"BGM"}] : []),{value:"skills",label:"Skill 库"}].map(tab => <button key={tab.value} type="button" aria-pressed={(["data","materials","skills","bgm"].includes(params.get("view") ?? "") ? params.get("view") : "content") === tab.value}
        onClick={() => setParams(previous => { const next = new URLSearchParams(previous); if(tab.value !== "content") next.set("view",tab.value); else next.delete("view"); return next; })}>{tab.label}</button>)}</nav>}
      {account && params.get("view") === "bgm" && account.id === QA_BGM_CREATOR ? <BgmPool key={account.id} creatorId={account.id} /> : account && params.get("view") === "skills" ? <CreatorSkillLibrary key={account.id} creatorId={account.id} onInspect={setInspectedSkill} /> : account && params.get("view") === "materials" ? <MediaCollections key={account.id} creatorId={account.id} /> : account && params.get("view") === "data" ? <AccountAnalytics key={account.id} creatorId={account.id} /> : <>
      <AccountOverview key={account?.id ?? "unassigned"} creatorId={account?.id ?? null} series={accountSeries}
        onSelect={id => selectSeries(id)} hasUnseen={id => inbox.hasUnseen("series", id)}
        onCreate={beginNewSeries} />
      </>}
      </>}
      {series && <SeriesWorkspace key={series.id} series={series}
        accounts={accounts.map(a => ({ id: a.id, name: a.display_name }))}
        skills={skills.data?.items ?? []} onInspectSkill={setInspectedSkill}
        onDiscuss={discuss}
        onAssign={(creatorId) => assign.mutate({ series, creatorId })}
        assignPending={assign.isPending && assign.variables?.series.id === series.id}
        assignError={assign.variables?.series.id === series.id ? assign.error?.message ?? null : null}
        onDelete={() => {
          if (series.topic_count > 0 && !window.confirm("从工作区移除，保留选题、图片与记录")) return;
          deleteSeries.mutate(series);
        }} deletePending={deleteSeries.isPending}
        deleteError={deleteSeries.variables?.id === series.id ? deleteSeries.error?.message ?? null : null} />}
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

function SeriesWorkspace({ series, accounts, skills, onInspectSkill, onAssign, assignPending = false, assignError, onDiscuss, onDelete, deletePending = false, deleteError }: {
  series: SeriesView;
  accounts: { id: string; name: string }[];
  skills: ProducerSkillItem[];
  onInspectSkill: (skill: ProducerSkillItem) => void;
  onAssign: (creatorId: string | null) => void;
  assignPending?: boolean; assignError?: string | null;
  onDiscuss?: (title: string, topic?: TopicView) => void;
  onDelete?: () => void; deletePending?: boolean; deleteError?: string | null;
}) {
  const client = useQueryClient();
  const [topicTitle, setTopicTitle] = useState("");
  const skillPool = useQuery({ queryKey: ["series-skills", series.id], queryFn: () => request<SkillLibraryView>(`/api/series/${encodeURIComponent(series.id)}/skills`), enabled: !!series.creator_id, retry: false });
  const poolItems = skillPool.data?.items ?? [];
  const [addOpen, setAddOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [nameDraft, setNameDraft] = useState<string | null>(null);
  const addId = useId();
  const settingsId = useId();
  const titleInput = useRef<HTMLInputElement>(null);
  const addTrigger = useRef<HTMLButtonElement>(null);
  useEffect(() => { if (addOpen) titleInput.current?.focus(); }, [addOpen]);
  const rename = useMutation({
    retry: false,
    mutationFn: (input: { id: string; name: string; expected_revision: number }) => studioApi.renameSeries(input.id, {
      name: input.name, expected_revision: input.expected_revision, request_id: crypto.randomUUID().replaceAll("-", ""),
    }),
    onSuccess: async () => {
      setNameDraft(null);
      await Promise.all(["series-all", "series", "creators", "overview"].map(key => client.invalidateQueries({ queryKey: [key] })));
    },
    onError: () => client.invalidateQueries({ queryKey: ["series-all"] }),
  });
  useEffect(() => { setNameDraft(null); rename.reset(); }, [series.id]);
  const runMutation = useMutation({
    retry: false,
    mutationFn: async (topic: TopicView) => {
      const run = topic.existing_run_id ? { id: topic.existing_run_id, version: topic.existing_run_version! } : await studioApi.startRun({ topic_id: topic.id });
      return studioApi.executeRun(run.id, run.version);
    },
    onSettled: async () => {
      await Promise.all(["topics", "series", "overview", "runs", "series-all"].map(key => client.invalidateQueries({ queryKey: key === "topics" || key === "series" ? [key, series.id] : [key] })));
    },
  });
  const addTopic = useMutation({
    retry: false,
    mutationFn: (title: string) => studioApi.queueTopics(series.id, [{ title }]),
    onSuccess: async () => {
      setTopicTitle("");
      setAddOpen(false);
      addTrigger.current?.focus();
      await Promise.all([["topics"], ["series", series.id], ["series-all"]].map(key => client.invalidateQueries({ queryKey: key })));
    },
  });
  const startButton = (topic: TopicView) => <button type="button" className="topic-run-button"
    disabled={!series.is_active || !series.creator_id || runMutation.isPending}
    title={!series.creator_id ? "先分配账号" : undefined}
    onClick={() => runMutation.mutate(topic)}>{runMutation.isPending && runMutation.variables?.id === topic.id ? "提交中…" : topic.available_actions.includes("resume") ? "恢复生产" : "生产"}</button>;
  const heading = <div className="workspace-title-group">
        <h1>{series.name}</h1>
        <div className="workspace-bound-skills" aria-label="栏目绑定的 Skills">
          {!poolItems.length && <button type="button" className="text-link" onClick={() => setSettingsOpen(true)}>设置栏目 Skill</button>}
          {poolItems.length > 0 && <div><span className="muted">栏目 Skill：</span>{poolItems.map((bound, index) => <span key={bound.id}>
            {index > 0 && <span aria-hidden="true"> · </span>}
            <SkillReferenceButton id={bound.id} known={bound} fallback={bound.display_name ?? bound.name} onInspect={onInspectSkill} showVersion />
          </span>)}</div>}
        </div>
      </div>;
  const headerActions = <>
    <button ref={addTrigger} type="button" className="button button-secondary workspace-add-trigger" aria-expanded={addOpen} aria-controls={addId}
      onClick={() => setAddOpen(value => !value)}><Plus size={16} aria-hidden="true" />{addOpen ? "收起添加" : "添加选题"}</button>
    <button type="button" className="button button-quiet workspace-settings-trigger" aria-expanded={settingsOpen} aria-controls={settingsId} aria-haspopup="dialog"
      onClick={() => setSettingsOpen(true)}><Settings2 size={16} aria-hidden="true" /><span>栏目设置</span></button>
  </>;
  const headerPanels = <>
    {settingsOpen && <WorkspaceDialog id={settingsId} title="栏目设置" className="series-settings-drawer"
      busy={rename.isPending || assignPending || deletePending} onClose={() => setSettingsOpen(false)}>
      <div className="series-settings-body">
      <p className="series-settings-name">{series.name}</p>
      <form className="workspace-rename" onSubmit={event => {
        event.preventDefault();
        const name = nameDraft?.trim();
        if (name && name !== series.name) rename.mutate({ id: series.id, name, expected_revision: series.revision });
      }}>
        <label>栏目名称<input aria-label="栏目名称" maxLength={120} value={nameDraft ?? series.name}
          disabled={rename.isPending} onChange={event => setNameDraft(event.target.value)} /></label>
        <button className="button button-secondary" disabled={rename.isPending || !nameDraft?.trim() || nameDraft.trim() === series.name}>
          {rename.isPending ? "保存中…" : "保存名称"}</button>
        {rename.isError && <p className="form-error" role="alert">{rename.error.message}</p>}
        {rename.isSuccess && <small role="status">名称已保存</small>}
      </form>
      {series.description && <p className="workspace-meta">{series.description}</p>}
      <div className="workspace-series-controls"><label>归属账号<select className="workspace-assign" aria-label="归属账号" value={series.creator_id ?? ""} disabled={assignPending}
        onChange={event => onAssign(event.target.value || null)}>
        <option value="">未分配</option>
        {accounts.map(account => <option key={account.id} value={account.id}>{account.name}</option>)}
      </select></label>
      </div>
      {assignPending && <p role="status" className="series-settings-note">正在调整归属…</p>}
      {assignError && <p className="form-error" role="alert">{assignError}</p>}
      {series.creator_id && <SeriesSkillPool key={`${series.id}:${series.creator_id}`} seriesId={series.id} creatorId={series.creator_id} />}
      {onDelete && <div className="series-settings-danger"><button type="button" className="button button-danger workspace-delete-series" aria-label={`删除栏目 ${series.name}`}
        disabled={deletePending || assignPending} onClick={onDelete}>{deletePending ? "处理中…" : "删除栏目"}</button>
        {deleteError && <p className="form-error" role="alert">{deleteError}</p>}
      </div>}
      </div>
    </WorkspaceDialog>}
    <form id={addId} className="workspace-add" hidden={!addOpen} onSubmit={event => {
      event.preventDefault();
      if (topicTitle.trim()) addTopic.mutate(topicTitle.trim());
    }}>
      <input ref={titleInput} aria-label="新选题标题" placeholder="加一个选题…" maxLength={240} value={topicTitle}
        onChange={event => setTopicTitle(event.target.value)} />
      <button className="button button-primary" disabled={!topicTitle.trim() || addTopic.isPending}>添加</button>
      <button type="button" className="button button-quiet" disabled={addTopic.isPending} onClick={() => { setAddOpen(false); addTrigger.current?.focus(); }}>取消</button>
      {addTopic.isError && <p className="form-error" role="alert">{addTopic.error.message}</p>}
    </form>
    {runMutation.isError && <p className="form-error" role="alert">{runMutation.error.message}</p>}
    {runMutation.isSuccess && <p className="queue-submitted" role="status">已加入生产队列。<Link to={`/runs/${runMutation.data.id}`}>查看任务 →</Link></p>}
  </>;
  return <section className="series-content-section">
    <TopicLibrary seriesId={series.id} startButton={startButton} onDiscuss={onDiscuss}
      series={series} skills={skills}
      heading={heading} headerActions={headerActions} headerPanels={headerPanels}
      productionEnabled={series.is_active && !!series.creator_id}
      batchProductionEnabled={!!skillPool.data?.defaults.image.length && skillPool.data.defaults.image.every(item => poolItems.some(skill => skill.id === item.skill_id))} />
  </section>;
}
