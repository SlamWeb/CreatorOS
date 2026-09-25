import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { ChevronLeft, ChevronRight } from "lucide-react";
import type { ReactNode } from "react";
import { request, studioApi } from "../api/client";
import type { PageResponse, TopicView } from "../api/types";
import { StatusPill } from "./StatusPill";
import { TopicResearchPanel } from "./TopicResearchPanel";
import "./topic-library.css";

type PendingTopic = { id: string; title: string; selection_state: "pending"; batch_id: string;
  candidate_id: string; angle: string; rationale: string; stale: boolean;
  sources: { title: string; url: string }[]; available_actions: string[] };
type LibraryTopic = PendingTopic | (TopicView & { selection_state: "queued" });

export function TopicLibrary({ seriesId, startButton }: { seriesId: string; startButton: (topic: TopicView) => ReactNode }) {
  const [params, setParams] = useSearchParams();
  const client = useQueryClient();
  const [editing, setEditing] = useState<{ id: string; title: string; brief: string } | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const state = ["pending", "queued"].includes(params.get("topics") ?? "") ? params.get("topics")! : "all";
  const rawOffset = Number(params.get("offset") ?? 0);
  const offset = Number.isSafeInteger(rawOffset) && rawOffset >= 0 ? rawOffset : 0;
  const selecting = params.get("select") === "1" && !!params.get("research");
  const query = useQuery({ queryKey: ["topics", seriesId, "library", state, offset],
    queryFn: () => request<PageResponse<LibraryTopic>>(`/api/series/${encodeURIComponent(seriesId)}/topic-library?state=${state}&offset=${offset}&limit=20`),
    refetchInterval: 3000, retry: false });
  const change = (key: string, value: string) => setParams(p => { p.set(key, value); if (key === "topics") p.delete("offset"); return p; });
  const invalidate = async () => {
    await client.invalidateQueries({ queryKey: ["topics"] });
    await client.invalidateQueries({ queryKey: ["series", seriesId] });
  };
  const editMutation = useMutation({ retry: false,
    mutationFn: (input: { id: string; title: string; brief: string }) => studioApi.editTopic(input.id, { title: input.title, brief: input.brief || null }),
    onSuccess: async () => { setEditing(null); setActionError(null); await invalidate(); },
    onError: (error) => setActionError(error.message) });
  const deleteMutation = useMutation({ retry: false,
    mutationFn: (id: string) => studioApi.deleteTopic(id),
    onSuccess: async () => { setConfirmingDelete(null); setActionError(null); await invalidate(); },
    onError: (error) => { setActionError(error.message); setConfirmingDelete(null); } });
  const moveMutation = useMutation({ retry: false,
    mutationFn: async (input: { topicId: string; delta: number }) => {
      const all = await studioApi.topics(seriesId);
      const ids = all.items.map(t => t.id);
      const from = ids.indexOf(input.topicId);
      const to = from + input.delta;
      if (from < 0 || to < 0 || to >= ids.length) return;
      [ids[from], ids[to]] = [ids[to], ids[from]];
      await studioApi.reorderTopics(seriesId, ids);
    },
    onSuccess: invalidate,
    onError: (error) => setActionError(error.message) });
  if (selecting) return <section className="topic-library">
    <button className="button button-secondary" onClick={() => setParams(p => { p.delete("select"); return p; })}>← 返回选题库</button>
    <p className="muted">返回会清空未确认的勾选和编辑草稿；已生成的预览仍可通过链接查看。</p>
    <TopicResearchPanel key={seriesId} seriesId={seriesId} />
  </section>;
  return <section className="topic-library" aria-label="选题库">
    <header className="research-heading"><div><h2>选题库</h2></div><span className="library-count">{query.data?.page.total ?? "—"} 项</span></header>
    <div className="library-filters" role="group" aria-label="选题状态">{[["all", "全部"], ["pending", "待选"], ["queued", "已入队"]].map(([value, label]) =>
      <button key={value} aria-pressed={state === value} onClick={() => change("topics", value)}>{label}</button>)}</div>
    {query.isPending && <p role="status">正在读取选题库…</p>}
    {query.error && <p role="alert" className="form-error">{query.error.message}<button onClick={() => void query.refetch()}>重新读取选题库</button></p>}
    {!query.isPending && !query.error && !query.data?.items.length && <p className="research-empty">{offset ? "这一页没有选题，请返回上一页。" : state === "queued" ? "还没有已入队选题。先从待选中挑选并确认。" : state === "pending" ? "暂无待选建议。" : "还没有选题。"}</p>}
    {!query.error && query.data?.items.map((topic, index) => <article className="library-item" key={topic.id} data-testid={`library-${topic.id}`}>
      <div className="library-item-heading"><span className="library-number">{offset + index + 1}</span><h3>{topic.title}</h3>
        {topic.selection_state === "pending" ? <span className="library-pending">{topic.stale ? "待选 · 已过期" : "待选"}</span> : <><span className="library-enqueued">已入队</span><StatusPill status={topic.existing_run_status ?? topic.status} /></>}</div>
      {topic.selection_state === "pending" ? <>
        <p>切入点：{topic.angle}</p><details><summary>切入点与来源</summary><p>{topic.rationale}</p><ul>{topic.sources.map((s, i) => <li key={i}><a href={s.url} target="_blank" rel="noreferrer">{s.title} ↗</a></li>)}</ul></details>
        <button className="button button-secondary" disabled={topic.stale || !topic.available_actions.includes("prepare_topic_selection")} onClick={() => setParams(p => { p.set("research", topic.batch_id); p.set("select", "1"); return p; })}>挑选本批次</button>
        {topic.stale && <small>栏目配置已变化，请重新调研。</small>}
      </> : <>
        {editing?.id === topic.id ? <form className="candidate-editor" onSubmit={event => { event.preventDefault(); if (editing.title.trim()) editMutation.mutate(editing); }}>
          <label>标题<input autoFocus required maxLength={240} value={editing.title} onChange={e => setEditing({ ...editing, title: e.target.value })} /></label>
          <label>切入点<textarea rows={3} maxLength={10000} value={editing.brief} onChange={e => setEditing({ ...editing, brief: e.target.value })} /></label>
          <div><button className="button button-primary" disabled={!editing.title.trim() || editMutation.isPending}>保存</button>
            <button className="button button-secondary" type="button" onClick={() => setEditing(null)}>取消</button></div>
        </form> : <>
        <details><summary>切入点与来源</summary><p className="library-brief">{topic.brief || "无补充说明"}</p></details>
        <div className="library-actions">{topic.existing_run_id && <Link to={`/runs/${topic.existing_run_id}`}>查看运行 →</Link>}
        {(topic.available_actions.includes("start") || topic.available_actions.includes("resume")) && startButton(topic)}
        <button type="button" aria-label={`编辑 ${topic.title}`} onClick={() => setEditing({ id: topic.id, title: topic.title, brief: topic.brief ?? "" })}>编辑</button>
        <button type="button" aria-label={`${topic.title} 上移`} disabled={moveMutation.isPending} onClick={() => moveMutation.mutate({ topicId: topic.id, delta: -1 })}>上移</button>
        <button type="button" aria-label={`${topic.title} 下移`} disabled={moveMutation.isPending} onClick={() => moveMutation.mutate({ topicId: topic.id, delta: 1 })}>下移</button>
        {confirmingDelete === topic.id
          ? <button type="button" className="library-delete" disabled={deleteMutation.isPending} onClick={() => deleteMutation.mutate(topic.id)}>确认删除</button>
          : <button type="button" aria-label={`删除 ${topic.title}`} onClick={() => setConfirmingDelete(topic.id)}>删除</button>}</div>
        </>}
        {actionError && <p className="form-error" role="alert">{actionError}</p>}
      </>}
    </article>)}
    {query.data && (query.data.page.total > 20 || offset > 0) && <nav className="library-pagination" aria-label="选题分页"><button aria-label="上一页" disabled={!offset || query.isFetching} onClick={() => change("offset", String(Math.max(0, offset - 20)))}><ChevronLeft size={15} strokeWidth={2} /></button><span>{Math.floor(offset / 20) + 1} / {Math.max(1, Math.ceil(query.data.page.total / 20))}</span><button aria-label="下一页" disabled={query.isFetching || offset + 20 >= query.data.page.total} onClick={() => change("offset", String(offset + 20))}><ChevronRight size={15} strokeWidth={2} /></button></nav>}
    <details className="library-research"><summary>调研选题</summary><TopicResearchPanel seriesId={seriesId} controlsOnly /></details>
  </section>;
}
