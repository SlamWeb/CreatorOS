import { useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { apiUrl, request, studioApi } from "../api/client";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Check, Copy } from "lucide-react";
import { ChatTrace } from "../components/ChatTrace";
import { ResearchActivity, type ResearchSnapshot } from "../components/ResearchActivity";

type Entry = { kind: string; text?: string; name?: string; status?: string; run_id?: string;
  input_tokens?: number; output_tokens?: number; turn_id?: string; complete?: boolean; terminal?: boolean;
  model_request_id?: string; research?: ResearchSnapshot };
type Session = { id: string; title: string; version: number; status: string; error: string | null;
  scope_kind: "overview" | "creator"; creator_id: string | null;
  entries: Entry[]; updated_at: string; has_older: boolean };
const tools: Record<string, string> = { list_creators: "查看账号", list_creator_series: "查看栏目",
  list_series_topics: "查看选题", start_content_run: "提交生产", get_content_run: "查询任务",
  research_series_topics: "调研选题", get_topic_research: "查看调研候选", prepare_topic_selection: "准备选题预览",
  queue_topics: "选题入队", compose_series: "创建栏目", update_series_composition: "修改组合", assign_series: "分配账号",
  list_producer_skills: "查看生产 Skill", install_producer_skill: "安装 Skill", get_skill_install: "查询安装",
  read_file: "读取历史资料", read_tool_result: "回读工具结果" };
const status: Record<string, string> = { idle: "可以继续对话", running: "正在处理", failed: "本次未完成", interrupted: "已中断" };
const base = "/api/agent/sessions";
const newestSession = (old: Session | undefined, next: Session) => old &&
  (old.version > next.version || (old.version === next.version && old.updated_at > next.updated_at)) ? old : next;
const draftStorageKey = (scope: string) => `creatoros.agent.draft.v1:${encodeURIComponent(scope)}`;
const selectedChatStorageKey = (scope: string) => `creatoros.agent.selected-chat.v1:${encodeURIComponent(scope)}`;
const readSessionValue = (key: string) => {
  try { return window.sessionStorage.getItem(key) ?? ""; } catch { return ""; }
};
const writeSessionValue = (key: string, value: string) => {
  try {
    if (value) window.sessionStorage.setItem(key, value);
    else window.sessionStorage.removeItem(key);
  } catch { /* In-memory state still works when storage is unavailable. */ }
};

export function AgentPage() {
  return <AgentConversation mode="standalone" />;
}

export type AgentConversationProps = {
  mode: "standalone" | "embedded";
  creatorId?: string | null;
  accountName?: string;
  active?: boolean;
  draftSeed?: string | null;
  draftSeedId?: string | number;
};

