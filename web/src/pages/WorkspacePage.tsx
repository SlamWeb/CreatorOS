import { useState } from "react";
import { Link, Navigate, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCreators } from "../api/hooks";
import { studioApi } from "../api/client";
import type { SeriesView, TopicView } from "../api/types";
import { ErrorState, LoadingState } from "../components/PageState";
import { StatusPill } from "../components/StatusPill";
import { ProducerSkillPanel } from "../components/ProducerSkillPanel";
import { TopicLibrary } from "../components/TopicLibrary";
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
  const seriesAll = useQuery({ queryKey: ["series-all"], queryFn: studioApi.seriesAll });
  const [accountDraft, setAccountDraft] = useState("");
  const [seriesDraft, setSeriesDraft] = useState<{ name: string; creatorId: string | null } | null>(null);

  const refresh = async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: ["series-all"] }),
      client.invalidateQueries({ queryKey: ["creators"] }),
      client.invalidateQueries({ queryKey: ["overview"] }),
    ]);
  };

  const createAccount = useMutation({
    retry: false,
    mutationFn: (name: string) => studioApi.createCreator({ display_name: name }),
    onSuccess: async () => { setAccountDraft(""); await refresh(); },
  });

  const createSeries = useMutation({
    retry: false,
    mutationFn: (input: { name: string; creatorId: string | null }) => studioApi.composeSeries({
      name: input.name, description: "", audience: "", creator_id: input.creatorId,
      skill_name: "knowledge-to-carousel", request_id: crypto.randomUUID().replaceAll("-", ""),
    }),
    onSuccess: async (result) => { setSeriesDraft(null); await refresh(); selectSeries(result.series.id); },
  });

  const assign = useMutation({
    retry: false,
    mutationFn: (input: { series: SeriesView; creatorId: string | null }) => studioApi.assignSeries(input.series.id, {
      creator_id: input.creatorId, expected_revision: input.series.revision,
      request_id: crypto.randomUUID().replaceAll("-", ""),
    }),
    onSuccess: refresh,
    onError: refresh,
  });

  if (creators.isPending || seriesAll.isPending) return <LoadingState label="正在读取…" />;
  if (creators.isError || seriesAll.isError) {
    const error = creators.error ?? seriesAll.error;
    return <ErrorState message={error?.message ?? "读取失败"} onRetry={() => void Promise.all([creators.refetch(), seriesAll.refetch()])} />;
  }

  const accounts = creators.data.items;
  const allSeries = seriesAll.data;
  const valid = allSeries.some(s => s.id === params.get("series"));
  const seriesId = valid ? params.get("series")! : (allSeries[0]?.id ?? null);
  const series = allSeries.find(s => s.id === seriesId) ?? null;
  const accountName = (id: string | null) => accounts.find(a => a.id === id)?.display_name ?? null;

  function selectSeries(id: string) {
    setParams(previous => {
      const next = new URLSearchParams(previous);
      next.set("series", id);
      for (const key of ["topics", "offset", "select", "research", "operation"]) next.delete(key);
      return next;
    });
  }

  return <div className="workspace">
    <aside className="workspace-rail" aria-label="账号与栏目">
      {accounts.map(account => <section className="rail-group" key={account.id}>
        <div className="rail-account">
          <span className="rail-avatar" aria-hidden="true">{account.display_name.slice(0, 1)}</span>
          <h2>{account.display_name}</h2>
          <button type="button" className="rail-add" aria-label={`在 ${account.display_name} 下新建栏目`}
            onClick={() => setSeriesDraft({ name: "", creatorId: account.id })}>+</button>
        </div>
        {allSeries.filter(s => s.creator_id === account.id).map(item => <button type="button" key={item.id}
          className={`rail-series ${item.id === seriesId ? "active" : ""}`}
          aria-current={item.id === seriesId ? "page" : undefined} onClick={() => selectSeries(item.id)}>
          <span>{item.name}</span>
          <small>{item.latest_run_status ? <StatusPill status={item.latest_run_status} /> : `${item.topic_count} 选题`}</small>
        </button>)}
      </section>)}
      {!!allSeries.some(s => s.creator_id === null) && <section className="rail-group">
        <div className="rail-account"><h2>未分配</h2>
          <button type="button" className="rail-add" aria-label="新建未分配栏目" onClick={() => setSeriesDraft({ name: "", creatorId: null })}>+</button>
        </div>
        {allSeries.filter(s => s.creator_id === null).map(item => <button type="button" key={item.id}
          className={`rail-series ${item.id === seriesId ? "active" : ""}`}
          aria-current={item.id === seriesId ? "page" : undefined} onClick={() => selectSeries(item.id)}>
          <span>{item.name}</span><small>{item.topic_count} 选题</small>
        </button>)}
      </section>}
      <div className="rail-foot">
        {seriesDraft ? <form className="rail-form" onSubmit={event => {
          event.preventDefault();
          if (seriesDraft.name.trim()) createSeries.mutate({ name: seriesDraft.name.trim(), creatorId: seriesDraft.creatorId });
        }}>
          <input autoFocus required maxLength={120} placeholder="栏目名称" value={seriesDraft.name}
            aria-label="新栏目名称" onChange={event => setSeriesDraft({ ...seriesDraft, name: event.target.value })} />
          <div><button className="button button-primary" disabled={createSeries.isPending}>创建</button>
            <button className="button button-secondary" type="button" onClick={() => setSeriesDraft(null)}>取消</button></div>
          {createSeries.isError && <p className="form-error" role="alert">{createSeries.error.message}</p>}
        </form> : null}
        <form className="rail-form" onSubmit={event => {
          event.preventDefault();
          if (accountDraft.trim()) createAccount.mutate(accountDraft.trim());
        }}>
          <input maxLength={120} placeholder="新账号名称" value={accountDraft}
            aria-label="新账号名称" onChange={event => setAccountDraft(event.target.value)} />
        </form>
        {createAccount.isError && <p className="form-error" role="alert">{createAccount.error.message}</p>}
      </div>
    </aside>
    <main className="workspace-main">
      {!series && <div className="workspace-empty">
        <h1>栏目</h1>
        <p>还没有栏目。先在 Skill 页组合或在这里新建。</p>
        <button type="button" className="button button-primary" onClick={() => setSeriesDraft({ name: "", creatorId: accounts[0]?.id ?? null })}>新建栏目</button>
      </div>}
      {series && <SeriesWorkspace key={series.id} series={series} accountLabel={accountName(series.creator_id)}
        accounts={accounts.map(a => ({ id: a.id, name: a.display_name }))}
        onAssign={(creatorId) => assign.mutate({ series, creatorId })} />}
    </main>
  </div>;
}

