import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { apiUrl, request } from "../api/client";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

type Entry = { kind: string; text?: string; name?: string; status?: string; run_id?: string;
  input_tokens?: number; output_tokens?: number };
type Session = { id: string; title: string; version: number; status: string; error: string | null;
  entries: Entry[]; updated_at: string; has_older: boolean };
const tools: Record<string, string> = { list_creators: "查看账号", list_creator_series: "查看栏目",
  list_series_topics: "查看选题", start_content_run: "提交生产", get_content_run: "查询任务",
  research_series_topics: "调研选题", get_topic_research: "查看调研候选", prepare_topic_selection: "准备选题预览",
  queue_topics: "选题入队", compose_series: "创建栏目", update_series_composition: "修改组合", assign_series: "分配账号",
  list_producer_skills: "查看生产 Skill", install_producer_skill: "安装 Skill", get_skill_install: "查询安装",
  read_file: "读取历史资料", read_tool_result: "回读工具结果" };
const status: Record<string, string> = { idle: "可以继续对话", running: "正在处理", failed: "本次未完成", interrupted: "已中断" };
const base = "/api/agent/sessions";

export function AgentPage() {
  const [params, setParams] = useSearchParams();
  const id = params.get("chat");
  const cache = useQueryClient();
  const [draft, setDraft] = useState("");
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const transcript = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const sessions = useQuery({ queryKey: ["agent-sessions"], queryFn: () => request<{ items: Session[] }>(base), refetchInterval: 5000 });
  const session = useQuery({ queryKey: ["agent-session", id], queryFn: () => request<Session>(`${base}/${id}`),
    enabled: !!id, refetchInterval: q => q.state.data?.status === "running" ? 2000 : 10000 });
  const doc = session.data;
  useEffect(() => {
    setConnected(false); follow.current = true;
    if (!id) return;
    const source = new EventSource(apiUrl(`${base}/${id}/events`));
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    source.addEventListener("snapshot", event => {
      const next = JSON.parse((event as MessageEvent).data) as Session;
      if (next.id !== id) return;
      cache.setQueryData<Session>(["agent-session", id], old => old && old.updated_at > next.updated_at ? old : next);
    });
    return () => source.close();
  }, [id, cache]);
  useEffect(() => {
    if (follow.current && transcript.current) transcript.current.scrollTop = transcript.current.scrollHeight;
  }, [doc]);
  const send = useMutation({
    retry: false,
    mutationFn: async ({ text, current }: { text: string; current: Session | undefined }) => {
      const target = current ?? await request<Session>(base, { method: "POST", body: "{}" });
      if (!current) {
        cache.setQueryData(["agent-session", target.id], target);
        setParams({ chat: target.id });
      }
      return request<Session>(`${base}/${target.id}/turns`, { method: "POST", body: JSON.stringify({
        text, request_id: crypto.randomUUID(), expected_version: target.version,
      }) });
    },
    onSuccess: next => {
      cache.setQueryData(["agent-session", next.id], next); setDraft(""); setError(""); follow.current = true;
      void cache.invalidateQueries({ queryKey: ["agent-sessions"] });
    },
    onError: (e: Error) => {
      setError(`${e.message} 没有自动重试；请先检查对话与运行记录。`);
      void cache.invalidateQueries({ queryKey: ["agent-session"] });
    },
  });
  const submit = () => {
    if (!draft.trim() || send.isPending || doc?.status === "running" || (id && !doc)) return;
    setError(""); send.mutate({ text: draft.trim(), current: doc });
  };
  const chooseSession = (next: string | null) => {
    setError(""); setDraft(""); setHistoryOpen(false);
    setParams(next ? { chat: next } : {});
  };
  const history = sessions.data?.items ?? [];
  return <section className="agent-workspace">
    <button type="button" className="agent-history-toggle" aria-expanded={historyOpen} aria-controls="agent-history"
      onClick={() => setHistoryOpen(!historyOpen)}>历史对话</button>
    <aside id="agent-history" className={`agent-history ${historyOpen ? "is-open" : ""}`} aria-label="历史对话">
      <button type="button" className="agent-new" disabled={send.isPending} onClick={() => chooseSession(null)}><span aria-hidden="true">＋</span> 新对话</button>
      <p className="agent-history-label">最近对话</p>
      <nav aria-label="选择对话" className="agent-history-list">
        {sessions.isPending && <p className="agent-note">正在读取…</p>}
        {!sessions.isPending && !sessions.isError && !history.length && <p className="agent-note">开始对话后，会保存在这里。</p>}
        {history.map(s => <button key={s.id} type="button" disabled={send.isPending} title={s.title}
          aria-current={s.id === id ? "page" : undefined} onClick={() => chooseSession(s.id)}>
          <span className="agent-history-title">{s.title}</span><small>{s.status === "running" ? "处理中" : new Date(s.updated_at).toLocaleDateString("zh-CN", { month: "short", day: "numeric" })}</small>
        </button>)}
        {id && !history.some(s => s.id === id) && <button type="button" aria-current="page" disabled>{doc?.title ?? "读取当前对话…"}</button>}
      </nav>
    </aside>
    <div className="agent-conversation">
    <div className="agent-heading"><div><h1>{doc?.title ?? "把想法交给 Agent"}</h1>
      {id && <span className="agent-connection">{connected ? "实时连接" : "连接中 · 自动刷新"}</span>}</div><Link className="text-link" to={id ? `?chat=${encodeURIComponent(id)}&command=new` : "?command=new"}>添加 / 调整选题 ↗</Link></div>
    {(session.isError || sessions.isError) && <p role="alert" className="review-warning">{session.error?.message ?? sessions.error?.message}</p>}
    <div className="agent-transcript" ref={transcript} onScroll={e => {
      const el = e.currentTarget; follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 70;
    }} aria-label="对话记录">
      {id && session.isPending && <p className="agent-note">正在读取对话…</p>}
      {(!id || (doc && !doc.entries.length)) && <div className="agent-welcome"><span>✦</span><h2>从你已有的账号开始</h2>
        <button type="button" onClick={() => setDraft("看看我有哪些账号和栏目，先不要生产。")}>看看我的账号和栏目 ↗</button></div>}
      {doc?.has_older && <p className="agent-note">显示最近记录；完整消息仍保存在本地会话中。</p>}
      {doc?.entries.map((entry, index) => <ChatEntry key={index} entry={entry} />)}
      {doc?.error && <p className="review-warning" role="alert">{doc.error}</p>}
    </div>
    <form className="agent-composer" onSubmit={e => { e.preventDefault(); submit(); }}>
      <textarea aria-label="给 Agent 的消息" placeholder="说说你想做什么…" value={draft} maxLength={8000} rows={2}
        onChange={e => setDraft(e.target.value)} onKeyDown={e => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit(); }
        }} />
      <div><span role="status">{send.isPending ? "正在提交…" : status[doc?.status ?? "idle"]} · Enter 发送</span>
        <button className="button button-primary" disabled={!draft.trim() || send.isPending || doc?.status === "running" || (!!id && !doc)}>发送 ↑</button></div>
    </form>
    {error && <p role="alert" className="review-warning">{error}</p>}
    </div>
  </section>;
}