export function AgentConversation({ mode, creatorId: embeddedCreatorId = null, accountName,
  active = true, draftSeed, draftSeedId }: AgentConversationProps) {
  const [params, setParams] = useSearchParams();
  const cache = useQueryClient();
  const standalone = mode === "standalone";
  const requestedCreatorId = standalone ? params.get("creator") : embeddedCreatorId;
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [selectedChats, setSelectedChats] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [historyOpen, setHistoryOpen] = useState(false);
  const scopeKeyRef = useRef("");
  const creatorScopeId = requestedCreatorId;
  const scopeKey = creatorScopeId ? `creator:${creatorScopeId}` : "overview";
  scopeKeyRef.current = scopeKey;
  const draft = drafts[scopeKey] ?? readSessionValue(draftStorageKey(scopeKey));
  const id = standalone ? params.get("chat") : ((selectedChats[scopeKey] ?? readSessionValue(selectedChatStorageKey(scopeKey))) || null);
  const error = errors[scopeKey] ?? "";
  const updateDraft = useCallback((scope: string, value: string) => {
    setDrafts(previous => ({ ...previous, [scope]: value }));
    writeSessionValue(draftStorageKey(scope), value);
  }, []);
  const updateError = useCallback((scope: string, value: string) => {
    setErrors(previous => ({ ...previous, [scope]: value }));
  }, []);
  const [connected, setConnected] = useState(false);
  const transcript = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const lastSeed = useRef("");
  const consumedSkillHandoff = useRef("");
  const session = useQuery({ queryKey: ["agent-session", scopeKey, id], queryFn: async () => {
    const next = await request<Session>(`${base}/${id}`);
    return newestSession(cache.getQueryData<Session>(["agent-session", scopeKey, id]), next);
  },
    enabled: active && !!id, refetchInterval: q => active && q.state.data?.status === "running" ? 2000 : false });
  const doc = session.data;
  const creatorId = standalone ? (requestedCreatorId ?? (doc?.scope_kind === "creator" ? doc.creator_id : null)) : embeddedCreatorId;
  const dataScopeKey = creatorId ? `creator:${creatorId}` : "overview";
  const scopeReady = active && (!id || !!doc || session.isError);
  const scopeQuery = creatorId
    ? `scope_kind=creator&creator_id=${encodeURIComponent(creatorId)}`
    : "scope_kind=overview";
  const sessions = useQuery({ queryKey: ["agent-sessions", dataScopeKey],
    queryFn: () => request<{ items: Session[] }>(`${base}?${scopeQuery}`),
    enabled: scopeReady, refetchInterval: q => active && q.state.data?.items.some(item => item.status === "running") ? 5000 : false });
  const creator = useQuery({ queryKey: ["creator", creatorId], queryFn: () => studioApi.creator(creatorId!), retry: false,
    enabled: active && !!creatorId });
  const docCreatorId = doc?.scope_kind === "creator" ? doc.creator_id : null;
  const scopeMismatch = !!doc && (doc.scope_kind !== (creatorId ? "creator" : "overview") || docCreatorId !== creatorId);
  const accountError = creatorId ? creator.isError : false;
  const accountInactive = !!creator.data && !creator.data.is_active;
  const accountPending = !!creatorId && creator.isPending;
  const cannotSend = scopeMismatch || accountError || accountInactive || accountPending;
  const observedSession = useRef<{ key: string; status?: string }>({ key: "" });

  useEffect(() => {
    if (!active || !doc || scopeMismatch) return;
    const key = `${scopeKey}:${doc.id}`;
    const previous = observedSession.current;
    observedSession.current = { key, status: doc.status };
    if (previous.key === key && previous.status === "running" && doc.status !== "running") {
      // A finished tool turn may have changed business state. Refresh once;
      // do not keep polling the workspace while the conversation is idle.
      for (const queryKey of ["creators", "series-all", "series", "topics", "runs", "overview"]) {
        void cache.invalidateQueries({ queryKey: [queryKey] });
      }
    }
  }, [active, doc?.id, doc?.status, scopeKey, scopeMismatch, cache]);

  useEffect(() => {
    if (!standalone || !id || requestedCreatorId || !doc || doc.scope_kind !== "creator" || !doc.creator_id) return;
    setParams(previous => {
      const next = new URLSearchParams(previous);
      next.set("creator", doc.creator_id!);
      return next;
    }, { replace: true });
  }, [standalone, id, requestedCreatorId, doc, setParams]);
  useEffect(() => { updateError(scopeKey, ""); setHistoryOpen(false); }, [id, scopeKey, updateError]);
  useEffect(() => {
    if (!draftSeed) { lastSeed.current = ""; return; }
    if (!active) return;
    const seedKey = `${scopeKey}\u001f${draftSeedId ?? draftSeed}`;
    if (lastSeed.current === seedKey) return;
    lastSeed.current = seedKey;
    updateDraft(scopeKey, draftSeed);
  }, [active, draftSeed, draftSeedId, scopeKey, updateDraft]);
  useEffect(() => {
    const nonce = params.get("skill_edit");
    if (!nonce || consumedSkillHandoff.current === nonce) return;
    consumedSkillHandoff.current = nonce;
    const key = `creatoros.skill-edit-handoff:${nonce}`;
    try {
      const raw = window.sessionStorage.getItem(key);
      if (!raw) return;
      const value = JSON.parse(raw) as { skillId?: string; skillName?: string; path?: string; request?: string };
      if (!value.skillId || !value.skillName || !value.path || !value.request) return;
      updateDraft(scopeKey, `请帮我修改 CreatorOS 本地 Skill「${value.skillName}」。\nSkill ID：${value.skillId}\n目标文件：${value.path}\n\n修改要求：\n${value.request}\n\n请先检查当前文件内容并给出具体修改；没有实际文件写入工具时，不要声称已经保存。`);
      window.sessionStorage.removeItem(key);
      setParams(previous => { const next = new URLSearchParams(previous); next.delete("skill_edit"); return next; }, { replace: true });
    } catch { updateError(scopeKey, "无法读取 Skill 修改草稿，请返回 Skill 卡片重新提交。"); }
  }, [params, scopeKey, setParams, updateDraft, updateError]);
  useEffect(() => {
    setConnected(false); follow.current = true;
    if (!active || !id || doc?.status !== "running") return;
    let live = true;
    const source = new EventSource(apiUrl(`${base}/${id}/events`));
    source.onopen = () => { if (live) setConnected(true); };
    source.onerror = () => { if (live) setConnected(false); };
    source.addEventListener("snapshot", event => {
      const next = JSON.parse((event as MessageEvent).data) as Session;
      if (!live || next.id !== id) return;
      cache.setQueryData<Session>(["agent-session", scopeKey, id], old => newestSession(old, next));
    });
    return () => { live = false; source.close(); };
  }, [active, id, scopeKey, doc?.status, cache]);
  useEffect(() => {
    if (follow.current && transcript.current) transcript.current.scrollTop = transcript.current.scrollHeight;
  }, [doc]);
  const send = useMutation({
    retry: false,
    mutationFn: async ({ text, current, scopeCreatorId, submittedScopeKey }: { text: string; current: Session | undefined; scopeCreatorId: string | null; submittedScopeKey: string }) => {
      const target = current ?? await request<Session>(base, { method: "POST", body: JSON.stringify({ creator_id: scopeCreatorId }) });
      const targetCreatorId = target.scope_kind === "creator" ? target.creator_id : null;
      if (targetCreatorId !== scopeCreatorId || target.scope_kind !== (scopeCreatorId ? "creator" : "overview")) {
        throw new Error("会话账号范围与当前选择不一致，已停止发送。请返回对应账号后打开正确的对话。");
      }
      if (!current) {
        cache.setQueryData(["agent-session", submittedScopeKey, target.id], target);
        writeSessionValue(selectedChatStorageKey(submittedScopeKey), target.id);
        setSelectedChats(previous => ({ ...previous, [submittedScopeKey]: target.id }));
        if (standalone && scopeKeyRef.current === submittedScopeKey) {
          setParams(previous => {
            const next = new URLSearchParams(previous);
            next.set("chat", target.id);
            if (scopeCreatorId) next.set("creator", scopeCreatorId);
            else next.delete("creator");
            return next;
          });
        }
      }
      return request<Session>(`${base}/${target.id}/turns`, { method: "POST", body: JSON.stringify({
        text, request_id: crypto.randomUUID(), expected_version: target.version,
      }) });
    },
    onSuccess: (next, variables) => {
      cache.setQueryData<Session>(["agent-session", variables.submittedScopeKey, next.id], old => newestSession(old, next));
      if (readSessionValue(draftStorageKey(variables.submittedScopeKey)).trim() === variables.text) updateDraft(variables.submittedScopeKey, "");
      updateError(variables.submittedScopeKey, "");
      if (scopeKeyRef.current === variables.submittedScopeKey) follow.current = true;
      void cache.invalidateQueries({ queryKey: ["agent-sessions", variables.submittedScopeKey] });
    },
    onError: (e: Error, variables) => {
      updateError(variables.submittedScopeKey, `${e.message} 没有自动重试；请先检查对话与运行记录。`);
      void cache.invalidateQueries({ queryKey: ["agent-session", variables.submittedScopeKey] });
    },
  });
  const submit = () => {
    if (!draft.trim() || send.isPending || doc?.status === "running" || (id && !doc) || cannotSend) return;
    updateError(scopeKey, ""); send.mutate({ text: draft.trim(), current: doc, scopeCreatorId: creatorId, submittedScopeKey: scopeKey });
  };
  const chooseSession = (next: string | null) => {
    updateError(scopeKey, ""); setHistoryOpen(false);
    if (standalone) {
      setParams(previous => {
        const nextParams = new URLSearchParams(previous);
        nextParams.delete("command");
        if (next) nextParams.set("chat", next); else nextParams.delete("chat");
        if (creatorId) nextParams.set("creator", creatorId);
        else nextParams.delete("creator");
        return nextParams;
      });
    } else {
      writeSessionValue(selectedChatStorageKey(scopeKey), next ?? "");
      setSelectedChats(previous => ({ ...previous, [scopeKey]: next ?? "" }));
    }
  };
  const history = sessions.data?.items ?? [];
  return <section className={`agent-workspace${standalone ? "" : " account-chat-workspace"}`}>
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
    <div className="agent-heading"><div><h1>{doc?.title ?? (creatorId ? "账号对话" : "把想法交给 Agent")}</h1>
      <span className="agent-connection">{creatorId ? `账号：${accountName ?? creator.data?.display_name ?? (creator.isPending ? "读取中…" : creatorId)}` : "全部账号"}{standalone && creatorId && <> · <Link to={`/?creator=${encodeURIComponent(creatorId)}`}>返回账号工作台</Link></>}</span>
      {id && <span className="agent-connection">{doc?.status === "running" ? (connected ? "实时连接" : "连接中 · 自动刷新") : "记录已保存"}</span>}</div>
      {standalone && <Link className="text-link" to={(() => { const next = new URLSearchParams(params); next.set("command", "new"); return `?${next.toString()}`; })()}>添加 / 调整选题 ↗</Link>}</div>
    {(session.isError || sessions.isError || accountError || accountInactive || scopeMismatch) && <p role="alert" className="review-warning">
      {scopeMismatch ? "这个对话属于其他账号范围，不能在当前账号上下文中继续发送。" : accountInactive ? "该账号已停用，不能发送新的对话指令。" : accountError ? `无法读取账号，已阻止发送：${creator.error?.message ?? "账号不存在或暂不可用。"}` : session.error?.message ?? sessions.error?.message}
    </p>}
    {!scopeMismatch && <div className="agent-transcript" ref={transcript} onScroll={e => {
      const el = e.currentTarget; follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 70;
    }} aria-label="对话记录">
      {id && session.isPending && <p className="agent-note">正在读取对话…</p>}
      {(!id || (doc && !doc.entries.length)) && <div className="agent-welcome"><span>✦</span><h2>{creatorId ? `从「${accountName ?? creator.data?.display_name ?? "当前账号"}」开始` : "从你已有的账号开始"}</h2>
        <button type="button" disabled={cannotSend} onClick={() => updateDraft(scopeKey, creatorId ? `看看「${accountName ?? creator.data?.display_name ?? "这个账号"}」的栏目和选题，先不要生产。` : "看看我有哪些账号和栏目，先不要生产。")}>{creatorId ? "查看这个账号的栏目与选题 ↗" : "看看我的账号和栏目 ↗"}</button></div>}
      {doc?.has_older && <p className="agent-note">显示最近记录；完整消息仍保存在本地会话中。</p>}
      {doc?.entries.map((entry, index) => <ChatEntry key={`${doc.id}-${index}`} entry={entry} sessionId={doc.id} sessionStatus={doc.status} />)}
      {doc?.error && <p className="review-warning" role="alert">{doc.error}</p>}
    </div>}
    <form className="agent-composer" onSubmit={e => { e.preventDefault(); submit(); }}>
      <textarea aria-label="给 Agent 的消息" placeholder={creatorId ? "围绕这个账号的栏目与选题继续…" : "说说你想做什么…"} value={draft} maxLength={8000} rows={2}
        onChange={e => updateDraft(scopeKey, e.target.value)} onKeyDown={e => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit(); }
        }} />
      <div><span role="status">{send.isPending ? "正在提交…" : status[doc?.status ?? "idle"]} · Enter 发送</span>
        <button className="button button-primary" disabled={!draft.trim() || send.isPending || doc?.status === "running" || (!!id && !doc) || cannotSend}>发送 ↑</button></div>
    </form>
    {error && <p role="alert" className="review-warning">{error}</p>}
    </div>
  </section>;
}

