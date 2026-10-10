import { useEffect, useId, useRef, useState, type ReactNode } from "react";
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
  const researchPanelId = useId();
  const [removePromptId, setRemovePromptId] = useState<string | null>(null);
  const [focusAfterClose, setFocusAfterClose] = useState<{ id: string; removed: boolean } | null>(null);
  const removeRequestIds = useRef(new Map<string, string>());
  const state = ["pending", "queued"].includes(params.get("topics") ?? "") ? params.get("topics")! : "all";
  const offsetKey = compact ? `offset-${seriesId}` : "offset";
  const rawOffset = Number(params.get(offsetKey) ?? 0);
  const offset = Number.isSafeInteger(rawOffset) && rawOffset >= 0 ? rawOffset : 0;
  const selecting = !compact && !!params.get("research");
  const query = useQuery({ queryKey: ["topics", seriesId, "library", state, offset],
    queryFn: () => request<PageResponse<LibraryTopic>>(`/api/series/${encodeURIComponent(seriesId)}/topic-library?state=${state}&offset=${offset}&limit=20`),
    refetchInterval: q => q.state.data?.items.some(t => t.selection_state === "queued" &&
      ["queued", "producing", "validating"].includes(t.existing_run_status ?? "")) ? 3000 : false,
    retry: false });
  const change = (key: string, value: string) => setParams(p => {
    const next = new URLSearchParams(p); next.set(key, value); next.delete("topic"); return next;
  });
  const openDetail = (id: string, removing = false) => {
    setRemovePromptId(removing ? id : null);
    setParams(p => { const next = new URLSearchParams(p); next.set("topic", id); return next; });
  };
  const closeDetail = (id: string, removed = false) => {
    const next = new URLSearchParams(window.location.search);
    if (next.get("topic") !== id) return;
    next.delete("topic");
    setFocusAfterClose({ id, removed });
    setParams(next);
    setRemovePromptId(current => current === id ? null : current);
  };
  useEffect(() => {
    if (!focusAfterClose || params.get("topic")) return;
    const frame = requestAnimationFrame(() => {
      (focusAfterClose.removed ? document.getElementById(`topic-library-${seriesId}-${compact ? "compact" : "main"}`)
        : document.getElementById(`topic-card-${focusAfterClose.id}`))?.focus();
      setFocusAfterClose(null);
    });
    return () => cancelAnimationFrame(frame);
  }, [focusAfterClose, params, seriesId, compact]);
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
    <button className="button button-secondary" onClick={() => setParams(p => { const next = new URLSearchParams(p); for (const key of ["select", "research", "operation"]) next.delete(key); return next; })}>← 返回选题库</button>
    <TopicResearchPanel key={seriesId} seriesId={seriesId} />
  </section>;
  return <section id={`topic-library-${seriesId}-${compact ? "compact" : "main"}`} tabIndex={-1}
    className={`topic-library${compact ? " compact-library" : ""}`} aria-label="选题库">
    <header className="library-heading">{!compact && <h2>内容</h2>}
      {!compact && <button type="button" className="button button-secondary library-research-trigger" aria-expanded={researchOpen}
        aria-controls={researchPanelId} onClick={() => setResearchOpen(value => !value)}>{researchOpen ? "收起调研" : "调研选题"}</button>}
      <span className="library-count">{query.data?.page.total ?? "—"} 项</span>
      <button type="button" className="library-refresh" aria-label="刷新选题库" disabled={query.isFetching} onClick={() => void query.refetch()}><RefreshCw size={14} /></button>
    </header>
    {!compact && <LibraryFilters />}
    {!compact && researchOpen && <div id={researchPanelId} className="library-research"><TopicResearchPanel seriesId={seriesId} controlsOnly /></div>}
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
            {pending ? <span className="library-pending">待选</span>
            : <StatusPill status={topic.existing_run_status ?? topic.status} />}
          {!pending && topic.card_count ? <small>{topic.card_count} 张</small> : null}
          {!pending && (topic.available_actions.includes("start") || topic.available_actions.includes("resume")) && startButton(topic)}
          <details className="content-card-menu" onKeyDown={e => { if (e.key === "Escape") { e.currentTarget.open = false; e.currentTarget.querySelector("summary")?.focus(); } }}>
            <summary aria-label={`选题操作 ${topic.title}`}><MoreHorizontal size={18} /></summary>
            <div><button type="button" onClick={e => { e.currentTarget.closest("details")?.removeAttribute("open"); openDetail(topic.id); }}>详情与编辑</button>
              <button type="button" onClick={e => { e.currentTarget.closest("details")?.removeAttribute("open"); openDetail(topic.id, true); }}>移除</button>
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
    {detail && <TopicDetail key={detail.id} topic={detail} onClose={() => closeDetail(detail.id)}
      onRemoved={() => { removeRequestIds.current.delete(detail.id); closeDetail(detail.id, true); }}
      requestId={() => {
        const existing = removeRequestIds.current.get(detail.id);
        if (existing) return existing;
        const created = crypto.randomUUID().replaceAll("-", "");
        removeRequestIds.current.set(detail.id, created);
        return created;
      }}
      startRemoving={removePromptId === detail.id} onChoose={chooseBatch}
      returnUrl={detailReturnUrl} onOpenRun={() => rememberReturn(detail.id, detailReturnUrl)}
      startButton={startButton} onDiscuss={onDiscuss} onChanged={async () => {
        await Promise.all([client.invalidateQueries({queryKey:["topics"]}), client.invalidateQueries({queryKey:["series-all"]})]);
      }} onRemovedChanged={async () => {
        await Promise.all([["topics"], ["series-all"], ["creators"], ["overview"], ["creator-tasks"],
          ["research-history", seriesId], ["research"]].map(queryKey => client.invalidateQueries({ queryKey })));
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

function TopicDetail({ topic, onClose, onRemoved, requestId, startRemoving, onChoose, startButton, onDiscuss, onChanged, onRemovedChanged, returnUrl, onOpenRun }: {
  topic: LibraryTopic; onClose: () => void; onChoose: (topic: PendingTopic) => void;
  onRemoved: () => void; requestId: () => string; startRemoving: boolean;
  returnUrl: string; onOpenRun: () => void;
  startButton: (topic: TopicView) => ReactNode; onDiscuss?: (title: string) => void;
  onChanged: () => Promise<void>; onRemovedChanged: () => Promise<void>;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [title, setTitle] = useState(topic.title);
  const [brief, setBrief] = useState(topic.selection_state === "queued" ? topic.brief ?? "" : topic.angle);
  const [editing, setEditing] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(startRemoving);
  useEffect(() => { dialog.current?.showModal(); }, []);
  useEffect(() => { if (startRemoving) setConfirmRemove(true); }, [startRemoving]);
  const save = useMutation({retry:false, mutationFn: () => studioApi.editTopic(topic.id, {title:title.trim(),brief:brief || null}),
    onSuccess: async () => { setEditing(false); await onChanged(); }});
  const remove = useMutation({retry:false, mutationFn: () => studioApi.removeTopic(topic.id, {
    request_id: requestId(), ...(topic.selection_state === "pending"
      ? { batch_id: topic.batch_id, candidate_id: topic.candidate_id } : {}),
  }), onSuccess: async () => { onRemoved(); await onRemovedChanged(); }});
  const pending = topic.selection_state === "pending";
  return <dialog ref={dialog} className="topic-detail-dialog" aria-label="选题详情" onCancel={e => {e.preventDefault();onClose();}}>
    <header><span>{pending ? "待选建议" : "选题详情"}</span><button type="button" aria-label="关闭选题详情" onClick={onClose}><X size={18} /></button></header>
    <h2>{topic.title}</h2>
    {editing ? <form className="candidate-editor" onSubmit={e => {e.preventDefault();if(title.trim() && !remove.isPending) save.mutate();}}>
      <label>标题<input required maxLength={240} value={title} onChange={e => setTitle(e.target.value)} /></label>
      <label>切入点<textarea aria-label="切入点" rows={5} maxLength={10000} value={brief} onChange={e => setBrief(e.target.value)} /></label>
      <div><button className="button button-primary" disabled={save.isPending || remove.isPending || !title.trim()}>保存</button><button type="button" className="button button-secondary" onClick={() => setEditing(false)}>取消</button></div>
    </form> : <p className="library-brief">{pending ? topic.angle : topic.brief || "尚未补充内容要求。"}</p>}
    {pending && <><p>{topic.rationale}</p><ul>{topic.sources.map((s,i) => <li key={i}><a href={s.url} target="_blank" rel="noreferrer">{s.title} ↗</a></li>)}</ul></>}
    {pending && topic.stale && <p className="form-error" role="alert">当前栏目或 Skill 不可用，请先修复配置；选题没有过期。</p>}
    <div className="topic-detail-actions">
      {pending ? <button type="button" className="button button-primary" disabled={remove.isPending || topic.stale || !topic.available_actions.includes("prepare_topic_selection")} onClick={() => onChoose(topic)}>挑选本批次</button>
        : <>{topic.existing_run_id && <Link className="button button-secondary" to={`/runs/${topic.existing_run_id}?return=${encodeURIComponent(returnUrl)}`} onClick={onOpenRun}>查看内容与生产记录</Link>}
          {!remove.isPending && (topic.available_actions.includes("start") || topic.available_actions.includes("resume")) && startButton(topic)}
          <button type="button" className="button button-secondary" disabled={remove.isPending} onClick={() => setEditing(true)}>编辑</button>
        </>}
      <button type="button" className="button button-quiet" disabled={remove.isPending || save.isPending} onClick={() => setConfirmRemove(true)}>移除</button>
      {onDiscuss && <button type="button" className="button button-quiet" disabled={remove.isPending} onClick={() => {onClose();onDiscuss(topic.title);}}>与 Agent 讨论</button>}
    </div>
    {confirmRemove && <div className="topic-remove-confirm" role="group" aria-label="确认移除选题">
      <p>{pending ? "移除这条待选建议？移除后会从候选列表隐藏。" : topic.existing_run_id
        ? "移除这张内容卡片？已有产物、生产记录和 Trace 会保留，仍可从原 Run 查看。"
        : "移除这条未生产选题？它会从内容列表删除。"}</p>
      <div><button type="button" className="button button-quiet" disabled={remove.isPending || save.isPending} onClick={() => remove.mutate()}>
        {remove.isPending ? "正在移除…" : remove.isError ? "重试移除" : "确认移除"}
      </button><button type="button" className="button button-secondary" disabled={remove.isPending} onClick={() => { remove.reset(); setConfirmRemove(false); }}>取消</button></div>
    </div>}
    {save.error || remove.error ? <p role="alert" className="form-error">{(save.error ?? remove.error)?.message}</p> : null}
  </dialog>;
}
