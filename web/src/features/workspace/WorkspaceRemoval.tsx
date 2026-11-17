import { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { lifecycleApi, type RemovalStatus, type WorkspaceObject } from "../../api/workspaceLifecycle";

const reasonLabels: Record<string, string> = {
  active_runs: "正在制作的任务",
  authorized_queue: "已授权排队的任务",
  waiting_tasks: "等待前置步骤的任务",
  research: "排队或进行中的调研",
  publishing: "正在发送或核验的发布任务",
  material_jobs: "正在导入或分析的旧账号音频",
  discussions: "排队或进行中的作品讨论",
  discussion_unreadable: "需要核对的讨论记录",
  agent_plans: "待审核或执行中的 Agent 计划",
  agent_chat: "进行中的账号对话",
  skill_iterations: "进行中的 Skill 改稿",
  skill_calibrations: "进行中的 Skill 校准",
};

export function WorkspaceRemoval({ type, id, name, busy, error, onConfirm, onCancel }: {
  type: WorkspaceObject; id: string; name: string; busy: boolean; error?: string | null;
  onConfirm: (status: RemovalStatus) => void; onCancel: () => void;
}) {
  const preview = useQuery({ queryKey: ["workspace-removal", type, id], queryFn: ({ signal }) => lifecycleApi.preview(type, id, signal),
    staleTime: 0, retry: false });
  const cancel = useRef<HTMLButtonElement>(null);
  useEffect(() => { cancel.current?.focus(); }, []);
  const status = preview.data?.status;
  const subject = type === "creators" ? "账号" : "栏目";
  return <section className="workspace-removal" aria-label={`删除或移出${subject}`}>
    <p className="workspace-removal-name"><strong>{name}</strong></p>
    {preview.isFetching ? <p role="status">正在核对任务和历史数据…</p> : preview.isError ? <p className="form-error" role="alert">{preview.error.message} <button type="button" className="text-link" onClick={() => void preview.refetch()}>重新核对</button></p> :
      status === "blocked" ? <><p role="status">暂时无法移出。以下任务结束后可以重新核对。</p>
        <ul>{preview.data?.reasons.map(reason => <li key={reason.code}>{reasonLabels[reason.code] ?? reason.code}（{reason.count}）</li>)}</ul>
        <button type="button" className="button button-secondary" onClick={() => void preview.refetch()}>重新核对</button></>
      : status === "archived" ? <p>从工作区移出此{subject}，保留历史数据。你可以在「已移出的账号与栏目」中恢复。</p>
      : status === "deleted" ? <p>此{subject}没有需要保留的数据。永久删除后无法恢复。</p> : null}
    {error && <p className="form-error" role="alert">{error}</p>}
    <div className="workspace-lifecycle-actions">
      <button type="button" ref={cancel} className="button button-secondary" disabled={busy} onClick={onCancel}>取消</button>
      {status && status !== "blocked" && <button type="button" className="button button-danger"
        disabled={busy || preview.isFetching || preview.isError} onClick={() => onConfirm(status)}>
        {busy ? "处理中…" : status === "deleted" ? "永久删除" + subject : "移出工作区"}
      </button>}
    </div>
  </section>;
}
