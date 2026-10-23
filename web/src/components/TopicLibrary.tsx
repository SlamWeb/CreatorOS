import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { ChevronLeft, ChevronRight, ListChecks, MoreHorizontal, RefreshCw, Search, X } from "lucide-react";
import { apiUrl, request, studioApi } from "../api/client";
import type { PageResponse, TopicSkillUsage, TopicView } from "../api/types";
import { StatusPill } from "./StatusPill";
import { TopicResearchPanel } from "./TopicResearchPanel";
import "./topic-library.css";

type PendingTopic = { id: string; title: string; selection_state: "pending"; batch_id: string;
  candidate_id: string; angle: string; rationale: string; stale: boolean;
  sources: { title: string; url: string }[]; available_actions: string[];
  production_state?: "unproduced" | "produced"; skill_usage?: TopicSkillUsage };
type LibraryTopic = PendingTopic | (TopicView & { selection_state: "queued" });

function TopicSkills({ topic }: { topic: LibraryTopic }) {
  const usage = topic.skill_usage;
  if (!usage) return null;
  const label = usage.source === "external" ? "外部导入" : usage.source === "series" ? "将用"
    : topic.production_state === "produced" ? "已用" : "任务用";
  return <div className="topic-skill-usage" aria-label="生产使用的 Skill"
    title={usage.source === "series" ? "栏目当前绑定版本，提交生产时确定" : "本次任务的版本，继续生产不会换成栏目新版"}>
    <span>{label}</span>
    {usage.items.map(skill => <Link key={skill.id} to={`/skills?skill=${encodeURIComponent(skill.id)}`}>
      <span>{skill.name}</span><small>{skill.version_number ? `v${skill.version_number}${skill.version_status === "candidate" ? " · 候选" : ""}`
        : skill.id === "knowledge-to-carousel" ? "内置" : "版本未知"}</small>
    </Link>)}
    {!usage.items.length && usage.source !== "external" && <span>未记录 Skill</span>}
  </div>;
}

