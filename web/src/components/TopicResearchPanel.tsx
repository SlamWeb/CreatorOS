import { useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { request } from "../api/client";
import { CreativeMark } from "./CreativeMark";
import "./topic-research.css";

type Candidate = { id: string; title: string; angle: string; rationale: string;
  sources: { title: string; url: string }[]; queued: boolean };
type Batch = { id: string; series_id: string; status: string; stale: boolean; note: string;
  created_at: string; candidates: Candidate[]; attempt: number };
type Selection = { candidate_id: string; title: string; angle: string };
const labels: Record<string, string> = { researching: "调研中", ready: "待选择", failed: "失败", interrupted: "已中断", stale: "需要重新调研" };

export function TopicResearchPanel({ seriesId }: { seriesId: string }) {
  const [params] = useSearchParams();
  const history = useQuery({ queryKey: ["research-history", seriesId],
    queryFn: () => request<{ items: Batch[] }>(`/api/series/${encodeURIComponent(seriesId)}/topic-research`), refetchInterval: 5000 });
  const batchId = params.get("research") ?? history.data?.items[0]?.id ?? null;
  if (history.isPending && !batchId) return <section className="research-panel"><p role="status">正在读取调研记录…</p></section>;
  return <ResearchPanel key={`${seriesId}-${batchId ?? "new"}`} seriesId={seriesId} batchId={batchId} history={history} />;
}

function ResearchPanel({ seriesId, batchId, history }: { seriesId: string; batchId: string | null;
  history: ReturnType<typeof useQuery<{ items: Batch[] }>> }) {
  const [, setParams] = useSearchParams();
  const [count, setCount] = useState(10);
  const [instructions, setInstructions] = useState("");
  const [researchOpen, setResearchOpen] = useState(!batchId);
  const [selected, setSelected] = useState<Selection[]>([]);
  const [edits, setEdits] = useState<Record<string, Selection>>({});
  const [editing, setEditing] = useState<Selection | null>(null);
  const editButtons = useRef<Record<string, HTMLButtonElement | null>>({});
  const batch = useQuery({ queryKey: ["research", batchId], enabled: !!batchId,
    queryFn: () => request<Batch>(`/api/topic-research/${encodeURIComponent(batchId!)}`), refetchInterval: 3000 });
  const chooseBatch = (id: string) => setParams(p => { p.set("research", id); return p; });
  const start = useMutation({ retry: false,
    mutationFn: () => request<Batch>(`/api/series/${encodeURIComponent(seriesId)}/topic-research`, {
      method: "POST", body: JSON.stringify({ count, instructions }) }),
    onSuccess: data => { chooseBatch(data.id); void history.refetch(); } });
  const preview = useMutation({ retry: false,
    mutationFn: () => request<{ id: string }>(`/api/topic-research/${batchId}/preview`, {
      method: "POST", body: JSON.stringify({ selections: selected }) }),
    onSuccess: data => setParams(p => { p.set("operation", data.id); return p; }) });
  const effective = (c: Candidate): Selection => edits[c.id] ?? { candidate_id: c.id, title: c.title, angle: c.angle };
  const toggle = (c: Candidate) => setSelected(items => items.some(s => s.candidate_id === c.id)
    ? items.filter(s => s.candidate_id !== c.id) : [...items, effective(c)]);
  const closeEdit = () => { if (editing) editButtons.current[editing.candidate_id]?.focus(); setEditing(null); };
  const saveEdit = () => {
    if (!editing?.title.trim() || !editing.angle.trim()) return;
    const saved = { ...editing, title: editing.title.trim(), angle: editing.angle.trim() };
    setEdits(values => ({ ...values, [saved.candidate_id]: saved }));
    setSelected(items => items.map(s => s.candidate_id === saved.candidate_id ? saved : s));
    closeEdit();
  };
  const move = (index: number, delta: number) => setSelected(items => {
    const copy = [...items]; [copy[index], copy[index + delta]] = [copy[index + delta], copy[index]]; return copy;
  });
  const data = batch.data;
  const canSelect = data?.status === "ready" && !data.stale && data.series_id === seriesId;
  return <section className="research-panel" aria-label="选题候选">
    <div className="research-heading"><div><h2>选题候选</h2><p>找到值得讲的内容，再决定制作什么。</p></div>
      <button className="button button-secondary" aria-expanded={researchOpen} onClick={() => setResearchOpen(!researchOpen)}>{batchId ? "重新调研" : "调研选题"} ↻</button></div>
    {researchOpen && <form className="research-form" onSubmit={e => { e.preventDefault(); start.mutate(); }}>
      <label>候选数量<input type="number" min={1} max={30} required value={count} onChange={e => setCount(e.target.valueAsNumber)} /></label>
      <label>本次偏好<input maxLength={3000} placeholder="例如：面向初学者，暂不讨论模型训练" value={instructions} onChange={e => setInstructions(e.target.value)} /></label>
      <button className="button button-primary" disabled={start.isPending || data?.status === "researching"}>开始联网调研</button>
      <small>由 Codex 联网调研，会消耗额度；不会直接入队或生产。</small>
    </form>}
    {start.error && <p className="form-error" role="alert">{start.error.message} 结果未知时先查历史批次，勿连续重发。</p>}
    {history.data?.items.length ? <div className="research-toolbar">
      <label>调研记录<select value={batchId ?? ""} disabled={!!editing || preview.isPending} onChange={e => chooseBatch(e.target.value)}>
        {history.data.items.map(b => <option key={b.id} value={b.id}>{new Date(b.created_at).toLocaleDateString()} · {labels[b.status] ?? b.status}</option>)}
      </select></label><span>{data ? `${data.candidates.length} 个候选` : "读取中…"}</span>
    </div> : null}
    {(batch.error || history.error) && <p className="form-error" role="alert">{(batch.error || history.error)?.message}<button onClick={() => { void batch.refetch(); void history.refetch(); }}>重新读取</button></p>}
    {batchId && batch.isPending && <p role="status">正在读取候选…</p>}
    {data?.status === "researching" && <p className="research-notice" role="status">正在联网调研 · 第 {data.attempt || 1} 次尝试。可以离开，回来继续查看。</p>}
    {data && data.status !== "ready" && data.status !== "researching" && <p className="form-error" role="status">{labels[data.status]} · {data.note}</p>}
    {data?.stale && <p className="form-error" role="alert">栏目配置已变化。请按最新配置重新调研，旧候选不会自动入队。</p>}
    {data?.status === "ready" && !data.candidates.length && <p className="research-empty">{data.note || "本次未找到适合的候选，可调整偏好重新调研。"}</p>}
    {!batchId && !history.isError && <div className="research-empty"><CreativeMark /><h3>给下一篇内容找个好起点</h3><p>填写本次偏好，让 Codex 根据栏目定位和 Skill 提供候选。你决定哪些入队。</p></div>}
    {!!data?.candidates.length && <>
      <div className="research-actions"><button disabled={!canSelect || !!editing} onClick={() => setSelected(data.candidates.filter(c => !c.queued).map(effective))}>选择全部未入队</button><button disabled={!!editing || !selected.length} onClick={() => setSelected([])}>清空选择</button></div>
      <div className="research-candidates">{data.candidates.map((c, index) => {
        const choice = selected.some(s => s.candidate_id === c.id);
        const value = effective(c);
        return <article className={`research-card${choice ? " chosen" : ""}${c.queued ? " queued" : ""}`} key={c.id} data-testid={`candidate-${c.id}`}>
          <div className="candidate-main">
            <input aria-label={`选择候选 ${c.id}`} type="checkbox" checked={choice} disabled={!canSelect || c.queued || !!editing} onChange={() => toggle(c)} />
            <div className="candidate-copy"><h3>{value.title}</h3><p>切入点：{value.angle}</p>
              <div className="candidate-links"><details><summary>查看来源 ↗</summary><p>{c.rationale}</p><ul>{c.sources.map((s, i) => <li key={i}><a href={s.url} target="_blank" rel="noreferrer">{s.title} ↗</a></li>)}</ul></details>
              <button ref={el => { editButtons.current[c.id] = el; }} disabled={!canSelect || c.queued || !!editing} onClick={() => setEditing(value)} aria-label={`编辑候选 ${c.id}`}>编辑</button>{edits[c.id] && <small>已调整</small>}{c.queued && <small>已入队</small>}</div>
            </div><div className="candidate-art"><CreativeMark variant={index % 3} /><span>{String(index + 1).padStart(2, "0")}</span></div>
          </div>
          {editing?.candidate_id === c.id && <form className="candidate-editor" onSubmit={e => { e.preventDefault(); saveEdit(); }} onKeyDown={e => { if (e.key === "Escape" && !e.nativeEvent.isComposing) { e.stopPropagation(); closeEdit(); } }}>
            <label>标题<input autoFocus required maxLength={240} value={editing.title} onChange={e => setEditing({ ...editing, title: e.target.value })} /></label>
            <label>切入点<textarea required rows={3} maxLength={3000} value={editing.angle} onChange={e => setEditing({ ...editing, angle: e.target.value })} /></label>
            <div><button className="button button-primary" disabled={!editing.title.trim() || !editing.angle.trim()}>保存修改</button><button className="button button-secondary" type="button" onClick={closeEdit}>取消编辑</button></div>
            <small>仅保存到本页草稿；确认入队后才持久化。刷新或切换批次会清空草稿。</small>
          </form>}
        </article>;
      })}</div>
      {selected.length > 1 && <details className="research-order"><summary>调整入队顺序 · {selected.length} 项</summary>{selected.map((s, i) => <div key={s.candidate_id}><span>{i + 1}. {s.title}</span><button aria-label={`${s.candidate_id} 上移`} disabled={!i || !!editing} onClick={() => move(i, -1)}>↑</button><button aria-label={`${s.candidate_id} 下移`} disabled={i === selected.length - 1 || !!editing} onClick={() => move(i, 1)}>↓</button></div>)}</details>}
      {preview.error && <p className="form-error" role="alert">{preview.error.message}</p>}
      <div className="research-selection-bar"><span>已选 <b>{selected.length}</b> 项<small>预览后由你确认入队</small></span>
      <button className="button button-primary" disabled={!canSelect || !selected.length || !!editing || preview.isPending || selected.some(s => data.candidates.find(c => c.id === s.candidate_id)?.queued)} onClick={() => preview.mutate()}>{preview.isPending ? "准备计划…" : "预览入队 →"}</button></div>
      {!!selected.length && <p className="research-draft-note">选择与修改尚未入队；刷新或切换批次会清空本页草稿。</p>}
    </>}
  </section>;
}
