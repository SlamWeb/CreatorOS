import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { ArrowDown, ArrowUpRight, ExternalLink, Minus, Plus, RefreshCw } from "lucide-react";
import { observationApi, type ObservationDetail, type ObservationNode } from "../api/observation";
import { ApiError } from "../api/client";
import "./observation.css";

const kinds: Record<string, string> = {
  creator: "账号", series: "栏目", topic: "选题", research: "调研", run: "生产任务", error: "错误诊断",
  revision: "版本", attempt: "执行尝试", codex_turn: "Codex 执行回合", codex_item: "工具 / 回复步骤",
  session: "Agent 对话", user_turn: "一次用户请求", request: "模型请求", tool: "工具调用", tool_call: "工具调用",
  group: "分组", discussion: "版本讨论", extraction: "Skill 提炼", message: "消息", sessions: "对话列表",
  topics: "选题与生产", researches: "选题调研", operations: "选题操作与审批", llm_request: "模型请求",
  worker: "Codex 执行记录", worker_turn: "Codex 执行回合", worker_item: "工具 / 回复步骤",
  research_attempt: "调研执行尝试", discussions: "版本讨论", operation: "选题操作",
  input: "输入", output: "输出", tool_result: "工具结果", agent_message: "公开回复",
  command_execution: "命令执行", file_change: "文件变更", mcp_tool_call: "工具调用",
  host: "宿主记录", business_state: "业务状态", progress: "公开进度", model_request: "模型请求",
  user: "用户指令", assistant: "模型公开回复", missing: "记录缺失", extraction_execution: "Skill 提炼执行",
};
const statuses: Record<string, string> = {
  running: "进行中", pending: "待处理", queued: "排队中", completed: "已完成", succeeded: "已完成",
  failed: "失败", interrupted: "已中断", cancelled: "已取消", ready: "已就绪", partial: "部分记录",
  awaiting_approval: "待批准", approved: "已批准", published: "已发布", idle: "空闲",
  active: "启用", archived: "已归档", unknown: "未知", missing: "记录缺失",
  validated: "已校验", executing: "执行中", started: "已开始", in_progress: "进行中",
  success: "成功", done: "已完成", error: "错误", stopped: "已停止", expired: "已过期",
  draft: "草稿", available: "待选择", selected: "已选择", disabled: "已停用", recorded: "已记录", unavailable: "尚无记录",
};
const fields: Record<string, string> = {
  role: "角色", content: "正文", text: "正文", message: "消息", messages: "消息上下文", input: "输入",
  output: "输出", result: "结果", arguments: "参数", tool_calls: "工具调用", tool_results: "工具结果",
  name: "名称", function: "函数", command: "命令", error: "错误", status: "状态", model: "模型",
  created_at: "创建时间", updated_at: "更新时间", started_at: "开始时间", finished_at: "结束时间",
  usage: "用量", response: "模型回复", request: "请求", prompt: "输入指令", response_text: "公开回复",
};

function Status({ status, kind }: { status?: string | null; kind?: string }) {
  if (!status) return null;
  const tone = /failed|error|interrupted/.test(status) ? "danger" : /running|queued|pending/.test(status) ? "active" : "neutral";
  const label = status === "completed" && (kind === "tool" || kind === "tool_call") ? "调用已返回"
    : status === "idle" && (kind === "user" || kind === "user_turn") ? "本轮已结束" : statuses[status] ?? status;
  return <span className={`observation-status ${tone}`}>{label}</span>;
}

