import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Activity, X } from "lucide-react";
import { request } from "../api/client";

type TraceRequest = {
  request_id: string;
  request_kind: "main" | "compaction";
  event: "started" | "finished";
  status: string;
  sent?: boolean;
  model?: string | null;
  estimated_input_tokens?: number | null;
  estimated_parts?: Record<string, unknown> | null;
  usage?: unknown;
  input_limit?: number | null;
  checkpoint_id?: string | null;
  compacted?: boolean;
  externalized?: boolean;
  finish_reason?: string | null;
  error_type?: string | null;
  elapsed_ms?: number | null;
  snapshot_available: boolean;
};
type TraceIndex = { turn_id: string; status: string; requests: TraceRequest[]; available: boolean };
type TraceMessage = { role?: string; content?: unknown; [key: string]: unknown };
type TraceSnapshot = {
  schema_version: number;
  turn_id: string;
  request_id: string;
  context: { messages: TraceMessage[]; tools: unknown[]; max_output_tokens: number | null };
  response: { role: "assistant"; content: string | null; tool_calls?: { id: string; name: string; arguments: unknown }[] } | null;
  tool_results: { tool_call_id: string; name: string; content: unknown; raw_content?: unknown; is_error: boolean; error_type?: string | null }[];
  redacted: boolean;
};

const base = "/api/agent/sessions";
const pretty = (value: unknown) => typeof value === "string" ? value : JSON.stringify(value, null, 2) ?? "";
const display = (value: unknown) => value == null ? "未记录" : String(value);
const formatNumber = (value: number | null | undefined) => value == null ? "未记录" : value.toLocaleString();

