import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ApiError, studioApi } from "../api/client";
import type { RunDetail } from "../api/types";

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
  return <div className="run-controls">
    {active ? <p className="muted" role="status">{stale ? "执行状态待核实，请检查本地服务。" : `${run.status === "validating" ? "正在检查产物" : "Codex 正在生产"} · 已运行 ${Math.floor(elapsed / 60)} 分 ${elapsed % 60} 秒`}</p> : null}
    <div className="form-actions">
      {canStart ? <button className="button button-primary" disabled={mutation.isPending} onClick={() => mutation.mutate("execute")}>{mutation.isPending ? "正在提交…" : run.status === "queued" ? "开始生产" : "恢复生产"}</button> : null}
      {run.allowed_actions.includes("cancel") ? <button className="button button-quiet" disabled={mutation.isPending} onClick={() => mutation.mutate("cancel")}>取消此任务</button> : null}
      <Link className="text-link" to={`/series/${run.series_id}`}>返回栏目 →</Link>
    </div>
    {canStart ? <p className="muted">开始或恢复会调用本机已登录的 Codex，并消耗用量。</p> : null}
    {mutation.isError ? <p className="form-error">{mutation.error.message} {mutation.error instanceof ApiError && mutation.error.runId ? <Link to={`/runs/${mutation.error.runId}`}>查看当前运行 →</Link> : null}</p> : null}
  </div>;
}
