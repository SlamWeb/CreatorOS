export const REASONING_EFFORTS = ["none", "low", "high", "max"] as const;
export type ReasoningEffort = typeof REASONING_EFFORTS[number];
export type ContextUsage = {
  input_tokens: number; context_window: number; measurement: "actual" | "estimate";
  source: "last_model_request"; observed_at: string; turn_id: string | null;
};
export type PendingAgentTurn = {
  sessionId: string; text: string; request_id: string; expected_version: number;
  attachment_ids: string[]; reasoning_effort: ReasoningEffort;
};
export const isReasoningEffort = (value: unknown): value is ReasoningEffort =>
  REASONING_EFFORTS.some(option => option === value);
export const reasoningPreferenceKey = (key: string) => `creatoros.agent.reasoning.v1:${encodeURIComponent(key)}`;
export const pendingAgentTurnKey = (sessionId: string) => `creatoros.agent.pending-turn.v1:${sessionId}`;
export function parsePendingAgentTurn(raw: string, sessionId: string): PendingAgentTurn | null {
  try {
    const value = JSON.parse(raw) as PendingAgentTurn;
    return value.sessionId === sessionId && typeof value.text === "string" &&
      typeof value.request_id === "string" && !!value.request_id &&
      Number.isInteger(value.expected_version) && value.expected_version >= 0 &&
      Array.isArray(value.attachment_ids) && value.attachment_ids.every(id => typeof id === "string") &&
      isReasoningEffort(value.reasoning_effort) ? value : null;
  } catch { return null; }
}
export function contextUsageView(usage: ContextUsage | null | undefined) {
  if (!usage || !Number.isFinite(usage.input_tokens) || usage.input_tokens < 0 ||
    !Number.isFinite(usage.context_window) || usage.context_window <= 0 ||
    !["actual", "estimate"].includes(usage.measurement) || usage.source !== "last_model_request") return null;
  const window = usage.context_window === 1_000_000 ? "1M" : usage.context_window.toLocaleString("zh-CN");
  return {
    label: `上下文已用 ${usage.input_tokens.toLocaleString("zh-CN")} / ${window}`,
    source: usage.measurement === "actual" ? "上次模型请求 · 实际输入" : "上次模型请求 · 估算输入",
    ratio: Math.min(1, usage.input_tokens / usage.context_window),
  };
}