export function ChatTrace({ sessionId, turnId, requestId, incomplete }: {
  sessionId: string; turnId?: string; requestId?: string; incomplete: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [selectedRequestId, setSelectedRequestId] = useState<string | null>(requestId ?? null);
  const trigger = useRef<HTMLButtonElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const index = useQuery({
    queryKey: ["agent-turn-trace", sessionId, turnId ?? null],
    queryFn: () => request<TraceIndex>(`${base}/${encodeURIComponent(sessionId)}/turn-trace/${encodeURIComponent(turnId!)}`),
    enabled: open && !!turnId,
    retry: false,
  });
  useEffect(() => {
    if (!open) return;
    const node = dialog.current;
    if (node && !node.open) node.showModal();
  }, [open]);
  useEffect(() => {
    if (!open || !index.data) return;
    const requests = index.data.requests;
    setSelectedRequestId(current => current && requests.some(item => item.request_id === current)
      ? current : requests[0]?.request_id ?? null);
  }, [open, index.data]);
  useEffect(() => () => { if (dialog.current?.open) dialog.current.close(); }, []);
  const selected = index.data?.requests.find(item => item.request_id === selectedRequestId);
  const snapshot = useQuery({
    queryKey: ["agent-turn-trace-snapshot", sessionId, turnId ?? null, selectedRequestId],
    queryFn: () => request<TraceSnapshot>(`${base}/${encodeURIComponent(sessionId)}/turn-trace/${encodeURIComponent(turnId!)}/requests/${encodeURIComponent(selectedRequestId!)}`),
    enabled: open && !!turnId && !!selectedRequestId && !!selected?.snapshot_available,
    retry: false,
    gcTime: 0,
  });
  const close = () => {
    setOpen(false);
    if (dialog.current?.open) dialog.current.close();
    window.requestAnimationFrame(() => trigger.current?.focus());
  };
  const closeNative = () => { setOpen(false); window.requestAnimationFrame(() => trigger.current?.focus()); };
  return <>
    <button ref={trigger} type="button" className="chat-reply-action" aria-label="查看回复 Trace" title="查看回复 Trace" onClick={() => setOpen(true)}>
      <Activity size={15} aria-hidden="true" />
    </button>
    {open && <dialog ref={dialog} className="chat-trace-dialog" aria-labelledby="chat-trace-title"
      onCancel={event => { event.preventDefault(); close(); }} onClose={closeNative}>
      <header className="chat-trace-header">
        <div><h2 id="chat-trace-title">回复 Trace</h2><p>只读本地诊断记录 · 不会调用模型或工具</p></div>
        <button type="button" className="chat-trace-close" aria-label="关闭 Trace" onClick={close}><X size={18} /></button>
      </header>
      {incomplete && <p className="chat-trace-incomplete" role="status">本条回复未正常完成；以下记录可能只包含已结束前的部分步骤。</p>}
      <div className="chat-trace-body">
        {!turnId && <p className="chat-trace-state">这条旧记录没有保存请求标识，无法关联到 Trace 快照。</p>}
        {!!turnId && index.isPending && <p role="status">正在读取请求步骤…</p>}
        {index.isError && <div className="chat-trace-state" role="alert"><p>Trace 读取失败：{index.error.message}</p><button type="button" onClick={() => void index.refetch()}>重试</button></div>}
        {!index.isPending && !index.isError && !index.data?.available && <p className="chat-trace-state">这条历史回复没有可用的 Trace 记录。</p>}
        {!index.isPending && !index.isError && index.data?.available && !index.data.requests.length && <p className="chat-trace-state">没有记录到模型请求步骤。</p>}
        {!!index.data?.requests.length && <>
          <nav className="chat-trace-steps" aria-label="模型请求步骤">
            {index.data.requests.map((step, i) => <button key={step.request_id} type="button"
              aria-current={step.request_id === selectedRequestId ? "step" : undefined}
              onClick={() => setSelectedRequestId(step.request_id)}>
              <span>步骤 {i + 1} · {step.request_kind === "compaction" ? "上下文压缩" : "主请求"}</span>
              <small>{step.status} · {step.event === "finished" ? "已结束" : "已开始"}</small>
            </button>)}
          </nav>
          {selected && <section key={selected.request_id} className="chat-trace-step-detail" aria-label="所选请求详情">
            <h3>{selected.request_kind === "compaction" ? "上下文压缩请求" : "模型请求"}</h3>
            <RequestMetadata step={selected} />
            {!selected.snapshot_available && <p className="chat-trace-state">这一步没有保存上下文快照；旧记录不会补造。</p>}
            {selected.snapshot_available && snapshot.isPending && <p role="status">正在读取这一步的上下文快照…</p>}
            {selected.snapshot_available && snapshot.isError && <div className="chat-trace-state" role="alert"><p>快照读取失败：{snapshot.error.message}</p><button type="button" onClick={() => void snapshot.refetch()}>重试</button></div>}
            {snapshot.data && <SnapshotView key={selected.request_id} data={snapshot.data} />}
          </section>}
        </>}
      </div>
    </dialog>}
  </>;
}

function SnapshotView({ data }: { data: TraceSnapshot }) {
  const [tab, setTab] = useState<"context" | "output" | "tools">("context");
  const messages = data.context.messages ?? [];
  const system = messages.filter(message => message.role === "system" || message.role === "developer");
  const history = messages.filter(message => message.role !== "system" && message.role !== "developer");
  const tabId = `trace-panel-${data.request_id}`;
  return <div className="chat-trace-snapshot">
    {data.redacted && <p className="chat-trace-redacted">快照中的敏感值已脱敏。</p>}
    <div className="chat-trace-tabs" role="tablist" aria-label="Trace 内容">
      {([ ["context", "上下文"], ["output", "模型输出"], ["tools", "工具"] ] as const).map(([key, label]) =>
        <button key={key} type="button" role="tab" id={`${tabId}-${key}-tab`} aria-selected={tab === key}
          aria-controls={`${tabId}-panel`} onClick={() => setTab(key)}>{label}</button>)}
    </div>
    <section className="chat-trace-tab-panel" id={`${tabId}-panel`} role="tabpanel"
      aria-labelledby={`${tabId}-${tab}-tab`}>
      {tab === "context" && <ContextView data={data} system={system} history={history} />}
      {tab === "output" && <OutputView data={data} />}
      {tab === "tools" && <ToolsView data={data} />}
    </section>
  </div>;
}

function requestSummary(step: TraceRequest) {
  const usage = step.usage as { input_tokens?: number; output_tokens?: number } | null;
  if (!step.model && usage?.input_tokens == null && usage?.output_tokens == null) return "请求信息";
  return [step.model, usage?.input_tokens == null ? null : `输入 ${formatNumber(usage.input_tokens)}`,
    usage?.output_tokens == null ? null : `输出 ${formatNumber(usage.output_tokens)}`].filter(Boolean).join(" · ");
}

function RequestMetadata({ step }: { step: TraceRequest }) {
  return <details className="chat-trace-metadata">
    <summary>{requestSummary(step)}</summary>
    <dl>
      <div><dt>状态</dt><dd>{step.status} · {step.event === "finished" ? "已结束" : "已开始"}</dd></div>
      <div><dt>模型</dt><dd>{display(step.model)}</dd></div>
      <div><dt>输入用量</dt><dd>{formatNumber((step.usage as { input_tokens?: number } | null)?.input_tokens)}</dd></div>
      <div><dt>输出用量</dt><dd>{formatNumber((step.usage as { output_tokens?: number } | null)?.output_tokens)}</dd></div>
      <div><dt>估算输入</dt><dd>{formatNumber(step.estimated_input_tokens)} tokens</dd></div>
      <div><dt>估算组成</dt><dd><TraceValue value={step.estimated_parts ?? null} /></dd></div>
      <div><dt>输入上限</dt><dd>{formatNumber(step.input_limit)} tokens</dd></div>
      <div><dt>耗时</dt><dd>{step.elapsed_ms == null ? "未记录" : `${step.elapsed_ms.toLocaleString()} ms`}</dd></div>
      <div><dt>已发送</dt><dd>{step.sent == null ? "未记录" : step.sent ? "是" : "否"}</dd></div>
      <div><dt>压缩 / 外置</dt><dd>{step.compacted ? "已压缩" : "未压缩"} · {step.externalized ? "已外置" : "未外置"}</dd></div>
      {step.finish_reason && <div><dt>结束原因</dt><dd>{step.finish_reason}</dd></div>}
      {step.error_type && <div><dt>错误类型</dt><dd>{step.error_type}</dd></div>}
      {step.checkpoint_id && <div><dt>检查点</dt><dd>{step.checkpoint_id}</dd></div>}
    </dl>
  </details>;
}

function ContextView({ data, system, history }: { data: TraceSnapshot; system: TraceMessage[]; history: TraceMessage[] }) {
  const [mode, setMode] = useState<"reading" | "raw">("reading");
  return <>
    <div className="chat-trace-message-toolbar" role="group" aria-label="消息显示方式">
      <button type="button" aria-pressed={mode === "reading"} onClick={() => setMode("reading")}>阅读</button>
      <button type="button" aria-pressed={mode === "raw"} onClick={() => setMode("raw")}>原始消息</button>
    </div>
    <div className="chat-trace-subsection">
      <h4>System / Developer</h4>
      {system.length ? system.map((message, i) => <ContextMessage key={i} message={message} mode={mode} />)
        : <p className="chat-trace-state">没有单独记录的 system / developer 消息。</p>}
    </div>
    <div className="chat-trace-subsection">
      <h4>对话消息</h4>
      {history.length ? history.map((message, i) => <ContextMessage key={i} message={message} mode={mode} />)
        : <p className="chat-trace-state">没有对话消息。</p>}
    </div>
    <div className="chat-trace-subsection"><h4>工具 Schema · {data.context.tools.length}</h4>
      <TraceBlock label="tools" value={data.context.tools} />
    </div>
    <p className="chat-trace-token-limit">最大输出 tokens：{data.context.max_output_tokens == null ? "模型默认" : formatNumber(data.context.max_output_tokens)}</p>
  </>;
}

function ContextMessage({ message, mode }: { message: TraceMessage; mode: "reading" | "raw" }) {
  const role = message.role ?? "消息";
  if (mode === "raw") return <TraceBlock label={role} value={message} />;
  const callId = typeof message.tool_call_id === "string" ? message.tool_call_id : null;
  const content = message.content;
  const text = content == null ? "（空）" : pretty(content);
  const assistantCalls = role === "assistant" && Array.isArray(message.tool_calls) ? message.tool_calls : [];
  return <div className="chat-trace-reading-message">
    <div className="chat-trace-message-label">{role}{callId && <span> · call ID {callId}</span>}</div>
    <TraceBlock label="内容" value={text} />
    {!!assistantCalls.length && <details className="chat-trace-inline-detail">
      <summary>历史工具调用 · {assistantCalls.length}</summary>
      {assistantCalls.map((call, i) => <TraceBlock key={i} label={`工具调用 ${i + 1}`} value={call} />)}
    </details>}
  </div>;
}

function OutputView({ data }: { data: TraceSnapshot }) {
  return <>
    <h4>模型返回</h4>
    {data.response ? <TraceBlock label="assistant content" value={data.response.content ?? "（空）"} />
      : <p className="chat-trace-state">没有保存模型返回。</p>}
  </>;
}

function ToolsView({ data }: { data: TraceSnapshot }) {
  const calls = data.response?.tool_calls ?? [];
  const resultsByCallId = new Map<string, typeof data.tool_results>();
  for (const result of data.tool_results) {
    const matches = resultsByCallId.get(result.tool_call_id) ?? [];
    matches.push(result);
    resultsByCallId.set(result.tool_call_id, matches);
  }
  const matchedIds = new Set(calls.map(call => call.id));
  const orphanResults = data.tool_results.filter(result => !matchedIds.has(result.tool_call_id));
  return <>
    <h4>工具调用与结果</h4>
    {calls.map((call, i) => {
      const results = resultsByCallId.get(call.id) ?? [];
      return <article className="chat-trace-tool-pair" key={`${call.id}-${i}`}>
        <h5>{call.name} · call ID {call.id}</h5>
        <TraceBlock label="参数" value={call.arguments} />
        {results.length ? results.map((result, resultIndex) => <ToolResultView key={`${result.tool_call_id}-${resultIndex}`} result={result} />)
          : <p className="chat-trace-state">未记录结果；执行结果未知。</p>}
      </article>;
    })}
    {!!orphanResults.length && <div className="chat-trace-subsection">
      <h5>没有匹配调用的结果</h5>
      {orphanResults.map((result, i) => <ToolResultView key={`${result.tool_call_id}-${i}`} result={result} />)}
    </div>}
    {!calls.length && !orphanResults.length && <p className="chat-trace-state">没有记录到工具调用或结果。</p>}
  </>;
}

function ToolResultView({ result }: { result: TraceSnapshot["tool_results"][number] }) {
  const [showRaw, setShowRaw] = useState(false);
  const hasRaw = result.raw_content !== undefined && result.raw_content !== result.content;
  return <div>
    <TraceBlock label={`结果${result.is_error ? ` · 失败${result.error_type ? ` (${result.error_type})` : ""}` : ""} · call ID ${result.tool_call_id}`}
      value={result.content} />
    {hasRaw && <>
      <button type="button" className="chat-trace-expand" aria-expanded={showRaw}
        onClick={() => setShowRaw(!showRaw)}>{showRaw ? "收起原始返回" : "查看原始返回"}</button>
      {showRaw && <TraceBlock label="工具原始返回" value={result.raw_content} />}
    </>}
  </div>;
}

function TraceValue({ value }: { value: unknown }) {
  if (value == null) return <>未记录</>;
  return <TraceBlock label="估算项" value={value} />;
}

function TraceBlock({ label, value }: { label: string; value: unknown }) {
  const [expanded, setExpanded] = useState(false);
  const text = pretty(value);
  const shouldTrim = text.length > 500;
  return <div className="chat-trace-block">
    <h5>{label}</h5>
    <pre><code>{shouldTrim && !expanded ? `${text.slice(0, 500)}…` : text}</code></pre>
    {shouldTrim && <button type="button" className="chat-trace-expand" aria-expanded={expanded}
      onClick={() => setExpanded(!expanded)}>{expanded ? "收起" : "展开全文"}</button>}
  </div>;
}