function ChatEntry({ entry, sessionId, sessionStatus }: { entry: Entry; sessionId: string; sessionStatus: string }) {
  if (entry.kind === "user") return <div className="chat-user">{entry.text}</div>;
  if (entry.kind === "assistant") return entry.text ? <div className="chat-reply"><div className="chat-answer"><Markdown remarkPlugins={[remarkGfm]} skipHtml components={{
    table: ({ children }) => <div className="chat-table-scroll" tabIndex={0} role="region" aria-label="表格，可横向滚动"><table>{children}</table></div>,
    img: ({ alt }) => <span>{alt ?? "图片请在内容页查看"}</span>,
    a: ({ href, children }) => {
      const runPath = href?.match(/^(?:http:\/\/(?:127\.0\.0\.1|localhost)(?::\d+)?)?(\/runs\/[a-f0-9-]{36})$/)?.[1];
      return runPath ? <Link to={runPath}>{children}</Link> : <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>;
    },
  }}>{entry.text}</Markdown></div>
    {(entry.complete || entry.terminal || (entry.complete === undefined && entry.terminal === undefined && sessionStatus !== "running")) && <div className="chat-reply-actions">
      <CopyReply text={entry.text} />
      <ChatTrace sessionId={sessionId} turnId={entry.turn_id} requestId={entry.model_request_id}
        incomplete={entry.terminal === true && !entry.complete} />
    </div>}
  </div> : null;
  if (entry.kind === "tool") return <div className="chat-tool" data-status={entry.status}>
    <span>{entry.status === "running" ? "◌" : entry.status === "done" ? "✓" : "!"} {tools[entry.name ?? ""] ?? entry.name}</span>
    <small>{entry.status === "running" ? (entry.research ? "等待调研结果" : "调用中") : entry.status === "done" ? "已返回" : "结果需检查"}</small>
    {entry.run_id && <Link to={`/runs/${entry.run_id}`}>查看内容任务 ↗</Link>}
    {entry.research && <ResearchActivity research={entry.research} />}
  </div>;
  if (entry.kind === "usage") return <details className="chat-usage"><summary>本轮用量</summary>
    输入 {entry.input_tokens?.toLocaleString()} · 输出 {entry.output_tokens?.toLocaleString()} tokens</details>;
  return <p className="agent-note">{entry.kind === "guard_stop" ? "已达到本次调用上限，请检查当前结果。" : entry.kind === "context_compacted" ? "较早消息已压缩，完整记录仍在本地。" : "上下文接近预算。"}</p>;
}

function CopyReply({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");
  const reset = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(reset.current), []);
  const copy = async () => {
    try {
      if (!navigator.clipboard?.writeText) throw new Error("浏览器不支持剪贴板写入。");
      await navigator.clipboard.writeText(text);
      setCopied(true); setError("");
      window.clearTimeout(reset.current);
      reset.current = window.setTimeout(() => setCopied(false), 1600);
    } catch (e) {
      setCopied(false);
      setError(e instanceof Error ? `复制失败：${e.message}` : "复制失败，请检查浏览器剪贴板权限。");
    }
  };
  return <>
    <button type="button" className="chat-reply-action" aria-label="复制回复原文" title="复制回复原文" onClick={() => void copy()}>
      {copied ? <Check size={15} aria-hidden="true" /> : <Copy size={15} aria-hidden="true" />}
    </button>
    {error && <span className="chat-copy-error" role="alert">{error}</span>}
    {copied && <span className="sr-only" role="status">已复制回复原文</span>}
  </>;
}
