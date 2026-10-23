export const REASONING_EFFORTS = ["low", "medium", "high", "xhigh", "ultra"] as const;
export type ReasoningEffort = typeof REASONING_EFFORTS[number];
export type ReplayReasoningEffort = ReasoningEffort | "none" | "max";
export type ContextUsage = {
  input_tokens: number; context_window: number; measurement: "actual" | "estimate";
  source: "last_model_request"; observed_at: string; turn_id: string | null;
};
export type ChatContextTarget = {
  series_id: string; topic_id: string; run_id?: string; revision_id?: string;
};
export type ChatContextFocus = { target: ChatContextTarget; title: string };
export type PendingAgentTurn = {
  sessionId: string; text: string; request_id: string; expected_version: number;
  attachment_ids: string[]; reasoning_effort: ReplayReasoningEffort;
  iteration_mode?: boolean;
  context_target?: ChatContextTarget;
};
export function isChatContextTarget(value: unknown): value is ChatContextTarget {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const target = value as Record<string, unknown>;
  const fields = ["series_id", "topic_id", "run_id", "revision_id"];
  return Object.keys(target).every(key => fields.includes(key)) &&
    ["series_id", "topic_id"].every(key => typeof target[key] === "string") &&
    (target.revision_id === undefined || typeof target.run_id === "string") &&
    Object.values(target).every(item => typeof item === "string" && /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(item));
}
export const contextFocusDraftKey = (scope: string) => `creatoros.agent.context-focus.v1:${encodeURIComponent(scope)}`;
export function parseContextFocusDraft(raw: string): ChatContextFocus | null {
  try {
    const value = JSON.parse(raw) as ChatContextFocus;
    return value && isChatContextTarget(value.target) && typeof value.title === "string" &&
      !!value.title.trim() && value.title.length <= 240 ? value : null;
  } catch { return null; }
}
export const sameContextTarget = (first: ChatContextTarget | undefined, second: ChatContextTarget | undefined) =>
  first?.series_id === second?.series_id && first?.topic_id === second?.topic_id &&
  first?.run_id === second?.run_id && first?.revision_id === second?.revision_id;
export const isReasoningEffort = (value: unknown): value is ReasoningEffort =>
  REASONING_EFFORTS.some(option => option === value);
export const isReplayReasoningEffort = (value: unknown): value is ReplayReasoningEffort =>
  isReasoningEffort(value) || value === "none" || value === "max";
export const initialReasoningEffort = (value?: string): string =>
  !value || value === "none" || value === "max" ? "high" : value;
export const reasoningPreferenceKey = (key: string) => `creatoros.agent.reasoning.v1:${encodeURIComponent(key)}`;
export const pendingAgentTurnKey = (sessionId: string) => `creatoros.agent.pending-turn.v1:${sessionId}`;
export function parsePendingAgentTurn(raw: string, sessionId: string): PendingAgentTurn | null {
  try {
    const value = JSON.parse(raw) as PendingAgentTurn;
    return value.sessionId === sessionId && typeof value.text === "string" &&
      typeof value.request_id === "string" && !!value.request_id &&
      Number.isInteger(value.expected_version) && value.expected_version >= 0 &&
      Array.isArray(value.attachment_ids) && value.attachment_ids.every(id => typeof id === "string") &&
      isReplayReasoningEffort(value.reasoning_effort) &&
      (value.context_target === undefined || isChatContextTarget(value.context_target)) &&
      (value.iteration_mode === undefined || typeof value.iteration_mode === "boolean") ? value : null;
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
