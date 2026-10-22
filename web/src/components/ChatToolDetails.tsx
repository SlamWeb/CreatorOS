import { useId, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { request } from "../api/client";

type ToolTrace = {
  response: { tool_calls?: { id: string; name: string; arguments: unknown }[] } | null;
  tool_results: { tool_call_id: string; name: string; content: unknown; raw_content?: unknown;
    is_error: boolean; error_type?: string | null }[];
};

export function ChatToolDetails({ sessionId, turnId, requestId, callId, running }: {
  sessionId: string; turnId?: string; requestId?: string; callId?: string; running: boolean;
}) {
  const snapshot = useQuery({
    queryKey: ["agent-tool-trace", sessionId, turnId, requestId, running],
    queryFn: () => request<ToolTrace>(`/api/agent/sessions/${encodeURIComponent(sessionId)}/turn-trace/${encodeURIComponent(turnId!)}/requests/${encodeURIComponent(requestId!)}`),
    enabled: !!turnId && !!requestId && !!callId,
    retry: false,
    refetchInterval: running ? 2000 : false,
    refetchOnWindowFocus: false,
    gcTime: 0,
  });
  if (!turnId || !requestId || !callId) return <p className="chat-tool-note">这条旧记录没有关联工具详情；完整历史可在回复 Trace 中查看。</p>;
  if (snapshot.isPending) return <p className="chat-tool-note" role="status">正在读取工具详情…</p>;
  if (snapshot.isError) return <div className="chat-tool-note" role="alert">
    <p>工具详情读取失败：{snapshot.error.message}</p>
    <button type="button" onClick={() => void snapshot.refetch()}>重新读取</button>
  </div>;
  const call = snapshot.data?.response?.tool_calls?.find(item => item.id === callId);
  const results = snapshot.data?.tool_results?.filter(item => item.tool_call_id === callId) ?? [];
  return <div className="chat-tool-detail-content">
    {call ? <ToolValue label="参数" value={call.arguments} /> : <p className="chat-tool-note">没有保存这次调用的参数；不按工具名猜测关联。</p>}
    {!results.length && <p className="chat-tool-note">{running ? "尚未返回结果。" : "未记录结果；执行结果未知。"}</p>}
    {results.map((result, index) => <div key={`${callId}:${index}`} className={result.is_error ? "chat-tool-result-error" : undefined}>
      <ToolValue label={result.is_error ? "错误" : "结果"} value={result.content} />
      {result.error_type && <p className="chat-tool-note">错误类型：{result.error_type}</p>}
      {result.raw_content != null && <RawToolValue value={result.raw_content} />}
    </div>)}
  </div>;
}

function ToolValue({ label, value }: { label: string; value: unknown }) {
  const [expanded, setExpanded] = useState(false);
  const id = useId();
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2) ?? "（空）";
  const long = text.length > 1400;
  return <section className="chat-tool-value" aria-label={label}>
    <h4>{label}</h4>
    <pre id={id}><code>{long && !expanded ? `${text.slice(0, 1400)}\n…` : text}</code></pre>
    {long && <button type="button" aria-expanded={expanded} aria-controls={id} onClick={() => setExpanded(value => !value)}>
      {expanded ? "收起全文" : `展开全文 · ${text.length.toLocaleString()} 字符`}
    </button>}
  </section>;
}

function RawToolValue({ value }: { value: unknown }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return <div className="chat-tool-raw">
    <button type="button" aria-expanded={open} aria-controls={id} onClick={() => setOpen(value => !value)}>
      {open ? "收起原始返回" : "查看原始返回"}
    </button>
    {open && <div id={id}><ToolValue label="原始返回（已脱敏）" value={value} /></div>}
  </div>;
}
