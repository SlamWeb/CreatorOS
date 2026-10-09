import { useEffect, useRef, useState } from "react";
import { Link, Navigate, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCreators } from "../api/hooks";
import { studioApi } from "../api/client";
import type { ProducerSkillItem, SeriesView, TopicView } from "../api/types";
import { ErrorState, LoadingState } from "../components/PageState";
import { LibraryFilters, TopicLibrary } from "../components/TopicLibrary";
import { AccountChatPanel } from "../components/AccountChatPanel";
import { BrandMark } from "../components/BrandMark";
import { SkillInspector } from "../components/SkillInspector";
import { CreatorTaskSummary } from "../components/CreatorTaskSummary";
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
  const creators = useCreators();
  const seriesAll = useQuery({ queryKey: ["series-all"], queryFn: studioApi.seriesAll,
    refetchInterval: q => q.state.data?.some(s => ["queued", "producing", "validating"].includes(s.latest_run_status ?? "")) ? 3000 : false });
  const skills = useQuery({ queryKey: ["producer-skills"], queryFn: studioApi.producerSkills });
  const [accountDraft, setAccountDraft] = useState<{ name: string; handle: string } | null>(null);
  const [seriesDraft, setSeriesDraft] = useState<{ name: string; creatorId: string | null } | null>(null);
  const [recipe, setRecipe] = useState<"legacy" | "pair">("legacy");
  const [pairMind, setPairMind] = useState("");
  const [pairVisual, setPairVisual] = useState("");
  const [confirmingAccount, setConfirmingAccount] = useState<string | null>(null);
  const [treeOpen, setTreeOpen] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);
  const [chatSeed, setChatSeed] = useState<{text:string; id:number} | null>(null);
  const chatOpener = useRef<HTMLButtonElement>(null);
  const [inspectedSkill, setInspectedSkill] = useState<ProducerSkillItem | null>(null);
  useEffect(() => {
    if (params.has("skill_edit")) setChatOpen(true);
  }, [params]);

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
    onSuccess: async () => { setAccountDraft(null); await refresh(); },
  });

  const deleteAccount = useMutation({
    retry: false,
    mutationFn: (id: string) => studioApi.deleteCreator(id),
    onSuccess: async () => { setConfirmingAccount(null); await refresh(); },
    onError: () => setConfirmingAccount(null),
  });

  const createSeries = useMutation({
    retry: false,
    mutationFn: (input: { name: string; creatorId: string | null }) => studioApi.composeSeries(
      recipe === "pair"
        ? { name: input.name, description: "", audience: "", creator_id: input.creatorId,
            mind_skill_id: pairMind, production_skill_id: pairVisual, request_id: crypto.randomUUID().replaceAll("-", "") }
        : { name: input.name, description: "", audience: "", creator_id: input.creatorId,
            skill_name: "knowledge-to-carousel", request_id: crypto.randomUUID().replaceAll("-", "") },
    ),
    onSuccess: async (result) => { setSeriesDraft(null); setRecipe("legacy"); await refresh(); selectSeries(result.series.id, result.series.creator_id); },
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

  function selectSeries(id: string, owner?: string | null) {
    setTreeOpen(false); setChatSeed(null);
    setParams(previous => {
      const next = new URLSearchParams(previous);
      next.set("series", id);
      const selected = allSeries.find(item => item.id === id);
      const creatorId = owner === undefined ? selected?.creator_id : owner;
      if (creatorId) next.set("creator", creatorId);
      else next.delete("creator");
      for (const key of ["topics", "offset", "select", "research", "operation", "topic"]) next.delete(key);
      return next;
    });
  }

  function selectAccount(id: string) {
    setTreeOpen(false); setChatSeed(null);
    setParams(previous => {
      const next = new URLSearchParams(previous); next.set("creator", id);
      for (const key of ["series", "topics", "offset", "select", "research", "operation", "topic", "chat"]) next.delete(key);
      return next;
    });
  }
  const discuss = (title: string) => {
    setChatSeed({text:`我们讨论一下选题「${title}」，先不要生产。`,id:Date.now()}); setChatOpen(true);
  };

  return <div className={`workspace${chatOpen ? " chat-open" : ""}`}>
    <button type="button" className="workspace-tree-toggle" aria-expanded={treeOpen} onClick={() => setTreeOpen(!treeOpen)}>账号与栏目 · {series?.name ?? account?.display_name ?? "选择账号"}</button>
    <aside className={`workspace-rail${treeOpen ? " is-open" : ""}`} aria-label="账号与栏目">
      {accounts.map(account => <section className="rail-group" key={account.id}>
        <div className="rail-account">
          <button type="button" className="rail-account-select" aria-label={`查看账号 ${account.display_name}`} aria-current={!seriesId && account.id === scopedCreatorId ? "page" : undefined} onClick={() => selectAccount(account.id)}>
            <span className="rail-avatar" aria-hidden="true">{account.display_name.slice(0, 1)}</span><h2>{account.display_name}</h2>
          </button>
          {!allSeries.some(s => s.creator_id === account.id) && (
            confirmingAccount === account.id
              ? <button type="button" className="rail-add rail-delete" disabled={deleteAccount.isPending}
                  onClick={() => deleteAccount.mutate(account.id)}>确认删除</button>
              : <button type="button" className="rail-add" aria-label={`删除账号 ${account.display_name}`}
                  title="删除账号" onClick={() => setConfirmingAccount(account.id)}>×</button>
          )}
          <button type="button" className="rail-add" aria-label={`在 ${account.display_name} 下新建栏目`}
            onClick={() => { setSeriesDraft({ name: "", creatorId: account.id }); setConfirmingAccount(null); }}>+</button>
        </div>
        {allSeries.filter(s => s.creator_id === account.id).map(item => <button type="button" key={item.id}
          className={`rail-series ${item.id === seriesId ? "active" : ""}`}
          aria-current={item.id === seriesId ? "page" : undefined} onClick={() => selectSeries(item.id)}>
          <span>{item.name}</span>
        </button>)}
      </section>)}
      {!!allSeries.some(s => s.creator_id === null) && <section className="rail-group">
        <div className="rail-account"><h2>未分配</h2>
          <button type="button" className="rail-add" aria-label="新建未分配栏目" onClick={() => setSeriesDraft({ name: "", creatorId: null })}>+</button>
        </div>
        {allSeries.filter(s => s.creator_id === null).map(item => <button type="button" key={item.id}
          className={`rail-series ${item.id === seriesId ? "active" : ""}`}
          aria-current={item.id === seriesId ? "page" : undefined} onClick={() => selectSeries(item.id)}>
          <span>{item.name}</span>
        </button>)}
      </section>}
      <div className="rail-foot">
        {seriesDraft ? <form className="rail-form" onSubmit={event => {
          event.preventDefault();
          if (seriesDraft.name.trim()) createSeries.mutate({ name: seriesDraft.name.trim(), creatorId: seriesDraft.creatorId });
        }}>
          <input autoFocus required maxLength={120} placeholder="栏目名称" value={seriesDraft.name}
            aria-label="新栏目名称" onChange={event => setSeriesDraft({ ...seriesDraft, name: event.target.value })} />
          <select aria-label="配方" value={recipe} onChange={event => setRecipe(event.target.value as "legacy" | "pair")}>
            <option value="legacy">知识点轮播</option>
            <option value="pair">组合（Mind × Visualize）</option>
          </select>
          {recipe === "pair" && <>
            <select aria-label="Mind" value={pairMind} onChange={event => setPairMind(event.target.value)}>
              <option value="">Mind…</option>
              {(skills.data?.items ?? []).filter(s => s.role === "mind").map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
            <select aria-label="Visualize" value={pairVisual} onChange={event => setPairVisual(event.target.value)}>
              <option value="">Visualize…</option>
              {(skills.data?.items ?? []).filter(s => s.role === "production").map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </>}
          <div><button className="button button-primary" disabled={createSeries.isPending || (recipe === "pair" && (!pairMind || !pairVisual))}>创建</button>
            <button className="button button-secondary" type="button" onClick={() => setSeriesDraft(null)}>取消</button></div>
          {createSeries.isError && <p className="form-error" role="alert">{createSeries.error.message}</p>}
        </form> : null}
        {accountDraft === null ? (
          <button type="button" className="rail-new-account" onClick={() => setAccountDraft({ name: "", handle: "" })}>+ 新账号</button>
        ) : <form className="rail-form" onSubmit={event => {
          event.preventDefault();
          if (accountDraft.name.trim()) createAccount.mutate({ name: accountDraft.name.trim(), handle: accountDraft.handle.trim() });
        }}>
          <input autoFocus required maxLength={120} placeholder="账号名称" value={accountDraft.name}
            aria-label="新账号名称" onChange={event => setAccountDraft({ ...accountDraft, name: event.target.value })} />
          <input maxLength={160} placeholder="账号标识（可选）" value={accountDraft.handle}
            aria-label="新账号标识" onChange={event => setAccountDraft({ ...accountDraft, handle: event.target.value })} />
          <div><button className="button button-primary" disabled={createAccount.isPending}>创建</button>
            <button className="button button-secondary" type="button" onClick={() => setAccountDraft(null)}>取消</button></div>
          {createAccount.isError && <p className="form-error" role="alert">{createAccount.error.message}</p>}
        </form>}
        {deleteAccount.isError && <p className="form-error" role="alert">{deleteAccount.error.message}</p>}
      </div>
    </aside>
    <main className="workspace-main">
      {assign.error && <p className="form-error" role="alert">{assign.error.message}</p>}
      {params.has("series") && !series && <p className="form-error" role="alert">栏目不存在或不属于当前账号，请从左侧重新选择。</p>}
      {params.has("creator") && !account && <p className="form-error" role="alert">账号不存在，请从左侧重新选择。</p>}
      {account && <CreatorTaskSummary key={`${account.id}:${seriesId ?? ""}`} creatorId={account.id} seriesId={seriesId} />}
      {!series && (!params.has("creator") || account) && <>
      <header className="workspace-head"><div><h1>{account?.display_name ?? "账号与栏目"}</h1></div>
        {!!accountSeries.length && <button type="button" className="button button-secondary" onClick={() => setSeriesDraft({name:"",creatorId:scopedCreatorId})}>新建栏目</button>}
      </header>
      {!!accountSeries.some(item => item.topic_count > 0) && <><LibraryFilters />{accountSeries.filter(item => item.topic_count > 0).map(item => <SeriesWorkspace key={item.id} series={item} overview onSelect={() => selectSeries(item.id)} onDiscuss={discuss}
        accounts={accounts.map(a => ({id:a.id,name:a.display_name}))} skills={skills.data?.items ?? []} onInspectSkill={setInspectedSkill}
        onAssign={creatorId => assign.mutate({series:item,creatorId})} />)}</>}
      {!accountSeries.some(item => item.topic_count > 0) && <div className="workspace-empty">
        <p>{account ? accountSeries.length ? "这个账号还没有选题。" : "这个账号还没有栏目。" : "还没有栏目。先在 Skill 页组合或在这里新建。"}</p>
        <button type="button" className="button button-primary" onClick={() => setSeriesDraft({ name: "", creatorId: scopedCreatorId ?? accounts[0]?.id ?? null })}>新建栏目</button>
      </div>}
      </>}
      {series && <SeriesWorkspace key={series.id} series={series}
        accounts={accounts.map(a => ({ id: a.id, name: a.display_name }))}
        skills={skills.data?.items ?? []} onInspectSkill={setInspectedSkill}
        onDiscuss={discuss}
        onAssign={(creatorId) => assign.mutate({ series, creatorId })}
        onDelete={() => {
          if (series.topic_count > 0 && !window.confirm("从工作区移除，保留选题、图片与记录")) return;
          deleteSeries.mutate(series);
        }} deletePending={deleteSeries.isPending}
        deleteError={deleteSeries.variables?.id === series.id ? deleteSeries.error?.message ?? null : null} />}
    </main>
    <button ref={chatOpener} type="button" className="workspace-chat-launcher" aria-label={chatOpen ? "关闭账号对话" : "打开账号对话"} aria-expanded={chatOpen}
      disabled={!account} title={account ? `与 ${account.display_name} 对话` : "先选择一个账号"}
      onClick={() => {setChatSeed(null);setChatOpen(!chatOpen);}}><BrandMark /></button>
    <AccountChatPanel creatorId={account?.id ?? null} accountName={account?.display_name ?? "全部账号"} open={chatOpen && !!account}
      onClose={() => {setChatOpen(false);setChatSeed(null);}} openerRef={chatOpener} draftSeed={chatSeed?.text} draftSeedId={chatSeed?.id} />
    {inspectedSkill && <SkillInspector skill={inspectedSkill} onClose={() => setInspectedSkill(null)}
      editContext={{ creatorId: account?.id, seriesId: series?.id }}
      onAgentEdit={account ? prompt => {
        setInspectedSkill(null); setChatSeed({ text: prompt, id: Date.now() }); setChatOpen(true);
      } : undefined} />}
  </div>;
}

function SeriesWorkspace({ series, accounts, skills, onInspectSkill, onAssign, overview = false, onSelect, onDiscuss, onDelete, deletePending = false, deleteError }: {
  series: SeriesView;
  accounts: { id: string; name: string }[];
  skills: ProducerSkillItem[];
  onInspectSkill: (skill: ProducerSkillItem) => void;
  onAssign: (creatorId: string | null) => void;
  overview?: boolean; onSelect?: () => void; onDiscuss?: (topic: string) => void;
  onDelete?: () => void; deletePending?: boolean; deleteError?: string | null;
}) {
  const client = useQueryClient();
  const [topicTitle, setTopicTitle] = useState("");
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
      await Promise.all([["topics"], ["series", series.id], ["series-all"]].map(key => client.invalidateQueries({ queryKey: key })));
    },
  });
  const startButton = (topic: TopicView) => <button type="button" className="topic-run-button"
    disabled={!series.is_active || !series.creator_id || runMutation.isPending}
    title={!series.creator_id ? "先分配账号" : undefined}
    onClick={() => runMutation.mutate(topic)}>{runMutation.isPending && runMutation.variables?.id === topic.id ? "提交中…" : topic.available_actions.includes("resume") ? "恢复生产" : "生产"}</button>;
  return <section className={overview ? "account-series-section" : "series-content-section"}>
    <header className={overview ? "account-series-head" : "workspace-head"}>
      <div>
        {overview ? <h2><button type="button" onClick={onSelect}>{series.name} <span aria-hidden="true">→</span></button></h2> : <h1>{series.name}</h1>}
        {!overview && <><div className="workspace-bound-skills" aria-label="栏目绑定的 Skills">
          {(series.skill_name ? [series.skill_name] : [series.mind_skill_id, series.production_skill_id].filter((id): id is string => !!id))
            .map((id, index) => {
              const bound = skills.find(item => item.id === id || item.name === id);
              const label = bound?.name ?? "Skill 信息不可读取";
              return <span key={`${id}-${index}`}>
                {index > 0 && <span aria-hidden="true"> × </span>}
                {bound ? <button type="button" onClick={() => onInspectSkill(bound)}>{label}</button> : <span title={`当前 Skill 不在可查看目录中：${id}`}>{label}</span>}
              </span>;
            })}
        </div>
        {series.description ? <p className="workspace-meta">{series.description}</p> : null}</>}
      </div>
      {!overview && <div className="workspace-series-controls">
      <select className="workspace-assign" aria-label="归属账号" value={series.creator_id ?? ""}
        onChange={event => onAssign(event.target.value || null)}>
        <option value="">未分配</option>
        {accounts.map(account => <option key={account.id} value={account.id}>{account.name}</option>)}
      </select>
      {onDelete && <button type="button" className="button button-secondary workspace-delete-series" aria-label={`删除栏目 ${series.name}`}
        disabled={deletePending} onClick={onDelete}>{deletePending ? "处理中…" : "删除栏目"}</button>}
      </div>}
    </header>
    {deleteError && <p className="form-error" role="alert">{deleteError}</p>}
    {!overview && <form className="workspace-add" onSubmit={event => {
      event.preventDefault();
      if (topicTitle.trim()) addTopic.mutate(topicTitle.trim());
    }}>
      <input aria-label="新选题标题" placeholder="加一个选题…" maxLength={240} value={topicTitle}
        onChange={event => setTopicTitle(event.target.value)} />
      <button className="button button-primary" disabled={!topicTitle.trim() || addTopic.isPending}>添加</button>
      {addTopic.isError && <p className="form-error" role="alert">{addTopic.error.message}</p>}
    </form>}
    {runMutation.isError && <p className="form-error" role="alert">{runMutation.error.message}</p>}
    {runMutation.isSuccess && <p className="queue-submitted" role="status">已提交后台。<Link to={`/runs/${runMutation.data.id}`}>查看本次运行 →</Link></p>}
    <TopicLibrary seriesId={series.id} startButton={startButton} compact={overview} onDiscuss={onDiscuss} />
  </section>;
}
