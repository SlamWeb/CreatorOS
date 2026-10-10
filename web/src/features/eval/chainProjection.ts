export type EvidenceRecord = Record<string, unknown>;
export type EvidenceMessage = EvidenceRecord & { role: string };
export interface RecordedCall { id: string; name: string; arguments: unknown; problem?: string }
export interface RecordedResponse { content: string; calls: RecordedCall[]; finish: string; problem?: string }
export interface RecordedRequest {
  id: string;
  index: number;
  kind: string;
  method: string;
  context: EvidenceRecord | null;
  messages: EvidenceMessage[] | null;
  account: EvidenceRecord | null;
  response: RecordedResponse;
  trace: EvidenceRecord[];
}
export interface DatabaseDifference {
  name: string;
  before: number | null;
  after: number | null;
  same: boolean | null;
  removed: EvidenceRecord[];
  added: EvidenceRecord[];
}

export function asRecord(value: unknown): EvidenceRecord | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as EvidenceRecord : null;
}

export const asText = (value: unknown) => typeof value === "string" ? value : "";
export const displayValue = (value: unknown) => typeof value === "string" ? value : JSON.stringify(value, null, 2) ?? "未记录";

export function recordArray(value: unknown): EvidenceRecord[] | null {
  return Array.isArray(value) && value.every(item => asRecord(item)) ? value as EvidenceRecord[] : null;
}

export function messageArray(value: unknown): EvidenceMessage[] | null {
  const rows = recordArray(value);
  return rows && rows.every(item => typeof item.role === "string") ? rows as EvidenceMessage[] : null;
}

export function readCall(value: unknown): RecordedCall {
  const row = asRecord(value);
  const fn = asRecord(row?.function) ?? row;
  const id = asText(row?.id);
  const name = asText(fn?.name);
  const args = fn?.arguments;
  return { id, name, arguments: args, ...(!id || !name || (typeof args !== "string" && !asRecord(args))
    ? { problem: "调用 ID、工具名或参数结构未完整记录，无法判断协议。" } : {}) };
}

export function readResponse(request: EvidenceRecord): RecordedResponse {
  const response = asRecord(request.response);
  if (response) {
    const calls = response.tool_calls == null ? [] : Array.isArray(response.tool_calls) ? response.tool_calls.map(readCall) : null;
    const content = response.content;
    return { content: asText(content), calls: calls ?? [], finish: asText(response.finish_reason) || asText(request.finish_reason),
      ...(!calls || (content != null && typeof content !== "string") ? { problem: "完整响应结构不受支持，无法判断。" } : {}) };
  }
  const events = recordArray(request.events);
  if (request.method !== "stream" || !events) return { content: "", calls: [], finish: "", problem: "未记录可识别的模型响应，无法判断。" };
  const parts: string[] = [];
  const calls = new Map<number, RecordedCall>();
  const ends: string[] = [];
  let problem = "";
  for (const event of events) {
    if (event.type === "TextDelta" && typeof event.content === "string") parts.push(event.content);
    else if (event.type === "ToolCallDelta" && Number.isInteger(event.index) && Number(event.index) >= 0
      && (event.arguments == null || typeof event.arguments === "string")) {
      const index = Number(event.index);
      const call = calls.get(index) ?? { id: "", name: "", arguments: "" };
      call.id = asText(event.id) || call.id;
      call.name = asText(event.name) || call.name;
      call.arguments = asText(call.arguments) + asText(event.arguments);
      calls.set(index, call);
    } else if (event.type === "StreamEnd") ends.push(asText(event.finish_reason));
    else problem = "包含未知或损坏的流式事件，响应投影不完整，无法判断。";
  }
  const projected = [...calls.entries()].sort(([a], [b]) => a - b).map(([, call]) => readCall(call));
  if (ends.length !== 1 || !ends[0] || (request.finish_reason != null && ends[0] !== request.finish_reason)) {
    problem ||= "缺少唯一且一致的 StreamEnd；已记录输出不能证明请求完整结束。";
  }
  return { content: parts.join(""), calls: projected, finish: ends.at(-1) ?? "", ...(problem ? { problem } : {}) };
}

