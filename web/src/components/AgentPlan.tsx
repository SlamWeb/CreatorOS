import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { request } from "../api/client";
import "./agent-plan.css";

type PlanStatus = "draft" | "running" | "paused" | "needs_attention" | "completed";
type StepStatus = "pending" | "running" | "waiting" | "succeeded" | "failed" | "blocked" | "unknown" | "skipped";
type PlanSummary = { id: string; title: string; status: PlanStatus; version: number; pause_reason?: "checkpoint" | "manual" | null; continuation_state?: string | null };
type PlanStep = { id: string; title: string; tool: string; depends_on: string[]; status: StepStatus; result?: unknown; error?: unknown; retryable?: boolean };
type Plan = PlanSummary & { steps: PlanStep[] };
type PlanAction = { version: number; action: "pause" | "resume" | "retry"; stepId?: string };

const planNames: Record<PlanStatus, string> = { draft: "未开始", running: "进行中", paused: "已暂停", needs_attention: "需要处理", completed: "已完成" };
const stepNames: Record<StepStatus, string> = { pending: "待开始", running: "进行中", waiting: "等待结果", succeeded: "已完成", failed: "失败", blocked: "等待前一步", unknown: "结果待核对", skipped: "已跳过" };
const active = (state: string) => state === "running";
const awaitingAgent = (plan: PlanSummary) => (plan.pause_reason === "checkpoint" || plan.status === "needs_attention") && ["pending", "claimed"].includes(plan.continuation_state ?? "");
const planLabel = (plan: PlanSummary) => awaitingAgent(plan) ? (plan.continuation_state === "claimed" ? "Agent 正在判断" : "等待 Agent 判断") : plan.pause_reason === "checkpoint" && plan.status === "paused" ? "等待继续" : planNames[plan.status] ?? plan.status;
const planPath = (sessionId: string) => `/api/agent/sessions/${encodeURIComponent(sessionId)}/plans`;

function textResult(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  try { return JSON.stringify(value, null, 2) ?? ""; } catch { return ""; }
}

function localResultLink(value: unknown): string | null {
  let result = value;
  if (typeof result === "string") { try { result = JSON.parse(result); } catch { return null; } }
  if (!result || typeof result !== "object") return null;
  const url = (result as Record<string, unknown>).url;
  if (typeof url !== "string") return null;
  try { const parsed = new URL(url, window.location.origin); return parsed.origin === window.location.origin ? `${parsed.pathname}${parsed.search}${parsed.hash}` : null; } catch { return null; }
}

function Step({ step, names, onRetry, disabled, plan }: { step: PlanStep; names: Map<string, string>; onRetry: (id: string) => void; disabled: boolean; plan: PlanSummary }) {
  const [open, setOpen] = useState(false);
  const detail = textResult(step.error ?? step.result);
  const link = localResultLink(step.result);
  return <li className="agent-plan-step">
    <div className="agent-plan-step-heading"><strong>{step.title}</strong><span className={`agent-plan-status is-${step.status}`}>{step.tool === "request_review" && step.status === "succeeded" ? (awaitingAgent(plan) ? "等待判断" : "已发起判断") : stepNames[step.status] ?? step.status}</span></div>
    {step.depends_on.length > 0 && <p className="agent-plan-dependencies">{["pending", "blocked"].includes(step.status) ? "等待" : "依赖"}：{step.depends_on.map(id => names.get(id) ?? "前一步").join("、")}</p>}
    {(detail || link || (step.status === "failed" && step.retryable)) && <div className="agent-plan-step-actions">
      {link && <Link className="text-link" to={link}>查看结果 ↗</Link>}
      {detail && <button type="button" className="text-link" aria-expanded={open} onClick={() => setOpen(value => !value)}>{open ? "收起详情" : step.error ? "查看原因" : "查看详情"}</button>}
      {step.status === "failed" && step.retryable && <button type="button" className="text-link" disabled={disabled} onClick={() => onRetry(step.id)}>重试这一步</button>}
    </div>}
    {open && <pre className="agent-plan-detail">{detail}</pre>}
  </li>;
}

