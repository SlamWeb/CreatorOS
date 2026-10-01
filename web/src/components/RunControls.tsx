import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ApiError, studioApi } from "../api/client";
import type { RunDetail } from "../api/types";

const stageLabels = { mind: "内容调研", visual: "图片制作", production: "生产" } as const;
const activityLabels = {
  thinking: "思考中", reading: "阅读资料", searching: "搜索中", tool_running: "工具执行中",
  responding: "正在整理回复", waiting: "等待中", completed: "已完成", failed: "失败",
} as const;

export function RunControls({ run }: { run: RunDetail }) {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: (action: "execute" | "cancel") => action === "execute"
      ? studioApi.executeRun(run.id, run.version)
      : studioApi.cancelRun(run.id, { expected_version: run.version }),
    onSettled: () => queryClient.invalidateQueries(),
  });
  const canStart = run.allowed_actions.includes("execute") || run.allowed_actions.includes("resume");
  const active = ["producing", "validating"].includes(run.status);
  const stale = active && run.lease_expires_at && Date.parse(run.lease_expires_at) < Date.now();
  const latest = run.revisions.at(-1)?.attempts.at(-1);
  const elapsed = latest ? Math.max(0, Math.floor((Date.now() - Date.parse(latest.started_at)) / 1000)) : 0;
  const progress = run.production_progress;
  const lastActivity = progress ? Date.parse(progress.last_activity_at) : Number.NaN;
  const activityAgeSeconds = Number.isFinite(lastActivity) ? Math.max(0, Math.floor((Date.now() - lastActivity) / 1000)) : null;
  const noRecentActivity = active && progress?.status === "running" && activityAgeSeconds !== null && activityAgeSeconds > 120;
  const collectingReceipt = progress?.status === "completed" && active;
  const hasProductionHistory = run.status !== "queued";
  return <div className="run-controls">
    {active ? <p className="muted" role="status">{stale ? "执行状态待核实，请检查本地服务。" : `${run.status === "validating" ? "正在检查产物" : "Codex 正在生产"} · 已运行 ${Math.floor(elapsed / 60)} 分 ${elapsed % 60} 秒`}</p> : null}
    {progress ? <div className="muted" aria-label="生产阶段进度">
      <p>{stageLabels[progress.stage]} · {collectingReceipt ? "正在整理回执/验收" : ({ running: "进行中", completed: "阶段已完成", failed: "阶段失败", interrupted: "阶段中断" } as const)[progress.status]}</p>
      <p>{collectingReceipt ? "回执/验收整理中" : activityLabels[progress.activity]} · 最近活动 {noRecentActivity ? `暂无新活动 · ${new Date(lastActivity).toLocaleString("zh-CN")}` : Number.isFinite(lastActivity) ? new Date(lastActivity).toLocaleString("zh-CN") : "时间未记录"}</p>
      {progress.total_pages !== null ? <p>共 {progress.total_pages} 页</p> : null}
      <p>已完成工具调用 {progress.completed_tool_calls} 次</p>
    </div> : hasProductionHistory ? <p className="muted">未记录阶段进度</p> : null}
    <div className="form-actions">
      {canStart ? <button className="button button-primary" disabled={mutation.isPending} onClick={() => mutation.mutate("execute")}>{mutation.isPending ? "正在提交…" : run.status === "queued" ? "开始生产" : "恢复生产"}</button> : null}
      {run.allowed_actions.includes("cancel") ? <button className="button button-quiet" disabled={mutation.isPending} onClick={() => mutation.mutate("cancel")}>取消此任务</button> : null}
      <Link className="text-link" to={`/series/${run.series_id}`}>返回栏目 →</Link>
    </div>
    {canStart ? <p className="muted">开始或恢复会调用本机已登录的 Codex，并消耗用量。</p> : null}
    {mutation.isError ? <p className="form-error">{mutation.error.message} {mutation.error instanceof ApiError && mutation.error.runId ? <Link to={`/runs/${mutation.error.runId}`}>查看当前运行 →</Link> : null}</p> : null}
  </div>;
}
