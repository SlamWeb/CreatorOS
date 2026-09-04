import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { studioApi } from "../../api/client";
import { useOperation, useOverview } from "../../api/hooks";
import type { PendingOperationView, PreviewTopic } from "../../api/types";

const labels: Record<PendingOperationView["status"], string> = {
  awaiting_approval: "等待确认", needs_clarification: "需要补充", unsupported: "请调整要求",
  stale: "队列已变化", succeeded: "已写入 · 尚未生产", cancelled: "已取消", failed: "执行失败",
};
function Topics({ topics }: { topics: PreviewTopic[] }) {
  return <ol>{topics.map(t => <li key={t.topic_id}>{t.title}{t.brief && <small className="topic-brief">{t.brief}</small>}</li>)}</ol>;
}
export function OperationDrawer() {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const cache = useQueryClient();
  const id = params.get("operation"), command = params.get("command");
  const opened = !!id || !!command;
  const query = useOperation(id), overview = useOverview();
  const [offset, setOffset] = useState(0);
  const plans = useQuery({ queryKey: ["operations", offset], queryFn: () => studioApi.operations(offset), enabled: command === "list" });
  const [draft, setDraft] = useState(""), [scope, setScope] = useState("");
  const [editText, setEditText] = useState<Record<string, string>>({});
  const [reviewed, setReviewed] = useState<PendingOperationView | null>(null);
  const [notice, setNotice] = useState<PendingOperationView | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const navigation = useRef(location.key);
  navigation.current = location.key;
  const scopeParam = params.get("series");
  useEffect(() => { if (command === "new" && scopeParam) setScope(scopeParam); }, [command, scopeParam]);
  useEffect(() => {
    if (query.data && query.data.id === id) setReviewed(old => old?.id === id ? old : query.data!);
  }, [query.data, id]);
  useEffect(() => {
    if (!opened) return;
    const trigger = document.activeElement as HTMLElement | null;
    const overflow = document.documentElement.style.overflow;
    document.documentElement.style.overflow = "hidden";
    dialog.current?.showModal();
    return () => { dialog.current?.close(); document.documentElement.style.overflow = overflow; trigger?.focus(); };
  }, [opened]);
  useEffect(() => {
    const keydown = (event: globalThis.KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault(); setParams(p => { p.delete("operation"); p.set("command", "new"); return p; });
      }
    };
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, [setParams]);
  const close = () => setParams(p => { p.delete("operation"); p.delete("command"); p.delete("series"); return p; });
  const show = (operationId: string) => setParams(p => { p.delete("command"); p.delete("series"); p.set("operation", operationId); return p; });
  const list = () => setParams(p => { p.delete("operation"); p.set("command", "list"); return p; });
  const refresh = () => {
    for (const key of ["overview", "operations", "topics", "series"]) void cache.invalidateQueries({ queryKey: [key] });
  };
  const accept = (next: PendingOperationView) => {
    cache.setQueryData(["operation", next.id], next);
    setReviewed(old => old?.id === next.id ? next : old);
    refresh();
  };
  const propose = useMutation({
    retry: false,
    mutationFn: (input: { text: string; scope: string; navigation: string }) => studioApi.proposeOperation({ request_text: input.text, series_id: input.scope || null }),
    onSuccess: (next, input) => {
      cache.setQueryData(["operation", next.id], next); refresh();
      if (navigation.current === input.navigation) { setDraft(""); show(next.id); }
      else setNotice(next);
    },
  });
  const change = useMutation({
    retry: false,
    mutationFn: ({ kind, snapshot, instruction }: { kind: "edit" | "confirm" | "cancel"; snapshot: PendingOperationView; instruction?: string }) => {
      const version = { expected_version: snapshot.version, expected_revision: snapshot.revision };
      if (kind === "edit") return studioApi.editOperation(snapshot.id, { ...version, instruction: instruction! });
      if (kind === "confirm") return studioApi.confirmOperation(snapshot.id, { ...version, confirmation_token: snapshot.confirmation_token! });
      return studioApi.cancelOperation(snapshot.id, version);
    },
    onSuccess: (next, input) => {
      accept(next);
      if (input.kind === "edit") setEditText(old => ({ ...old, [next.id]: "" }));
    },
    onError: () => { void query.refetch(); },
  });
  const submit = () => {
    if (draft.trim() && !propose.isPending) propose.mutate({ text: draft.trim(), scope, navigation: navigation.current });
  };
  const enter = (event: KeyboardEvent<HTMLTextAreaElement>, action: () => void) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); action(); }
  };
  const operation = reviewed?.id === id ? reviewed : null;
  const updated = operation && query.data && operation.version !== query.data.version;
  const text = id ? editText[id] ?? "" : "";
  const editable = operation && ["awaiting_approval", "needs_clarification", "unsupported", "stale"].includes(operation.status);
  const changes = operation?.preview?.changes ?? [];
  const finalQueues = [...new Map(changes.map(c => [c.series_id, c])).values()];
  const hasError = change.isError && change.variables?.snapshot.id === id;
  const disabled = change.isPending || !!updated || hasError;
  const edit = () => { if (operation && !disabled && text.trim()) change.mutate({ kind: "edit", snapshot: operation, instruction: text.trim() }); };
  return <>
    {notice && <div className="command-notice" role="status">计划已就绪 <button onClick={() => { show(notice.id); setNotice(null); }}>查看</button><button aria-label="关闭通知" onClick={() => setNotice(null)}>×</button></div>}
    <dialog className="command-drawer" ref={dialog} aria-label="运营指令" onCancel={e => { e.preventDefault(); close(); }}>
      <div className="drawer-head"><div><p className="section-kicker">OPERATIONS</p><h2>{id ? "检查运营计划" : command === "list" ? "已保存的计划" : "用一句话调整选题"}</h2></div><button className="icon-button" aria-label="关闭运营指令" onClick={close}>×</button></div>
      {command === "list" && !id ? <>
        <p>计划已保存在本地；打开不会重新调用模型。</p>
        {plans.isPending ? <p>正在读取…</p> : plans.isError ? <p role="alert">{plans.error.message}<button onClick={() => void plans.refetch()}>重试</button></p> : <>
          {plans.data.items.map(p => <button className="saved-plan" key={p.id} onClick={() => show(p.id)}><b>{p.request_text}</b><small>{labels[p.status]}</small></button>)}
          {!plans.data.items.length && <p>暂无已保存计划。</p>}
          <div className="drawer-foot"><button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>上一页</button><span>{offset + 1} / {plans.data.page.total}</span><button disabled={offset + 20 >= plans.data.page.total} onClick={() => setOffset(offset + 20)}>下一页</button></div>
        </>}
      </> : !id ? <form onSubmit={e => { e.preventDefault(); submit(); }}>
        <p className="drawer-lede">只生成预览，确认后才写入选题队列。不会开始生产或发布。</p>
        <label>作用范围<select value={scope} disabled={propose.isPending} onChange={e => setScope(e.target.value)}><option value="">未限定栏目</option>{overview.data?.creators.flatMap(c => c.series.map(s => <option key={s.id} value={s.id} disabled={!c.is_active || !s.is_active}>{c.display_name} / {s.name}</option>))}</select></label>
        <label>运营要求<textarea autoFocus rows={5} maxLength={5000} value={draft} disabled={propose.isPending} onChange={e => setDraft(e.target.value)} onKeyDown={e => enter(e, submit)} placeholder="在队尾加 MCP 和 Tool Calling，再把 MCP 放第一条，其余保持顺序。" /></label>
        {propose.isError && <p className="form-error" role="alert">{propose.error.message} 若响应丢失，请先查看已保存计划，避免重复提交。</p>}
        <div className="drawer-foot"><span className="drawer-hint">Enter 提交 · Shift+Enter 换行</span><button className="button button-primary" disabled={!draft.trim() || propose.isPending}>{propose.isPending ? "正在整理选题计划…" : "生成预览"}</button></div>
      </form> : query.isError ? <div role="alert"><p>{query.error.message}</p><button onClick={() => void query.refetch()}>重试读取</button></div> : !operation ? <p>正在恢复计划…</p> : <>
        <div className="operation-meta"><b>{labels[operation.status]}</b><span>{operation.scope_series_id ? finalQueues.find(c => c.series_id === operation.scope_series_id)?.series_name ?? "固定栏目范围" : "未限定栏目"}</span></div>
        <p className="operation-request">{operation.request_text}</p>
        {operation.message && <p className="drawer-message">{operation.message}</p>}
        {updated && <p className="form-error">计划已在其他页面更新，旧预览不能确认。</p>}
        {hasError && <p className="form-error" role="alert">{change.error.message}</p>}
        {(updated || hasError) && <button className="button button-secondary" onClick={async () => { const result = await query.refetch(); if (result.data) { setReviewed(result.data); change.reset(); } }}>重新查看最新计划</button>}
        <div className="operation-changes">{changes.map((c, i) => <article key={i}><div className="change-head"><b>{c.series_name}</b><span>{c.creator_name} · {c.action === "add_topics" ? "新增" : "调序"}</span></div>
          {c.action === "add_topics" ? <Topics topics={c.after_topics.filter(t => !c.before_order.includes(t.topic_id))} /> : <><p className="queue-label">调整前</p><Topics topics={c.before_topics} /><p className="queue-label">调整后</p><Topics topics={c.after_topics} /></>}
        </article>)}</div>
        {finalQueues.map(c => <section className="final-queue" key={c.series_id}><h3>{c.series_name} · 最终队列</h3><Topics topics={c.after_topics} />{operation.status === "succeeded" && <Link to={"/series/" + c.series_id}>查看栏目 →</Link>}</section>)}
        {editable && <>
          <label>{operation.status === "needs_clarification" ? "补充信息" : "修改要求"}<textarea rows={3} maxLength={5000} value={text} onChange={e => setEditText(old => ({ ...old, [id!]: e.target.value }))} onKeyDown={e => enter(e, edit)} placeholder="把 Tool Calling 放第二条，其他顺序不变。" /></label>
          <div className="drawer-foot"><button className="button button-secondary" disabled={disabled || !text.trim()} onClick={edit}>{change.isPending ? "正在处理…" : "更新预览"}</button>
            {operation.status === "awaiting_approval" && <button className="button button-primary" disabled={disabled || !!text.trim()} onClick={() => change.mutate({ kind: "confirm", snapshot: operation })}>确认写入队列</button>}
          </div>
          {text.trim() && <p className="drawer-hint">先提交修改或清空输入，再确认当前预览。</p>}
          <button className="drawer-cancel" disabled={disabled} onClick={() => change.mutate({ kind: "cancel", snapshot: operation })}>取消这份计划</button>
        </>}
        <details className="operation-technical"><summary>技术详情</summary><p>{operation.id} · revision {operation.revision} · version {operation.version}</p><p>scope: {operation.scope_series_id ?? "global"}</p><pre>{JSON.stringify(operation.usage, null, 2)}</pre></details>
      </>}
      <div className="drawer-foot"><button className="button button-quiet" onClick={list}>查看已保存计划</button><button className="button button-quiet" onClick={close}>关闭，稍后处理</button></div>
    </dialog>
  </>;
}