/** Plans are displayed only for this conversation; the host advances them independently. */
export function AgentPlan({ sessionId, visible, conversationRunning }: { sessionId: string; visible: boolean; conversationRunning: boolean }) {
  const client = useQueryClient();
  const [expanded, setExpanded] = useState(false);
  const [picked, setPicked] = useState<string | null>(null);
  const base = planPath(sessionId);
  const listKey = ["agent-plans", sessionId];
  const list = useQuery({ queryKey: listKey, queryFn: () => request<{ items: PlanSummary[] }>(base), enabled: visible && !!sessionId, retry: false,
    refetchInterval: query => visible && (conversationRunning || query.state.data?.items.some(item => active(item.status) || awaitingAgent(item))) ? 3000 : false });
  useEffect(() => { if (visible) void client.invalidateQueries({ queryKey: listKey }); }, [visible, conversationRunning, sessionId]);
  const summaries = list.data?.items ?? [];
  const selected = summaries.find(item => item.id === picked) ?? summaries.find(item => active(item.status) || awaitingAgent(item) || item.status === "needs_attention") ?? summaries[0];
  const detailKey = ["agent-plan", sessionId, selected?.id];
  const detail = useQuery({ queryKey: detailKey, queryFn: () => request<Plan>(`${base}/${encodeURIComponent(selected!.id)}`), enabled: visible && expanded && !!selected, retry: false,
    refetchInterval: query => visible && expanded && (conversationRunning || (query.state.data && (active(query.state.data.status) || awaitingAgent(query.state.data)))) ? 3000 : false });
  const control = useMutation({ retry: false,
    mutationFn: (input: PlanAction) => request<Plan>(`${base}/${encodeURIComponent(selected!.id)}/actions`, { method: "POST", body: JSON.stringify({ expected_version: input.version, action: input.action, ...(input.stepId ? { step_id: input.stepId } : {}) }) }),
    onSettled: async () => { await Promise.all([client.invalidateQueries({ queryKey: listKey }), client.invalidateQueries({ queryKey: detailKey })]); },
  });
  if (!visible || !sessionId || (!summaries.length && !list.isError)) return null;
  if (list.isError) return <div className="agent-plan"><p role="alert">计划读取失败：{list.error.message} <button className="text-link" type="button" onClick={() => void list.refetch()}>重试</button></p></div>;
  const plan = detail.data;
  const names = new Map(plan?.steps.map(step => [step.id, step.title]) ?? []);
  function perform(action: PlanAction["action"], stepId?: string) { if (plan && !control.isPending) control.mutate({ version: plan.version, action, stepId }); }
  return <section className="agent-plan" aria-label="任务计划">
    <div className="agent-plan-heading"><button type="button" className="agent-plan-toggle" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>
      <strong>任务计划</strong><span>{selected ? planLabel(selected) : ""}</span><span>{expanded ? "收起" : "查看"}</span></button></div>
    {expanded && <div className="agent-plan-content">
      {summaries.length > 1 && <label>选择计划<select aria-label="选择计划" value={selected?.id ?? ""} onChange={event => { setPicked(event.target.value); control.reset(); }}>{summaries.map(item => <option key={item.id} value={item.id}>{item.title} · {planLabel(item)}</option>)}</select></label>}
      {detail.isPending && <p role="status">正在读取计划…</p>}
      {detail.isError && <p role="alert">{detail.error.message} <button type="button" className="text-link" onClick={() => void detail.refetch()}>重新读取</button></p>}
      {plan && <><div className="agent-plan-title"><strong>{plan.title}</strong><span>{planLabel(plan)}</span></div>
        <ol className="agent-plan-steps">{plan.steps.map(step => <Step key={step.id} step={step} names={names} onRetry={id => perform("retry", id)} disabled={control.isPending} plan={plan} />)}</ol>
        <div className="agent-plan-controls">
          {plan.status === "running" && <button type="button" className="button button-secondary" disabled={control.isPending} onClick={() => perform("pause")}>暂停后续步骤</button>}
          {plan.status === "paused" && !awaitingAgent(plan) && <button type="button" className="button button-secondary" disabled={control.isPending} onClick={() => perform("resume")}>继续计划</button>}
        </div>
        {plan.status === "paused" && plan.pause_reason !== "checkpoint" && <p className="agent-plan-note">已派发的任务会继续运行。</p>}
      </>}
      {control.isError && <p role="alert">{control.error.message} 请检查当前状态后再操作。</p>}
    </div>}
  </section>;
}