export function TopicLibrary({ seriesId, startButton, compact = false, onDiscuss, productionEnabled = false,
  heading, headerActions, headerPanels, onViewAll }: {
  seriesId: string; startButton: (topic: TopicView) => ReactNode; compact?: boolean;
  onDiscuss?: (title: string, topic?: TopicView) => void; productionEnabled?: boolean;
  heading?: ReactNode; headerActions?: ReactNode; headerPanels?: ReactNode;
  onViewAll?: () => void;
}) {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const client = useQueryClient();
  const [researchOpen, setResearchOpen] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [pendingSelection, setPendingSelection] = useState<string[]>([]);
  const pendingSubmitting = useRef(false);
  const pendingRequestIds = useRef(new Map<string, string>());
  const submitting = useRef(false);
  const researchPanelId = useId();
  const libraryRef = useRef<HTMLElement>(null);
  const pageNavigation = useRef(false);
  const [removePromptId, setRemovePromptId] = useState<string | null>(null);
  const [focusAfterClose, setFocusAfterClose] = useState<{ id: string; removed: boolean } | null>(null);
  const removeRequestIds = useRef(new Map<string, string>());
  const state = "all";
  const order = "created";
  const limit = compact ? 6 : 20;
  const offsetKey = compact ? `offset-${seriesId}` : "offset";
  const rawOffset = Number(params.get(offsetKey) ?? 0);
  const offset = !compact && Number.isSafeInteger(rawOffset) && rawOffset >= 0 ? rawOffset : 0;
  const selecting = !compact && !!params.get("research");
  const query = useQuery({ queryKey: ["topics", seriesId, "library", state, offset, order, limit],
    queryFn: () => request<PageResponse<LibraryTopic>>(`/api/series/${encodeURIComponent(seriesId)}/topic-library?state=${state}&offset=${offset}&limit=${limit}&order=${order}`),
    refetchInterval: q => q.state.data?.items.some(t => t.selection_state === "queued" &&
      ["queued", "producing", "validating"].includes(t.existing_run_status ?? "")) ? 3000 : false,
    retry: false });
  const eligible = query.data?.items.filter((topic): topic is TopicView & { selection_state: "queued" } =>
    topic.selection_state === "queued" && topic.available_actions.includes("start")) ?? [];
  const selectedTopics = eligible.filter(topic => selected.includes(topic.id));
  const pendingTopics = query.data?.items.filter((topic): topic is PendingTopic => topic.selection_state === "pending"
    && !topic.stale && topic.available_actions.includes("prepare_topic_selection")) ?? [];
  const selectedPending = pendingTopics.filter(topic => pendingSelection.includes(topic.id));
  const pendingQueue = useMutation({
    retry: false,
    mutationFn: async (topics: PendingTopic[]) => {
      const groups = new Map<string, PendingTopic[]>();
      for (const topic of topics) groups.set(topic.batch_id, [...(groups.get(topic.batch_id) ?? []), topic]);
      const accepted: string[] = [];
      const failures: string[] = [];
      for (const [batchId, items] of groups) {
        const selections = items.map(topic => ({ candidate_id: topic.candidate_id, title: topic.title, angle: topic.angle }));
        const key = JSON.stringify([batchId, selections]);
        const requestId = pendingRequestIds.current.get(key) ?? crypto.randomUUID().replaceAll("-", "");
        pendingRequestIds.current.set(key, requestId);
        try {
          await request(`/api/topic-research/${encodeURIComponent(batchId)}/queue`, {
            method: "POST", body: JSON.stringify({ selections, request_id: requestId }),
          });
          accepted.push(...items.map(topic => topic.id));
        } catch (error) {
          failures.push(`${items[0].title}等 ${items.length} 项：${error instanceof Error ? error.message : "入队结果未知"}`);
        }
      }
      return { accepted, failures };
    },
    onSuccess: result => setPendingSelection(current => current.filter(id => !result.accepted.includes(id))),
    onSettled: async () => {
      try {
        await Promise.all([["topics"], ["research"], ["research-history", seriesId], ["series", seriesId],
          ["series-all"], ["overview"], ["creator-tasks"]].map(queryKey => client.invalidateQueries({ queryKey })));
      } finally { pendingSubmitting.current = false; }
    },
  });
  const submitPending = () => {
    if (pendingSubmitting.current || !selectedPending.length || query.isError) return;
    pendingSubmitting.current = true;
    pendingQueue.mutate(selectedPending);
  };
  const batch = useMutation({
    retry: false,
    mutationFn: (topics: { id: string; title: string }[]) => studioApi.batchProduction(topics.map(topic => topic.id)),
    onSuccess: result => {
      const accepted = new Set(result.items.filter(item => item.accepted).map(item => item.topic_id));
      setSelected(current => current.filter(id => !accepted.has(id)));
    },
    onSettled: async () => {
      try {
        await Promise.all(["topics", "series", "overview", "runs", "series-all", "creator-tasks"].map(key =>
          client.invalidateQueries({ queryKey: key === "topics" || key === "series" ? [key, seriesId] : [key] })));
      } finally { submitting.current = false; }
    },
  });
  useEffect(() => { setSelected([]); setPendingSelection([]); batch.reset(); pendingQueue.reset(); }, [seriesId, state, offset, order]);
  useEffect(() => {
    if (!pageNavigation.current || query.isPending) return;
    pageNavigation.current = false;
    libraryRef.current?.scrollIntoView({ block: "start" });
    libraryRef.current?.focus({ preventScroll: true });
  }, [offset, query.isPending]);
  const submitBatch = () => {
    if (submitting.current || !productionEnabled || !selectedTopics.length) return;
    submitting.current = true;
    batch.mutate(selectedTopics.map(topic => ({ id: topic.id, title: topic.title })));
  };
  const changePage = (value: number) => {
    pageNavigation.current = true;
    setParams(p => {
      const next = new URLSearchParams(p); next.set(offsetKey, String(value)); next.delete("topic"); return next;
    });
  };
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
  const pagination = (position: string) => !compact && query.data && (query.data.page.total > limit || offset > 0)
    ? <nav className="library-pagination" aria-label={`选题分页 · ${position}`}>
      <button type="button" aria-label="上一页" disabled={!offset || query.isFetching}
        onClick={() => changePage(Math.max(0, offset - limit))}><ChevronLeft size={16} /></button>
      <label><span className="sr-only">选题页码 · {position}</span>
        <select aria-label={`选题页码 · ${position}`} value={Math.floor(offset / limit)}
          disabled={query.isFetching} onChange={event => changePage(Number(event.target.value) * limit)}>
          {Array.from({ length: Math.max(Math.floor(offset / limit) + 1, Math.ceil(query.data.page.total / limit), 1) },
            (_, index) => <option key={index} value={index}>第 {index + 1} 页</option>)}
        </select>
      </label>
      <span>/ {Math.max(1, Math.ceil(query.data.page.total / limit))}</span>
      <button type="button" aria-label="下一页" disabled={query.isFetching || offset + limit >= query.data.page.total}
        onClick={() => changePage(offset + limit)}><ChevronRight size={16} /></button>
    </nav> : null;
  return <section ref={libraryRef} id={`topic-library-${seriesId}-${compact ? "compact" : "main"}`} tabIndex={-1}
    className={`topic-library${compact ? " compact-library" : ""}`} aria-label="选题库">
    {!compact && <header className="library-heading">{heading ?? <h2>内容</h2>}
      <div className="library-heading-actions"><button type="button" className="button button-primary library-research-trigger" aria-expanded={researchOpen}
        aria-controls={researchPanelId} onClick={() => setResearchOpen(value => !value)}><Search size={16} aria-hidden="true" />{researchOpen ? "收起调研" : "调研选题"}</button>
      {headerActions}</div>
    </header>}
    {headerPanels}
    <div className="library-browse-toolbar">
      <div className="library-browse-actions">
      <span className="library-count">{query.data?.page.total ?? "—"} 项</span>
      <button type="button" className="library-refresh" aria-label="刷新选题库" disabled={query.isFetching} onClick={() => void query.refetch()}><RefreshCw size={14} /></button>
      {compact && onViewAll && <button type="button" className="library-view-all" onClick={onViewAll}>查看全部<ChevronRight size={14} aria-hidden="true" /></button>}
      {!compact && !query.isError && (!!pendingTopics.length || (productionEnabled && !!eligible.length)) && <details className="library-select-menu" onKeyDown={event => {
        if (event.key === "Escape") { event.currentTarget.open = false; event.currentTarget.querySelector("summary")?.focus(); }
      }}><summary><ListChecks size={15} aria-hidden="true" />选择</summary><div>
        {!!pendingTopics.length && <button type="button" disabled={pendingQueue.isPending || batch.isPending} onClick={event => {
          setSelected([]); setPendingSelection(pendingTopics.map(topic => topic.id));
          const menu = event.currentTarget.closest("details"); menu?.removeAttribute("open"); menu?.querySelector("summary")?.focus();
        }}>全选本页待选</button>}
        {productionEnabled && !!eligible.length && <button type="button" disabled={pendingQueue.isPending || batch.isPending} onClick={event => {
          setPendingSelection([]); setSelected(eligible.map(topic => topic.id));
          const menu = event.currentTarget.closest("details"); menu?.removeAttribute("open"); menu?.querySelector("summary")?.focus();
        }}>全选本页可生产选题</button>}
      </div></details>}
      </div>
    </div>
    {pagination("顶部")}
    {!compact && researchOpen && <div id={researchPanelId} className="library-research"><TopicResearchPanel seriesId={seriesId} controlsOnly /></div>}
    {pendingQueue.data && <div className="library-batch-result" aria-label="入队结果">
      {!!pendingQueue.data.accepted.length && <p role="status">已入队 {pendingQueue.data.accepted.length} 项。</p>}
      {!!pendingQueue.data.failures.length && <div role="alert" className="form-error">{pendingQueue.data.failures.map(message => <p key={message}>{message}</p>)}
        请先刷新核对；未确认成功的选择已保留，不会自动重提。</div>}
    </div>}
    {batch.isError && <p className="form-error" role="alert">{batch.error.message} 请先刷新核对任务状态；选择已保留，不会自动重提。</p>}
    {batch.data && <section className="library-batch-result" aria-label="批量提交结果">
      <p role="status">{batch.data.accepted_count > 0 ? `已受理 ${batch.data.accepted_count} 项，后台依次生产。` : "本次没有任务被受理。"}</p>
      <ul>{batch.data.items.map((item, index) => <li key={`${item.topic_id}-${index}`}>
        {item.run_id ? <Link to={`/runs/${encodeURIComponent(item.run_id)}`}>{item.topic_title ?? batch.variables?.find(topic => topic.id === item.topic_id)?.title ?? "查看任务"}</Link>
          : <span>{item.topic_title ?? batch.variables?.find(topic => topic.id === item.topic_id)?.title ?? "选题"}</span>}
        <span className={item.accepted ? "batch-accepted" : "form-error"}>{item.accepted ? "已受理" : item.error ?? "未受理，请查看任务状态"}</span>
      </li>)}</ul>
    </section>}
    {query.isPending && <p role="status">正在读取内容…</p>}
    {query.error && <p role="alert" className="form-error">{query.error.message}<button onClick={() => void query.refetch()}>重新读取选题库</button></p>}
    {!query.isPending && !query.error && !query.data?.items.length && <p className="research-empty">{offset ? "这一页没有选题，请返回上一页。" : "还没有选题。"}</p>}
    {!query.error && <div className="content-feed">{query.data?.items.map(topic => {
      const pending = topic.selection_state === "pending";
      const content = <>{!pending && topic.cover_url && <CardCover key={topic.cover_url} url={topic.cover_url} title={topic.title} />}
        <div className="content-card-copy"><h3>{topic.title}</h3>
          {(pending ? topic.angle : topic.brief) && <p>{pending ? topic.angle : topic.brief}</p>}
        </div></>;
      return <article className={`content-card${selected.includes(topic.id) || (pending && pendingSelection.includes(topic.id)) ? " is-selected" : ""}`} key={topic.id} data-testid={`library-${topic.id}`}>
        {!pending && topic.existing_run_id
          ? <Link id={`topic-card-${topic.id}`} className="content-card-main" aria-label={`查看内容 ${topic.title}`}
              to={`/runs/${topic.existing_run_id}?return=${encodeURIComponent(location.pathname + location.search)}`}
              onClick={() => rememberReturn(topic.id)}>{content}</Link>
          : <button id={`topic-card-${topic.id}`} type="button" className="content-card-main" aria-label={`查看选题 ${topic.title}`}
              onClick={() => openDetail(topic.id)}>{content}</button>}
        <TopicSkills topic={topic} />
        <footer className="content-card-footer">
          {pending && !topic.stale && topic.available_actions.includes("prepare_topic_selection") && <input className="content-card-select" type="checkbox"
            aria-label={`选择入队 ${topic.title}`} checked={pendingSelection.includes(topic.id)} disabled={pendingQueue.isPending || batch.isPending}
            onChange={event => { if (event.target.checked) setSelected([]); setPendingSelection(current => event.target.checked ? [...current, topic.id] : current.filter(id => id !== topic.id)); }} />}
          {productionEnabled && !pending && topic.available_actions.includes("start") && <input className="content-card-select" type="checkbox"
            aria-label={`选择生产 ${topic.title}`} checked={selected.includes(topic.id)} disabled={batch.isPending || pendingQueue.isPending}
            onChange={event => { if (event.target.checked) setPendingSelection([]); setSelected(current => event.target.checked ? [...current, topic.id] : current.filter(id => id !== topic.id)); }} />}
            {pending ? <span className="library-pending">待确认</span>
            : <StatusPill status={topic.production_queue_status === "pending" ? "production_pending"
              : topic.production_queue_status === "needs_attention" ? "needs_attention" : topic.existing_run_status ?? topic.status} />}
          {!pending && topic.production_queue_status === "pending" && topic.production_queue_position
            ? <small>第 {topic.production_queue_position} 项</small> : null}
          {!pending && topic.card_count ? <small>{topic.card_count} 张</small> : null}
          {!pending && (topic.available_actions.includes("start") || topic.available_actions.includes("resume")) && startButton(topic)}
          <details className="content-card-menu" onKeyDown={e => { if (e.key === "Escape") { e.currentTarget.open = false; e.currentTarget.querySelector("summary")?.focus(); } }}>
            <summary aria-label={`选题操作 ${topic.title}`}><MoreHorizontal size={18} /></summary>
            <div><button type="button" onClick={e => { e.currentTarget.closest("details")?.removeAttribute("open"); openDetail(topic.id); }}>详情与编辑</button>
              <button type="button" className="action-danger" onClick={e => { e.currentTarget.closest("details")?.removeAttribute("open"); openDetail(topic.id, true); }}>移除</button>
              {onDiscuss && <button type="button" onClick={e => { e.currentTarget.closest("details")?.removeAttribute("open"); onDiscuss(topic.title, topic.selection_state === "queued" ? topic : undefined); }}>与 Agent 讨论</button>}</div>
          </details>
        </footer>
      </article>;
    })}</div>}
    {pagination("底部")}
    {(selectedPending.length > 0 || pendingQueue.isPending || (productionEnabled && (selectedTopics.length > 0 || batch.isPending))) && <div className="library-selection-dock">
      {(selectedPending.length > 0 || pendingQueue.isPending) ? <div className="library-batch-toolbar" role="group" aria-label="批量入队">
        <label><input type="checkbox" aria-label="全选本页待选" disabled={pendingQueue.isPending || query.isError}
          checked={!!pendingTopics.length && pendingTopics.every(topic => pendingSelection.includes(topic.id))}
          ref={element => { if (element) element.indeterminate = selectedPending.length > 0 && selectedPending.length < pendingTopics.length; }}
          onChange={event => setPendingSelection(event.target.checked ? pendingTopics.map(topic => topic.id) : [])} />全选本页待选</label>
        <span>已选 {pendingQueue.isPending ? pendingQueue.variables?.length ?? selectedPending.length : selectedPending.length} 项</span>
        <button type="button" className="button button-secondary" disabled={pendingQueue.isPending} onClick={() => setPendingSelection([])}>取消选择</button>
        <button type="button" className="button button-primary" disabled={!selectedPending.length || pendingQueue.isPending || query.isError} onClick={submitPending}>
          {pendingQueue.isPending ? "正在入队…" : `确认入队（${selectedPending.length}）`}</button>
      </div> : <div className="library-batch-toolbar" role="group" aria-label="批量生产">
        <label><input type="checkbox" aria-label="全选本页可生产选题" disabled={batch.isPending || query.isError}
          checked={!!eligible.length && eligible.every(topic => selected.includes(topic.id))}
          ref={element => { if (element) element.indeterminate = selectedTopics.length > 0 && selectedTopics.length < eligible.length; }}
          onChange={event => setSelected(event.target.checked ? eligible.map(topic => topic.id) : [])} />全选本页</label>
        <span>已选 {batch.isPending ? batch.variables?.length ?? selectedTopics.length : selectedTopics.length} 项</span>
        <button type="button" className="button button-secondary" disabled={batch.isPending} onClick={() => setSelected([])}>取消选择</button>
        <button type="button" className="button button-primary" disabled={!selectedTopics.length || batch.isPending || query.isError} onClick={submitBatch}>
          {batch.isPending ? "正在提交…" : `批量生产（${selectedTopics.length}）`}</button>
      </div>}
    </div>}
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

function CardCover({ url, title }: {url: string; title: string}) {
  const [failed, setFailed] = useState(false);
  return failed ? <p className="cover-unavailable">封面暂不可读取</p>
    : <img className="content-card-cover" src={apiUrl(url)} alt={`${title} · 第一张图片`} loading="lazy" onError={() => setFailed(true)} />;
}

function TopicDetail({ topic, onClose, onRemoved, requestId, startRemoving, onChoose, startButton, onDiscuss, onChanged, onRemovedChanged, returnUrl, onOpenRun }: {
  topic: LibraryTopic; onClose: () => void; onChoose: (topic: PendingTopic) => void;
  onRemoved: () => void; requestId: () => string; startRemoving: boolean;
  returnUrl: string; onOpenRun: () => void;
  startButton: (topic: TopicView) => ReactNode; onDiscuss?: (title: string, topic?: TopicView) => void;
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
    <TopicSkills topic={topic} />
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
      <button type="button" className="button button-danger" disabled={remove.isPending || save.isPending} onClick={() => setConfirmRemove(true)}>移除</button>
      {onDiscuss && <button type="button" className="button button-quiet" disabled={remove.isPending} onClick={() => {onClose();onDiscuss(topic.title, topic.selection_state === "queued" ? topic : undefined);}}>与 Agent 讨论</button>}
    </div>
    {confirmRemove && <div className="topic-remove-confirm" role="group" aria-label="确认移除选题">
      <p>{pending ? "移除这条待选建议？移除后会从候选列表隐藏。" : topic.existing_run_id
        ? "移除这张内容卡片？已有产物、生产记录和 Trace 会保留，仍可从原 Run 查看。"
        : "移除这条未生产选题？它会从内容列表删除。"}</p>
      <div><button type="button" className="button button-danger" disabled={remove.isPending || save.isPending} onClick={() => remove.mutate()}>
        {remove.isPending ? "正在移除…" : remove.isError ? "重试移除" : "确认移除"}
      </button><button type="button" className="button button-secondary" disabled={remove.isPending} onClick={() => { remove.reset(); setConfirmRemove(false); }}>取消</button></div>
    </div>}
    {save.error || remove.error ? <p role="alert" className="form-error">{(save.error ?? remove.error)?.message}</p> : null}
  </dialog>;
}