function ChatEntry({ entry }: { entry: Entry }) {
  if (entry.kind === "user") return <div className="chat-user">{entry.text}</div>;
  if (entry.kind === "assistant") return entry.text ? <div className="chat-answer"><Markdown remarkPlugins={[remarkGfm]} skipHtml components={{
    table: ({ children }) => <div className="chat-table-scroll" tabIndex={0} role="region" aria-label="表格，可横向滚动"><table>{children}</table></div>,
    img: ({ alt }) => <span>{alt ?? "图片请在内容页查看"}</span>,
    a: ({ href, children }) => {
      const runPath = href?.match(/^(?:http:\/\/(?:127\.0\.0\.1|localhost)(?::\d+)?)?(\/runs\/[a-f0-9-]{36})$/)?.[1];
      return runPath ? <Link to={runPath}>{children}</Link> : <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>;
    },
  }}>{entry.text}</Markdown></div> : null;
  if (entry.kind === "tool") return <div className="chat-tool">
    <span>{entry.status === "running" ? "◌" : entry.status === "done" ? "✓" : "!"} {tools[entry.name ?? ""] ?? entry.name}</span>
    <small>{entry.status === "running" ? "调用中" : entry.status === "done" ? "已返回" : "结果需检查"}</small>
    {entry.run_id && <Link to={`/runs/${entry.run_id}`}>查看内容任务 ↗</Link>}
  </div>;
  if (entry.kind === "usage") return <details className="chat-usage"><summary>本轮用量</summary>
    输入 {entry.input_tokens?.toLocaleString()} · 输出 {entry.output_tokens?.toLocaleString()} tokens</details>;
  return <p className="agent-note">{entry.kind === "guard_stop" ? "已达到本次调用上限，请检查当前结果。" : entry.kind === "context_compacted" ? "较早消息已压缩，完整记录仍在本地。" : "上下文接近预算。"}</p>;
}