export function accountFromMessages(messages: EvidenceMessage[] | null): EvidenceRecord | null {
  for (const message of messages ?? []) {
    const content = asText(message.content);
    const start = content.indexOf("{");
    if (start < 0) continue;
    try {
      const data = asRecord(JSON.parse(content.slice(start)));
      if (data?.kind === "creator_context" && asRecord(data.creator) && recordArray(data.series) && recordArray(data.skills)) return data;
    } catch { /* Ordinary user text is not structured host context. */ }
  }
  return null;
}

export function projectRequests(value: unknown, traceValue: unknown): RecordedRequest[] | null {
  const rows = recordArray(value);
  if (!rows) return null;
  const traceRows = recordArray(traceValue) ?? [];
  return rows.map((row, index) => {
    const id = asText(row.trace_request_id) || asText(row.request_id);
    const trace = id ? traceRows.filter(item => item.request_id === id) : [];
    const started = trace.find(item => item.event === "started");
    const context = asRecord(row.context);
    const messages = messageArray(context?.messages);
    const account = accountFromMessages(messages);
    return { id, index, kind: asText(started?.request_kind) || asText(row.request_kind), method: asText(row.method), context, messages,
      account, response: readResponse(row), trace };
  });
}

export function toolResults(callId: string, ledger: EvidenceMessage[] | null, requests: RecordedRequest[]) {
  if (!callId) return [];
  const fromLedger = ledger?.filter(row => row.role === "tool" && row.tool_call_id === callId) ?? [];
  if (fromLedger.length) return fromLedger.map(row => ({ row, source: "messages.json" }));
  const seen = new Set<string>();
  const results: { row: EvidenceMessage; source: string }[] = [];
  for (const request of requests) for (const row of request.messages ?? []) {
    if (row.role !== "tool" || row.tool_call_id !== callId) continue;
    const key = canonical(row);
    if (!seen.has(key)) { results.push({ row, source: "requests.json" }); seen.add(key); }
  }
  return results;
}

function canonical(value: unknown): string {
  const row = asRecord(value);
  if (row) return JSON.stringify(Object.fromEntries(Object.keys(row).sort().map(key => [key, JSON.parse(canonical(row[key]))])));
  if (Array.isArray(value)) return JSON.stringify(value.map(item => JSON.parse(canonical(item))));
  return JSON.stringify(value) ?? "null";
}

function subtractRows(left: EvidenceRecord[], right: EvidenceRecord[]) {
  const remaining = new Map<string, number>();
  for (const row of right) { const key = canonical(row); remaining.set(key, (remaining.get(key) ?? 0) + 1); }
  return left.filter(row => {
    const key = canonical(row);
    const count = remaining.get(key) ?? 0;
    if (!count) return true;
    remaining.set(key, count - 1);
    return false;
  });
}

export function compareDatabase(beforeValue: unknown, afterValue: unknown): DatabaseDifference[] | null {
  const before = asRecord(asRecord(beforeValue)?.database);
  const after = asRecord(asRecord(afterValue)?.database);
  if (!before || !after) return null;
  return [...new Set([...Object.keys(before), ...Object.keys(after)])].sort().map(name => {
    const first = recordArray(before[name]);
    const last = recordArray(after[name]);
    const removed = first && last ? subtractRows(first, last) : [];
    const added = first && last ? subtractRows(last, first) : [];
    return { name, before: first?.length ?? null, after: last?.length ?? null,
      same: first && last ? removed.length === 0 && added.length === 0 : null, removed, added };
  });
}

export function compareFiles(beforeValue: unknown, afterValue: unknown) {
  const before = asRecord(asRecord(beforeValue)?.files);
  const after = asRecord(asRecord(afterValue)?.files);
  if (!before || !after || !Object.values(before).every(value => typeof value === "string")
    || !Object.values(after).every(value => typeof value === "string")) return null;
  const changed = [...new Set([...Object.keys(before), ...Object.keys(after)])].filter(name => before[name] !== after[name]);
  return { before: Object.keys(before).length, after: Object.keys(after).length, changed };
}
