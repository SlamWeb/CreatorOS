import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, studioApi } from "../api/client";
import type { RunDetail } from "../api/types";

const stageLabels = { mind: "内容调研", visual: "图片制作", production: "生产" } as const;
const activityLabels = {
  thinking: "思考中", reading: "阅读资料", searching: "搜索中", tool_running: "工具执行中",
  responding: "正在整理回复", waiting: "等待中", completed: "已完成", failed: "失败",
} as const;
const phaseLabels = { planning: "准备内容", rendering: "逐页制图", assembling: "整理产物" } as const;

export function RunControls({ run, compact = false }: { run: RunDetail; compact?: boolean }) {
  const detailId = useId();
  const [detailOpen, setDetailOpen] = useState(false);
  const [stopping, setStopping] = useState(false);
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: (action: "execute" | "cancel") => action === "execute"
      ? studioApi.executeRun(run.id, run.version)
      : studioApi.cancelRun(run.id, { expected_version: run.version }),
    onSuccess: (_result, action) => { if (action === "cancel" && run.status === "producing") setStopping(true); },
    onSettled: () => queryClient.invalidateQueries(),
  });
  useEffect(() => { if (run.status !== "producing") setStopping(false); }, [run.id, run.status]);
  const queued = run.production_queue?.status === "pending";
  const blockedReason = queued ? run.production_queue?.blocked_reason : null;
  const canStart = !queued && (run.allowed_actions.includes("execute") || run.allowed_actions.includes("resume"));
  const active = ["producing", "validating"].includes(run.status);
  const stale = active && run.lease_expires_at && Date.parse(run.lease_expires_at) < Date.now();
  const latest = run.revisions.at(-1)?.attempts.at(-1);
  const elapsed = latest ? Math.max(0, Math.floor((Date.now() - Date.parse(latest.started_at)) / 1000)) : 0;
  const progress = run.production_progress;
  const lastActivity = progress ? Date.parse(progress.last_activity_at) : Number.NaN;
  const activityAgeSeconds = Number.isFinite(lastActivity) ? Math.max(0, Math.floor((Date.now() - lastActivity) / 1000)) : null;
  const noRecentActivity = active && progress?.status === "running" && activityAgeSeconds !== null && activityAgeSeconds > 120;
  const collectingReceipt = progress?.status === "completed" && active;
  const showProgress = active || ["failed", "interrupted"].includes(run.status);
  return <div className="run-controls">
    {queued && <p className="muted" role="status">{blockedReason ? `生产队列已暂停 · ${blockedReason}`
      : `已加入生产队列${run.production_queue?.position ? ` · 排队第 ${run.production_queue.position} 项` : ""}`}</p>}
    {active ? <p className="muted" role="status"><span className={!stale && !noRecentActivity ? "task-running-text" : undefined}>{stale ? "执行状态待核实，请检查本地服务。" : `${run.status === "validating" ? "正在检查产物" : "Codex 正在生产"}`}</span>{!stale && ` · 已运行 ${Math.floor(elapsed / 60)} 分 ${elapsed % 60} 秒`}</p> : null}
    {showProgress && progress ? <div className="muted run-live-progress" aria-label="生产阶段进度">
      <p>{stageLabels[progress.stage]} · {collectingReceipt ? "正在整理回执/验收" : ({ running: "进行中", completed: "阶段已完成", failed: "阶段失败", interrupted: "阶段中断" } as const)[progress.status]}</p>
      {compact && noRecentActivity && <p className="review-warning" role="status">暂无新活动 · {new Date(lastActivity).toLocaleString("zh-CN")}</p>}
      {progress.phase ? <p>{phaseLabels[progress.phase]}{progress.current_page !== null ? ` · 第 ${progress.current_page} 页` : ""}{progress.page_attempt > 0 ? ` · 第 ${progress.page_attempt} 次尝试` : ""} · 已完成 {progress.completed_pages} 页</p> : progress.completed_pages > 0 ? <p>已完成 {progress.completed_pages} 页</p> : null}
      {compact && <button type="button" className="text-link run-progress-toggle" aria-expanded={detailOpen} aria-controls={detailId} onClick={() => setDetailOpen(value => !value)}>{detailOpen ? "收起运行详情" : "运行详情"}</button>}
      <div id={detailId} hidden={compact && !detailOpen}>
      <p>{collectingReceipt ? "回执/验收整理中" : activityLabels[progress.activity]} · 最近活动 {noRecentActivity ? `暂无新活动 · ${new Date(lastActivity).toLocaleString("zh-CN")}` : Number.isFinite(lastActivity) ? new Date(lastActivity).toLocaleString("zh-CN") : "时间未记录"}</p>
      {progress.total_pages !== null ? <p>共 {progress.total_pages} 页</p> : null}
      <p>已完成工具调用 {progress.completed_tool_calls} 次</p>
      </div>
    </div> : active ? <p className="muted">未记录阶段进度</p> : null}
    <div className="form-actions">
      {canStart ? <button className="button button-primary" disabled={mutation.isPending} onClick={() => mutation.mutate("execute")}>{mutation.isPending ? "正在提交…" : run.status === "queued" && run.production_queue?.status !== "needs_attention" ? "开始生产" : "恢复生产"}</button> : null}
      {run.allowed_actions.includes("cancel") ? <button className="button button-quiet" disabled={mutation.isPending || stopping} onClick={() => mutation.mutate("cancel")}>{stopping ? "正在停止…" : run.status === "producing" ? "停止" : "取消此任务"}</button> : null}
    </div>
    {mutation.isError ? <p className="form-error" role="alert">{mutation.error.message} {mutation.error instanceof ApiError && mutation.error.runId ? <Link to={`/runs/${mutation.error.runId}`}>查看当前运行 →</Link> : null}</p> : null}
  </div>;
}
