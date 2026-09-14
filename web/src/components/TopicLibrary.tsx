import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import type { ReactNode } from "react";
import { request } from "../api/client";
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
  const state = ["pending", "queued"].includes(params.get("topics") ?? "") ? params.get("topics")! : "all";
  const rawOffset = Number(params.get("offset") ?? 0);
  const offset = Number.isSafeInteger(rawOffset) && rawOffset >= 0 ? rawOffset : 0;
  const selecting = params.get("select") === "1" && !!params.get("research");
  const query = useQuery({ queryKey: ["topics", seriesId, "library", state, offset],
    queryFn: () => request<PageResponse<LibraryTopic>>(`/api/series/${encodeURIComponent(seriesId)}/topic-library?state=${state}&offset=${offset}&limit=20`),
    refetchInterval: 3000, retry: false });
  const change = (key: string, value: string) => setParams(p => { p.set(key, value); if (key === "topics") p.delete("offset"); return p; });
  if (selecting) return <section className="topic-library">
    <button className="button button-secondary" onClick={() => setParams(p => { p.delete("select"); return p; })}>← 返回选题库</button>
    <p className="muted">返回会清空未确认的勾选和编辑草稿；已生成的预览仍可通过链接查看。</p>
    <TopicResearchPanel key={seriesId} seriesId={seriesId} />
  </section>;
  return <section className="topic-library" aria-label="选题库">
    <header className="research-heading"><div><h2>选题库</h2><p>先挑选，再生产。所有选题都在这里。</p></div><span>{query.data?.page.total ?? "—"} 项</span></header>
    <div className="library-filters" role="group" aria-label="选题状态">{[["all", "全部"], ["pending", "待选"], ["queued", "已入队"]].map(([value, label]) =>
      <button key={value} aria-pressed={state === value} onClick={() => change("topics", value)}>{label}</button>)}</div>
    {query.isPending && <p role="status">正在读取选题库…</p>}
    {query.error && <p role="alert" className="form-error">{query.error.message}<button onClick={() => void query.refetch()}>重新读取选题库</button></p>}
    {!query.isPending && !query.error && !query.data?.items.length && <p className="research-empty">{offset ? "这一页没有选题，请返回上一页。" : state === "queued" ? "还没有已入队选题。先从待选中挑选并确认。" : state === "pending" ? "暂无待选建议，可在下方发起调研。" : "还没有选题。可以调研获取建议，也可以手动添加。"}</p>}
    {!query.error && query.data?.items.map((topic, index) => <article className="library-item" key={topic.id} data-testid={`library-${topic.id}`}>
      <div className="library-item-heading"><span className="library-number">{offset + index + 1}</span><h3>{topic.title}</h3>
        {topic.selection_state === "pending" ? <span className="library-pending">{topic.stale ? "待选 · 已过期" : "待选"}</span> : <><span className="library-enqueued">已入队</span><StatusPill status={topic.existing_run_status ?? topic.status} /></>}</div>
      {topic.selection_state === "pending" ? <>
        <p>切入点：{topic.angle}</p><details><summary>切入点与来源</summary><p>{topic.rationale}</p><ul>{topic.sources.map((s, i) => <li key={i}><a href={s.url} target="_blank" rel="noreferrer">{s.title} ↗</a></li>)}</ul></details>
        <button className="button button-secondary" disabled={topic.stale || !topic.available_actions.includes("prepare_topic_selection")} onClick={() => setParams(p => { p.set("research", topic.batch_id); p.set("select", "1"); return p; })}>挑选本批次</button>
        {topic.stale && <small>栏目配置已变化，请重新调研。</small>}
      </> : <>
        <details><summary>切入点与来源</summary><p className="library-brief">{topic.brief || "无补充说明"}</p></details>
        <div className="library-actions">{topic.existing_run_id && <Link to={`/runs/${topic.existing_run_id}`}>查看运行 →</Link>}
        {(topic.available_actions.includes("start") || topic.available_actions.includes("resume")) && startButton(topic)}</div>
      </>}
    </article>)}
    <nav className="library-pagination" aria-label="选题分页"><button disabled={!offset || query.isFetching} onClick={() => change("offset", String(Math.max(0, offset - 20)))}>上一页</button><span>第 {Math.floor(offset / 20) + 1} 页</span><button disabled={query.isFetching || !query.data || offset + 20 >= query.data.page.total} onClick={() => change("offset", String(offset + 20))}>下一页</button></nav>
    <details className="library-research"><summary>调研选题 / 查看调研进度</summary><TopicResearchPanel seriesId={seriesId} controlsOnly /></details>
  </section>;
}
