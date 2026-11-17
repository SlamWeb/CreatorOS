import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { studioApi } from "../../api/client";
import type { CreatorDirectoryItem } from "../../api/types";
import { lifecycleApi, type WorkspaceObject } from "../../api/workspaceLifecycle";
import { StatusPill } from "../../components/StatusPill";
import { WorkspaceDialog } from "../../components/WorkspaceDialog";
import { refreshWorkspace } from "./workspaceData";

type RecoveryObject = { type: WorkspaceObject; id: string; name: string };

export function WorkspaceRecovery({ accounts, selectedCreator, onClose }: {
  accounts: CreatorDirectoryItem[]; selectedCreator?: CreatorDirectoryItem; onClose: () => void;
}) {
  const client = useQueryClient();
  const [offset, setOffset] = useState(0);
  const [history, setHistory] = useState<RecoveryObject | null>(null);
  const retired = useQuery({ queryKey: ["creators", "archived", offset], queryFn: ({ signal }) => lifecycleApi.accounts(offset, signal), retry: false });
  const columns = useQuery({ queryKey: ["series-all", "archived"], queryFn: ({ signal }) => lifecycleApi.series(signal), retry: false });
  const restore = useMutation({ retry: false, mutationFn: (target: RecoveryObject) => lifecycleApi.restore(target.type, target.id),
    onSettled: async (_, __, target) => {
      await Promise.all([refreshWorkspace(client), client.invalidateQueries({ queryKey: ["creator", target.id] }), client.invalidateQueries({ queryKey: ["run"] }),
        client.invalidateQueries({ queryKey: ["workspace-removal"] })]);
    } });
  const retiredAccounts = retired.data?.items ?? [];
  const accountNames = new Map([...retiredAccounts, ...accounts].map(account => [account.id, account.display_name]));
  if (selectedCreator) accountNames.set(selectedCreator.id, selectedCreator.display_name);
  const activeIds = new Set(accounts.map(account => account.id));
  const columnRows = columns.data ?? [];
  function actions(target: RecoveryObject, parentRemoved = false) {
    const showHistory = history?.id === target.id && history.type === target.type;
    return <div className="workspace-lifecycle-actions">
      <button className="text-link" type="button" aria-expanded={showHistory} aria-label={`查看历史 ${target.name}`}
        onClick={() => setHistory(showHistory ? null : target)}>{showHistory ? "收起历史" : "查看历史"}</button>
      <button className="button button-secondary" type="button" disabled={restore.isPending || parentRemoved}
        aria-label={`恢复 ${target.name}`} onClick={() => restore.mutate(target)}>
        {restore.isPending && restore.variables?.id === target.id ? "正在恢复…" : "恢复"}
      </button>
    </div>;
  }
  return <WorkspaceDialog title="已移出的账号与栏目" className="workspace-recovery-dialog" busy={restore.isPending} onClose={onClose}>
    <p className="workspace-recovery-intro">恢复后的账号或栏目会重新出现在工作区。</p>
    {restore.isSuccess && <p role="status">已恢复「{restore.variables.name}」。</p>}
    {restore.isError && <p className="form-error" role="alert">{restore.error.message}</p>}
    <section className="workspace-recovery-group" aria-label="已移出的账号">
      <h3>账号</h3>
      {retired.isPending ? <p role="status">正在读取账号…</p> : retired.isError ? <p className="form-error" role="alert">{retired.error.message} <button type="button" className="text-link" onClick={() => void retired.refetch()}>重新读取</button></p> :
        retiredAccounts.length ? <ul>{retiredAccounts.map(item => <li key={item.id}>
          <strong>{item.display_name}</strong>{actions({ type: "creators", id: item.id, name: item.display_name })}
        </li>)}</ul> : <p>此页没有已移出的账号。</p>}
      {retired.data && (offset > 0 || offset + retired.data.page.limit < retired.data.page.total) && <div className="workspace-lifecycle-actions">
        <button type="button" className="button button-quiet" disabled={!offset} onClick={() => setOffset(value => Math.max(0, value - 50))}>上一页账号</button>
        <button type="button" className="button button-quiet" disabled={offset + 50 >= retired.data.page.total} onClick={() => setOffset(value => value + 50)}>下一页账号</button>
      </div>}
    </section>
    <section className="workspace-recovery-group" aria-label="已移出的栏目">
      <h3>栏目</h3>
      {columns.isPending ? <p role="status">正在读取栏目…</p> : columns.isError ? <p className="form-error" role="alert">{columns.error.message} <button type="button" className="text-link" onClick={() => void columns.refetch()}>重新读取</button></p> :
        columnRows.length ? <ul>{columnRows.map(item => {
          const parentRemoved = !!item.creator_id && !activeIds.has(item.creator_id);
          return <li key={item.id}>
            <div><strong>{item.name}</strong><small>{item.creator_id ? accountNames.get(item.creator_id) ?? "所属账号" : "未分配栏目"}{parentRemoved && " · 请先恢复所属账号"}</small></div>
            {actions({ type: "series", id: item.id, name: item.name }, parentRemoved)}
          </li>;
        })}</ul> : <p>还没有已移出的栏目。</p>}
    </section>
    {history && <RecoveryHistory key={history.id} target={history} />}
  </WorkspaceDialog>;
}

function RecoveryHistory({ target }: { target: RecoveryObject }) {
  const [offset, setOffset] = useState(0);
  const section = useRef<HTMLElement>(null);
  useEffect(() => { section.current?.focus(); }, []);
  const runs = useQuery({ queryKey: ["runs", "workspace-recovery", target.type, target.id, offset],
    queryFn: () => studioApi.runsPage(offset, target.type === "creators" ? target.id : undefined, target.type === "series" ? target.id : undefined), retry: false });
  return <section ref={section} tabIndex={-1} className="workspace-recovery-group" aria-label="历史制作记录">
    <h3>{target.name}的历史制作记录</h3>
    {runs.isPending ? <p role="status">正在读取历史制作记录…</p> : runs.isError ? <p className="form-error" role="alert">{runs.error.message} <button type="button" className="text-link" onClick={() => void runs.refetch()}>重新读取</button></p> :
      runs.data.items.length ? <ul>{runs.data.items.map(run => <li key={run.id}>
        <Link to={`/runs/${encodeURIComponent(run.id)}`}>{run.topic_title}</Link><StatusPill status={run.status} />
      </li>)}</ul> : <p>没有历史制作记录。</p>}
    {runs.data && (offset > 0 || offset + runs.data.page.limit < runs.data.page.total) && <div className="workspace-lifecycle-actions">
      <button type="button" className="button button-quiet" disabled={!offset} onClick={() => setOffset(value => Math.max(0, value - 50))}>上一页历史</button>
      <button type="button" className="button button-quiet" disabled={offset + 50 >= runs.data.page.total} onClick={() => setOffset(value => value + 50)}>下一页历史</button>
    </div>}
  </section>;
}
