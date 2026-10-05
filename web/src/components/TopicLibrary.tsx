import { useEffect, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { ChevronLeft, ChevronRight, MoreHorizontal, RefreshCw, X } from "lucide-react";
import { apiUrl, request, studioApi } from "../api/client";
import type { PageResponse, TopicView } from "../api/types";
import { StatusPill } from "./StatusPill";
import { TopicResearchPanel } from "./TopicResearchPanel";
import "./topic-library.css";

type PendingTopic = { id: string; title: string; selection_state: "pending"; batch_id: string;
  candidate_id: string; angle: string; rationale: string; stale: boolean;
  sources: { title: string; url: string }[]; available_actions: string[] };
type LibraryTopic = PendingTopic | (TopicView & { selection_state: "queued" });

export function TopicLibrary({ seriesId, startButton, compact = false, onDiscuss }: {
  seriesId: string; startButton: (topic: TopicView) => ReactNode; compact?: boolean;
  onDiscuss?: (topic: string) => void;
}) {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const client = useQueryClient();
  const [researchOpen, setResearchOpen] = useState(false);
  const state = ["pending", "queued"].includes(params.get("topics") ?? "") ? params.get("topics")! : "all";
  const offsetKey = compact ? `offset-${seriesId}` : "offset";
  const rawOffset = Number(params.get(offsetKey) ?? 0);
  const offset = Number.isSafeInteger(rawOffset) && rawOffset >= 0 ? rawOffset : 0;
  const selecting = !compact && params.get("select") === "1" && !!params.get("research");
  const query = useQuery({ queryKey: ["topics", seriesId, "library", state, offset],
    queryFn: () => request<PageResponse<LibraryTopic>>(`/api/series/${encodeURIComponent(seriesId)}/topic-library?state=${state}&offset=${offset}&limit=20`),
    refetchInterval: q => q.state.data?.items.some(t => t.selection_state === "queued" &&
      ["queued", "producing", "validating"].includes(t.existing_run_status ?? "")) ? 3000 : false,
    retry: false });
  const change = (key: string, value: string) => setParams(p => {
    const next = new URLSearchParams(p); next.set(key, value); next.delete("topic"); return next;
  });
  const openDetail = (id: string) => setParams(p => { const next = new URLSearchParams(p); next.set("topic", id); return next; });
  const closeDetail = () => {
    const id = params.get("topic");
    setParams(p => { const next = new URLSearchParams(p); next.delete("topic"); return next; });
    requestAnimationFrame(() => document.getElementById(`topic-card-${id}`)?.focus());
  };
  const rememberReturn = (id: string, url = location.pathname + location.search) => {
    try { sessionStorage.setItem("creatoros-content-return", JSON.stringify({
      url, y: window.scrollY, focus: `topic-card-${id}`,
    })); } catch { /* Navigation still works without browser storage. */ }
  };
  const chooseBatch = (topic: PendingTopic) => setParams(p => {
    const next = new URLSearchParams(p); next.set("series", seriesId); next.set("research", topic.batch_id);
    next.set("select", "1"); next.delete("topic"); return next;
  });
  const detail = query.data?.items.find(topic => topic.id === params.get("topic"));
  const returnParams = new URLSearchParams(location.search);
  returnParams.delete("topic");
  const detailReturnUrl = location.pathname + (returnParams.size ? `?${returnParams}` : "");
  useEffect(() => {
    try {
      const saved = JSON.parse(sessionStorage.getItem("creatoros-content-return") ?? "null");
      if (!saved || saved.url !== location.pathname + location.search || typeof saved.focus !== "string") return;
      const target = document.getElementById(saved.focus);
      if (!target || !query.data?.items.some(t => `topic-card-${t.id}` === saved.focus)) return;
      sessionStorage.removeItem("creatoros-content-return");
      requestAnimationFrame(() => {window.scrollTo(0, Number(saved.y) || 0);target.focus({preventScroll:true});});
    } catch { /* Invalid/stale restoration data must not break the library. */ }
  }, [query.data, location.pathname, location.search]);
  if (selecting) return <section className="topic-library">
    <button className="button button-secondary" onClick={() => setParams(p => { const next = new URLSearchParams(p); next.delete("select"); return next; })}>← 返回选题库</button>
    <TopicResearchPanel key={seriesId} seriesId={seriesId} />
  </section>;
  return <section className={`topic-library${compact ? " compact-library" : ""}`} aria-label="选题库">
    <header className="library-heading">{!compact && <h2>内容</h2>}
      <span className="library-count">{query.data?.page.total ?? "—"} 项</span>
      <button type="button" className="library-refresh" aria-label="刷新选题库" disabled={query.isFetching} onClick={() => void query.refetch()}><RefreshCw size={14} /></button>
    </header>
    {!compact && <LibraryFilters />}
    {query.isPending && <p role="status">正在读取内容…</p>}
    {query.error && <p role="alert" className="form-error">{query.error.message}<button onClick={() => void query.refetch()}>重新读取选题库</button></p>}
    {!query.isPending && !query.error && !query.data?.items.length && <p className="research-empty">{offset ? "这一页没有选题，请返回上一页。" : state === "queued" ? "还没有已入队选题。" : state === "pending" ? "暂无待选建议。" : "还没有选题。"}</p>}
    {!query.error && <div className="content-feed">{query.data?.items.map(topic => {
      const pending = topic.selection_state === "pending";
      const content = <>{!pending && topic.cover_url && <CardCover key={topic.cover_url} url={topic.cover_url} title={topic.title} />}
        <div className="content-card-copy"><h3>{topic.title}</h3>
          {(pending ? topic.angle : topic.brief) && <p>{pending ? topic.angle : topic.brief}</p>}
        </div></>;
      return <article className="content-card" key={topic.id} data-testid={`library-${topic.id}`}>
        {!pending && topic.existing_run_id
          ? <Link id={`topic-card-${topic.id}`} className="content-card-main" aria-label={`查看内容 ${topic.title}`}
              to={`/runs/${topic.existing_run_id}?return=${encodeURIComponent(location.pathname + location.search)}`}
              onClick={() => rememberReturn(topic.id)}>{content}</Link>
          : <button id={`topic-card-${topic.id}`} type="button" className="content-card-main" aria-label={`查看选题 ${topic.title}`}
              onClick={() => openDetail(topic.id)}>{content}</button>}
        <footer className="content-card-footer">
          {pending ? <span className="library-pending">{topic.stale ? "待选 · 已过期" : "待选"}</span>
            : <StatusPill status={topic.existing_run_status ?? topic.status} />}
          {!pending && topic.card_count ? <small>{topic.card_count} 张</small> : null}
          {!pending && (topic.available_actions.includes("start") || topic.available_actions.includes("resume")) && startButton(topic)}
          <details className="content-card-menu" onKeyDown={e => { if (e.key === "Escape") { e.currentTarget.open = false; e.currentTarget.querySelector("summary")?.focus(); } }}>
            <summary aria-label={`选题操作 ${topic.title}`}><MoreHorizontal size={18} /></summary>
            <div><button type="button" onClick={e => { e.currentTarget.closest("details")?.removeAttribute("open"); openDetail(topic.id); }}>详情与编辑</button>
              {onDiscuss && <button type="button" onClick={e => { e.currentTarget.closest("details")?.removeAttribute("open"); onDiscuss(topic.title); }}>与 Agent 讨论</button>}</div>
          </details>
        </footer>
      </article>;
    })}</div>}
    {query.data && (query.data.page.total > 20 || offset > 0) && <nav className="library-pagination" aria-label="选题分页">
      <button aria-label="上一页" disabled={!offset || query.isFetching} onClick={() => change(offsetKey, String(Math.max(0, offset - 20)))}><ChevronLeft size={15} /></button>
      <span>{Math.floor(offset / 20) + 1} / {Math.max(1, Math.ceil(query.data.page.total / 20))}</span>
      <button aria-label="下一页" disabled={query.isFetching || offset + 20 >= query.data.page.total} onClick={() => change(offsetKey, String(offset + 20))}><ChevronRight size={15} /></button>
    </nav>}
    {!compact && <details className="library-research" open={researchOpen} onToggle={e => setResearchOpen(e.currentTarget.open)}>
      <summary>调研选题</summary>{researchOpen && <TopicResearchPanel seriesId={seriesId} controlsOnly />}
    </details>}
    {detail && <TopicDetail key={detail.id} topic={detail} onClose={closeDetail} onChoose={chooseBatch}
      returnUrl={detailReturnUrl} onOpenRun={() => rememberReturn(detail.id, detailReturnUrl)}
      startButton={startButton} onDiscuss={onDiscuss} onChanged={async () => {
        await Promise.all([client.invalidateQueries({queryKey:["topics"]}), client.invalidateQueries({queryKey:["series-all"]})]);
      }} />}
  </section>;
}

export function LibraryFilters() {
  const [params, setParams] = useSearchParams();
  const state = ["pending", "queued"].includes(params.get("topics") ?? "") ? params.get("topics") : "all";
  return <div className="library-filters" role="group" aria-label="选题状态">{[["all", "全部"], ["pending", "待选"], ["queued", "已入队"]].map(([value, label]) =>
    <button key={value} aria-pressed={state === value} onClick={() => setParams(p => {
      const next = new URLSearchParams(p); next.set("topics", value); next.delete("topic");
      for (const key of [...next.keys()]) if (key === "offset" || key.startsWith("offset-")) next.delete(key);
      return next;
    })}>{label}</button>)}</div>;
}

function CardCover({ url, title }: {url: string; title: string}) {
  const [failed, setFailed] = useState(false);
  return failed ? <p className="cover-unavailable">封面暂不可读取</p>
    : <img className="content-card-cover" src={apiUrl(url)} alt={`${title} · 第一张图片`} loading="lazy" onError={() => setFailed(true)} />;
}

function TopicDetail({ topic, onClose, onChoose, startButton, onDiscuss, onChanged, returnUrl, onOpenRun }: {
  topic: LibraryTopic; onClose: () => void; onChoose: (topic: PendingTopic) => void;
  returnUrl: string; onOpenRun: () => void;
  startButton: (topic: TopicView) => ReactNode; onDiscuss?: (title: string) => void; onChanged: () => Promise<void>;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [title, setTitle] = useState(topic.title);
  const [brief, setBrief] = useState(topic.selection_state === "queued" ? topic.brief ?? "" : topic.angle);
  const [editing, setEditing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  useEffect(() => { dialog.current?.showModal(); }, []);
  const save = useMutation({retry:false, mutationFn: () => studioApi.editTopic(topic.id, {title:title.trim(),brief:brief || null}),
    onSuccess: async () => { setEditing(false); await onChanged(); }});
  const remove = useMutation({retry:false, mutationFn: () => studioApi.deleteTopic(topic.id),
    onSuccess: async () => { onClose(); await onChanged(); }});
  const pending = topic.selection_state === "pending";
  return <dialog ref={dialog} className="topic-detail-dialog" aria-label="选题详情" onCancel={e => {e.preventDefault();onClose();}}>
    <header><span>{pending ? "待选建议" : "选题详情"}</span><button type="button" aria-label="关闭选题详情" onClick={onClose}><X size={18} /></button></header>
    <h2>{topic.title}</h2>
    {editing ? <form className="candidate-editor" onSubmit={e => {e.preventDefault();if(title.trim()) save.mutate();}}>
      <label>标题<input required maxLength={240} value={title} onChange={e => setTitle(e.target.value)} /></label>
      <label>切入点<textarea aria-label="切入点" rows={5} maxLength={10000} value={brief} onChange={e => setBrief(e.target.value)} /></label>
      <div><button className="button button-primary" disabled={save.isPending || !title.trim()}>保存</button><button type="button" className="button button-secondary" onClick={() => setEditing(false)}>取消</button></div>
    </form> : <p className="library-brief">{pending ? topic.angle : topic.brief || "尚未补充内容要求。"}</p>}
    {pending && <><p>{topic.rationale}</p><ul>{topic.sources.map((s,i) => <li key={i}><a href={s.url} target="_blank" rel="noreferrer">{s.title} ↗</a></li>)}</ul></>}
    <div className="topic-detail-actions">
      {pending ? <button type="button" className="button button-primary" disabled={topic.stale || !topic.available_actions.includes("prepare_topic_selection")} onClick={() => onChoose(topic)}>挑选本批次</button>
        : <>{topic.existing_run_id && <Link className="button button-secondary" to={`/runs/${topic.existing_run_id}?return=${encodeURIComponent(returnUrl)}`} onClick={onOpenRun}>查看内容与生产记录</Link>}
          {(topic.available_actions.includes("start") || topic.available_actions.includes("resume")) && startButton(topic)}
          <button type="button" className="button button-secondary" onClick={() => setEditing(true)}>编辑</button>
          {!topic.existing_run_id && <button type="button" className="button button-quiet" disabled={remove.isPending} onClick={() => confirmDelete ? remove.mutate() : setConfirmDelete(true)}>{confirmDelete ? "确认删除" : "删除选题"}</button>}
        </>}
      {onDiscuss && <button type="button" className="button button-quiet" onClick={() => {onClose();onDiscuss(topic.title);}}>与 Agent 讨论</button>}
    </div>
    {save.error || remove.error ? <p role="alert" className="form-error">{(save.error ?? remove.error)?.message}</p> : null}
  </dialog>;
}