function SeriesWorkspace({ series, accountLabel, accounts, onAssign }: {
  series: SeriesView;
  accountLabel: string | null;
  accounts: { id: string; name: string }[];
  onAssign: (creatorId: string | null) => void;
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
  return <>
    <header className="workspace-head">
      <div>
        <h1>{series.name}</h1>
        <p className="workspace-meta">{accountLabel ?? "未分配账号"} · {series.skill_name ?? "组合"}{series.skill_name === "knowledge-to-carousel" ? " · 知识点轮播" : ""}{series.description ? ` · ${series.description}` : ""}</p>
      </div>
      <select className="workspace-assign" aria-label="归属账号" value={series.creator_id ?? ""}
        onChange={event => onAssign(event.target.value || null)}>
        <option value="">未分配</option>
        {accounts.map(account => <option key={account.id} value={account.id}>{account.name}</option>)}
      </select>
    </header>
    {series.skill_name !== null && <details className="workspace-skill"><summary>生产 Skill</summary><ProducerSkillPanel seriesId={series.id} current={series.skill_name} /></details>}
    <form className="workspace-add" onSubmit={event => {
      event.preventDefault();
      if (topicTitle.trim()) addTopic.mutate(topicTitle.trim());
    }}>
      <input aria-label="新选题标题" placeholder="加一个选题…" maxLength={240} value={topicTitle}
        onChange={event => setTopicTitle(event.target.value)} />
      <button className="button button-primary" disabled={!topicTitle.trim() || addTopic.isPending}>添加</button>
      {addTopic.isError && <p className="form-error" role="alert">{addTopic.error.message}</p>}
    </form>
    {runMutation.isError && <p className="form-error" role="alert">{runMutation.error.message}</p>}
    {runMutation.isSuccess && <p className="queue-submitted" role="status">已提交后台。<Link to={`/runs/${runMutation.data.id}`}>查看本次运行 →</Link></p>}
    <TopicLibrary seriesId={series.id} startButton={startButton} />
  </>;
}