function LongText({ text, code = false }: { text: string; code?: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const long = text.length > 200 || text.split("\n").length > 8;
  const preview = text.slice(0, 200).split("\n").slice(0, 8).join("\n");
  return <div className="observation-long-text">
    <pre className={`${code ? "observation-code" : "observation-prose"} ${long && !expanded ? "observation-text-preview" : ""}`}>{long && !expanded ? `${preview}\n…` : text || "（空正文）"}</pre>
    {long && <button type="button" className="observation-text-action" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>{expanded ? "收起全文" : "展开全文"}<span className="observation-character-count">{text.length.toLocaleString()} 字符</span></button>}
  </div>;
}

function ReadableValue({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (value === null || value === undefined) return <span className="observation-muted">未记录</span>;
  if (typeof value === "string") return <LongText text={value} />;
  if (typeof value !== "object") return <span>{String(value)}</span>;
  if (depth > 4) return <LongText text={JSON.stringify(value, null, 2)} code />;
  if (Array.isArray(value)) return value.length ? <ol className="observation-values">{value.map((entry, index) => <li key={index}><ReadableValue value={entry} depth={depth + 1} /></li>)}</ol> : <span className="observation-muted">暂无记录</span>;
  return <dl className="observation-fields">{Object.entries(value).map(([key, entry]) => <div key={key}><dt>{fields[key] ?? key}</dt><dd><ReadableValue value={entry} depth={depth + 1} /></dd></div>)}</dl>;
}

function Reveal({ label, className, children }: { label: string; className: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return <div className={className} data-open={open}>
    <button className="observation-reveal" type="button" aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}>
      <span>{label}</span>{open ? <Minus size={14} aria-hidden="true" /> : <Plus size={14} aria-hidden="true" />}
    </button>
    <div id={id} hidden={!open}>{open && children}</div>
  </div>;
}

function EventContent({ value }: { value: unknown }) {
  if (value !== null && typeof value === "object") {
    const publicText = !Array.isArray(value) && "text" in value && typeof value.text === "string" ? value.text : null;
    return <>{publicText && <LongText text={publicText} />}<Reveal className="observation-event-payload" label="查看输入 / 输出"><ReadableValue value={value} /></Reveal></>;
  }
  return <ReadableValue value={value} />;
}

function ReadError({ error, retry, label = "重新读取" }: { error: Error; retry: () => void; label?: string }) {
  const missing = error instanceof ApiError && error.status === 404;
  return <div className="observation-error" role="alert"><p>{missing ? "这条记录不存在或已不可用。可从记录树重新选择。" : error.message}</p><button type="button" onClick={retry}>{label}</button></div>;
}

interface TreeProps {
  parent?: string;
  selected: string;
  expanded: Set<string>;
  toggle: (id: string, open: boolean) => void;
  select: (id: string) => void;
  depth?: number;
  path: string[];
}

function TreeBranch({ parent, selected, expanded, toggle, select, depth = 0, path }: TreeProps) {
  const query = useInfiniteQuery({
    queryKey: ["observation-tree", parent ?? "root"], initialPageParam: 0,
    queryFn: ({ pageParam, signal }) => observationApi.tree(parent, pageParam, signal),
    getNextPageParam: page => page.has_more ? page.next_offset ?? undefined : undefined,
    retry: false, staleTime: 30_000, refetchOnWindowFocus: false,
  });
  const nodes = query.data?.pages.flatMap(page => page.items) ?? [];
  const desiredChild = parent ? path[path.indexOf(parent) + 1] : path[0];
  useEffect(() => {
    // A deep link may point beyond the first page. Only load its known ancestor branch.
    if (desiredChild && (!parent || path.includes(parent)) && query.isSuccess && query.hasNextPage && !query.isFetching && !nodes.some(node => node.id === desiredChild)) {
      void query.fetchNextPage();
    }
  }, [desiredChild, parent, path, nodes, query.isSuccess, query.hasNextPage, query.isFetching, query.fetchNextPage]);
  function onKey(event: KeyboardEvent<HTMLButtonElement>, node: ObservationNode) {
    if (event.key === "ArrowRight" && node.has_children) { event.preventDefault(); toggle(node.id, true); }
    if (event.key === "ArrowLeft" && node.has_children && expanded.has(node.id)) { event.preventDefault(); toggle(node.id, false); }
    if (["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      const buttons = Array.from(event.currentTarget.closest(".observation-tree")?.querySelectorAll<HTMLButtonElement>(".observation-node-select") ?? []);
      const index = buttons.indexOf(event.currentTarget);
      const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : Math.max(0, Math.min(buttons.length - 1, index + (event.key === "ArrowDown" ? 1 : -1)));
      buttons[next]?.focus();
    }
  }
  return <div className="observation-branch">
    {query.isPending && <p className="observation-tree-note" role="status">读取记录…</p>}
    {query.isError && <ReadError error={query.error} retry={() => { void query.refetch(); }} label="重试读取记录树" />}
    {query.isSuccess && !nodes.length && <p className="observation-tree-note">{parent ? "暂无下级记录" : "暂无可观察记录。创建账号或产生任务后会出现在这里。"}</p>}
    <ul>{nodes.map(node => <li key={node.id}>
      <div className={`observation-tree-row ${selected === node.id ? "selected" : ""}`} style={{ paddingLeft: Math.min(depth, 8) * 13 + 5 }}>
        {node.has_children ? <button className="observation-node-toggle" type="button" aria-label={`${expanded.has(node.id) ? "收起" : "展开"}${node.label}`} aria-expanded={expanded.has(node.id)} onClick={() => toggle(node.id, !expanded.has(node.id))}>{expanded.has(node.id) ? <Minus size={14} aria-hidden="true" /> : <Plus size={14} aria-hidden="true" />}</button> : <span className="observation-node-leaf" />}
        <button className="observation-node-select" type="button" aria-current={selected === node.id ? "true" : undefined} onKeyDown={event => onKey(event, node)} onClick={() => select(node.id)} title={node.label}><span className="observation-node-label">{node.label}</span><span className="observation-node-kind">{kinds[node.kind] ?? node.kind}</span></button>
        {node.status && <span className={`observation-tree-dot ${/failed|error/.test(node.status) ? "danger" : /running|pending/.test(node.status) ? "active" : ""}`} title={statuses[node.status] ?? node.status} aria-label={statuses[node.status] ?? node.status} />}
      </div>
      {node.has_children && expanded.has(node.id) && <TreeBranch parent={node.id} selected={selected} expanded={expanded} toggle={toggle} select={select} depth={depth + 1} path={path} />}
    </li>)}</ul>
    {query.hasNextPage && <button className="observation-load-more" type="button" disabled={query.isFetchingNextPage} onClick={() => { void query.fetchNextPage(); }}>{query.isFetchingNextPage ? "读取中…" : "加载更多记录"}</button>}
  </div>;
}

function safeBusinessHref(href?: string) {
  if (!href || !href.startsWith("/") || href.startsWith("//") || href.includes("\\")) return null;
  const path = href.split(/[?#]/)[0];
  return path === "/" || /^\/(runs|series)\/[^/]+$/.test(path) || ["/agent", "/skills", "/observation"].includes(path) ? href : null;
}

function DetailContent({ detail, select }: { detail: ObservationDetail; select: (id: string) => void }) {
  const endRef = useRef<HTMLDivElement>(null);
  const followLatest = useRef(false);
  const previousTimeline = useRef<string | null>(null);
  const timeline = detail.timeline ?? [];
  // The selected record remounts this component. Never jump to the end on first open.
  const timelineVersion = JSON.stringify(timeline);
  useLayoutEffect(() => {
    if (previousTimeline.current !== null && previousTimeline.current !== timelineVersion && detail.active && followLatest.current) {
      endRef.current?.scrollIntoView({ block: "end" });
    }
    previousTimeline.current = timelineVersion;
  }, [timelineVersion, detail.active]);
  useEffect(() => {
    const update = () => {
      const bottom = endRef.current?.getBoundingClientRect().bottom;
      followLatest.current = bottom !== undefined && bottom >= 0 && bottom <= window.innerHeight + 100;
    };
    update();
    window.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    return () => { window.removeEventListener("scroll", update); window.removeEventListener("resize", update); };
  }, []);
  const goLatest = () => { followLatest.current = true; endRef.current?.scrollIntoView({ block: "end" }); };
  return <>
    <nav className="observation-breadcrumbs" aria-label="记录路径">{(detail.breadcrumbs ?? detail.ancestors)?.filter(item => item.id !== detail.id).map(item => <span key={item.id}><button type="button" onClick={() => select(item.id)}>{item.label}</button><span aria-hidden="true">/</span></span>)}</nav>
    <header className="observation-detail-header"><h2>{detail.label}</h2><div className="observation-detail-meta"><span>{kinds[detail.kind] ?? detail.kind}</span><Status status={detail.status} kind={detail.kind} /></div></header>
    {!!detail.warnings.length && <div className="observation-warnings" role="note">{detail.warnings.map((warning, index) => <p key={index}>{warning}</p>)}</div>}
    {!!detail.links.length && <nav className="observation-links" aria-label="关联记录">{detail.links.map((link, index) => link.node_id ? <button key={index} type="button" onClick={() => select(link.node_id!)}>{link.label}<ArrowUpRight size={13} aria-hidden="true" /></button> : safeBusinessHref(link.href) ? <Link key={index} to={safeBusinessHref(link.href)!}>{link.label}<ExternalLink size={13} aria-hidden="true" /></Link> : <span key={index}>{link.label}（无可用入口）</span>)}</nav>}
    <section className="observation-timeline-section" aria-label="公开时间线">
      <div className="observation-section-title"><h3>公开时间线</h3><button type="button" className="observation-text-action" onClick={goLatest}><ArrowDown size={13} />回到最新</button></div>
      {!timeline.length && <p className="observation-empty-timeline">暂无时间线，可查看下级记录或原始记录。</p>}
      <ol className="observation-timeline">{timeline.map((item, index) => <li key={item.id || `${item.kind}-${index}`} className={item.status === "failed" ? "failed" : ""}><span className="observation-timeline-marker" aria-hidden="true" /><article>
        <header><span className="observation-event-kind">{kinds[item.kind] ?? item.kind}</span>{item.at && <time dateTime={item.at}>{new Date(item.at).toString() === "Invalid Date" ? item.at : new Date(item.at).toLocaleString("zh-CN", { hour12: false })}</time>}<Status status={item.status} kind={item.kind} /></header>
        <h4>{item.node_id ? <button className="observation-event-link" type="button" onClick={() => select(item.node_id!)}>{item.label}<ArrowUpRight size={13} aria-hidden="true" /></button> : item.label}</h4>
        {item.content !== undefined && <EventContent value={item.content} />}
      </article></li>)}</ol><div ref={endRef} className="observation-timeline-end" />
    </section>
    <section className="observation-records" aria-label="原始记录"><h3>原始记录</h3>{detail.sections.map((section, index) => <Reveal key={`${detail.id}-${section.title}-${index}`} className="observation-record" label={section.title}><div className="observation-record-content"><ReadableValue value={section.content} /><Reveal className="observation-raw-json" label="查看 JSON"><LongText text={JSON.stringify(section.content, null, 2) ?? "null"} code /></Reveal></div></Reveal>)}</section>
    <Reveal className="observation-identifiers" label="记录标识"><code>{detail.id}</code></Reveal>
  </>;
}

export function ObservationPage() {
  const [params, setParams] = useSearchParams();
  const selected = params.get("node") ?? "";
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [visible, setVisible] = useState(document.visibilityState !== "hidden");
  const [showTree, setShowTree] = useState(true);
  const queryClient = useQueryClient();
  const detail = useQuery({
    queryKey: ["observation-detail", selected], enabled: !!selected,
    queryFn: ({ signal }) => observationApi.detail(selected, signal), retry: false,
    refetchInterval: query => visible && query.state.status !== "error" && query.state.data?.active ? 4_000 : false,
    refetchIntervalInBackground: false, refetchOnWindowFocus: false,
  });
  useEffect(() => {
    const changed = () => setVisible(document.visibilityState !== "hidden");
    document.addEventListener("visibilitychange", changed);
    return () => document.removeEventListener("visibilitychange", changed);
  }, []);
  useEffect(() => {
    const path = detail.data?.breadcrumbs ?? detail.data?.ancestors;
    if (!path) return;
    setExpanded(previous => new Set([...previous, ...path.filter(item => item.id !== selected).map(item => item.id)]));
  }, [detail.data?.id, detail.data?.breadcrumbs, detail.data?.ancestors, selected]);
  const select = (id: string) => setParams(previous => { previous.set("node", id); return previous; });
  const toggle = (id: string, open: boolean) => setExpanded(previous => { const next = new Set(previous); if (open) next.add(id); else next.delete(id); return next; });
  const refresh = () => { void queryClient.invalidateQueries({ queryKey: ["observation-tree"] }); if (selected) void detail.refetch(); };
  const path = detail.data ? [...(detail.data.breadcrumbs ?? detail.data.ancestors ?? []).map(item => item.id), detail.data.id] : [];
  return <div className="observation-page">
    <header className="observation-page-header"><h1>Observation</h1><button type="button" className="observation-refresh" onClick={refresh} disabled={detail.isFetching}><RefreshCw size={14} />刷新记录</button></header>
    <div className="observation-workbench">
      <aside className={`observation-tree-panel ${showTree ? "" : "mobile-collapsed"}`} aria-label="记录导航"><div className="observation-tree-heading"><h2>记录树</h2><button className="observation-mobile-toggle" type="button" aria-expanded={showTree} onClick={() => setShowTree(value => !value)}>{showTree ? "收起目录" : "展开目录"}</button></div><div className="observation-tree"><TreeBranch selected={selected} expanded={expanded} toggle={toggle} select={select} path={path} /></div></aside>
      <section className="observation-detail-panel" aria-label="观察详情" aria-busy={detail.isFetching}>
        {!selected ? <div className="observation-welcome"><h2>选择一条记录</h2><p>查看账号任务或 Agent 对话。</p></div> : <>
          <div className="observation-live-status" role="status"><span className={`observation-live-dot ${detail.data?.active ? "active" : ""}`} />{detail.isPending ? "正在读取记录…" : detail.data?.active ? visible ? "进行中 · 自动更新" : "页面已隐藏 · 暂停更新" : "已保存记录"}{detail.isFetching && !detail.isPending && <span>读取中…</span>}</div>
          {detail.isError && <ReadError error={detail.error} retry={() => { void detail.refetch(); }} />}
          {detail.data && <DetailContent key={detail.data.id} detail={detail.data} select={select} />}
        </>}
      </section>
    </div>
  </div>;
}
