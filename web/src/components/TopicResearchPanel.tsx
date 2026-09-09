import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { request } from "../api/client";
import "./topic-research.css";

type Candidate = { id: string; title: string; angle: string; rationale: string;
  sources: { title: string; url: string }[]; queued: boolean };
type Batch = { id: string; series_id: string; status: string; stale: boolean; note: string;
  created_at: string; candidates: Candidate[]; attempt: number };
type Selection = { candidate_id: string; title: string; angle: string };
const labels: Record<string, string> = { researching: "调研中", ready: "待选择", failed: "失败", interrupted: "已中断", stale: "需要重新调研" };

export function TopicResearchPanel({ seriesId }: { seriesId: string }) {
  const [params] = useSearchParams();
  return <ResearchPanel key={params.get("research") ?? "new"} seriesId={seriesId} />;
}

function ResearchPanel({ seriesId }: { seriesId: string }) {
  const [params, setParams] = useSearchParams();
  const batchId = params.get("research");
  const [count, setCount] = useState(10);
  const [instructions, setInstructions] = useState("");
  const [selected, setSelected] = useState<Selection[]>([]);
  const history = useQuery({ queryKey: ["research-history", seriesId],
    queryFn: () => request<{ items: Batch[] }>(`/api/series/${encodeURIComponent(seriesId)}/topic-research`), refetchInterval: 5000 });
  const batch = useQuery({ queryKey: ["research", batchId], enabled: !!batchId,
    queryFn: () => request<Batch>(`/api/topic-research/${encodeURIComponent(batchId!)}`),
    refetchInterval: 3000 });
  const chooseBatch = (id: string) => { setSelected([]); setParams(p => { if (id) p.set("research", id); else p.delete("research"); return p; }); };
  const start = useMutation({ retry: false,
    mutationFn: () => request<Batch>(`/api/series/${encodeURIComponent(seriesId)}/topic-research`, {
      method: "POST", body: JSON.stringify({ count, instructions }) }),
    onSuccess: data => { chooseBatch(data.id); void history.refetch(); } });
  const preview = useMutation({ retry: false,
    mutationFn: () => request<{ id: string }>(`/api/topic-research/${batchId}/preview`, {
      method: "POST", body: JSON.stringify({ selections: selected }) }),
    onSuccess: data => setParams(p => { p.set("operation", data.id); return p; }) });
  const toggle = (c: Candidate) => setSelected(items => items.some(s => s.candidate_id === c.id)
    ? items.filter(s => s.candidate_id !== c.id) : [...items, { candidate_id: c.id, title: c.title, angle: c.angle }]);
  const edit = (id: string, field: "title" | "angle", value: string) => setSelected(items => items.map(s => s.candidate_id === id ? { ...s, [field]: value } : s));
  const move = (index: number, delta: number) => setSelected(items => {
    const copy = [...items]; [copy[index], copy[index + delta]] = [copy[index + delta], copy[index]]; return copy;
  });
  const data = batch.data;
  const canSelect = data?.status === "ready" && !data.stale && data.series_id === seriesId;
  return <section className="content-section research-panel">
    <div className="section-title"><h2>发现下一组选题</h2><span className="count-badge">候选 ≠ 队列</span></div>
    <p className="page-subtitle">Codex 按栏目定位、受众和 Skill 联网调研。你选择、调整，再确认入队；不会自动生产。</p>
    <form className="research-form" onSubmit={e => { e.preventDefault(); start.mutate(); }}>
      <label>候选数量<input type="number" min={1} max={30} required value={count} onChange={e => setCount(e.target.valueAsNumber)} /></label>
      <label>本次偏好<input maxLength={3000} placeholder="例如：更适合初学者，暂不讨论模型训练" value={instructions} onChange={e => setInstructions(e.target.value)} /></label>
      <button className="button button-primary" disabled={start.isPending || data?.status === "researching"}>开始联网调研</button>
    </form>
    {start.error && <p className="form-error">{start.error.message} 结果未知时先查看下面的历史批次，勿连续重发。</p>}
    <label className="research-history">调研记录<select value={batchId ?? ""} onChange={e => chooseBatch(e.target.value)}>
      <option value="">选择历史批次</option>{history.data?.items.map(b => <option key={b.id} value={b.id}>{new Date(b.created_at).toLocaleString()} · {labels[b.status] ?? b.status}</option>)}
    </select></label>
    {(batch.error || history.error) && <p className="form-error">{(batch.error || history.error)?.message}</p>}
    {data && <p role="status">{labels[data.status] ?? data.status} · {data.note || `已找到 ${data.candidates.length} 个候选`}{data.status === "researching" && " 可离开页面，回来继续查看。"}</p>}
    {data?.stale && <p className="form-error">栏目配置已变化。请按最新配置重新调研，旧候选不会自动入队。</p>}
    {!!data?.candidates.length && <>
      <div className="research-actions"><button className="button button-secondary" disabled={!canSelect} onClick={() => setSelected(data.candidates.filter(c => !c.queued).map(c => ({ candidate_id: c.id, title: c.title, angle: c.angle })))}>选择全部未入队</button><button className="button button-secondary" onClick={() => setSelected([])}>清空选择</button></div>
      <div className="research-candidates">{data.candidates.map(c => {
        const choice = selected.find(s => s.candidate_id === c.id);
        return <article className={`research-card${choice ? " chosen" : ""}`} key={c.id}>
          <label className="research-check"><input type="checkbox" checked={!!choice} disabled={!canSelect || c.queued} onChange={() => toggle(c)} /><strong>{c.title}</strong>{c.queued && <small>已入队</small>}</label>
          {choice ? <><label>标题<input aria-label={`${c.id} 标题`} maxLength={240} value={choice.title} onChange={e => edit(c.id, "title", e.target.value)} /></label><label>切入点<textarea aria-label={`${c.id} 切入点`} rows={3} maxLength={3000} value={choice.angle} onChange={e => edit(c.id, "angle", e.target.value)} /></label></> : <p>{c.angle}</p>}
          <details><summary>为什么适合 · 查看来源</summary><p>{c.rationale}</p><ul>{c.sources.map((s, i) => <li key={i}><a href={s.url} target="_blank" rel="noreferrer">{s.title} ↗</a></li>)}</ul></details>
        </article>;
      })}</div>
      {!!selected.length && <div className="research-order"><h3>入队顺序 · {selected.length} 项</h3>{selected.map((s, i) => <div key={s.candidate_id}><span>{i + 1}. {s.title}</span><button aria-label={`${s.candidate_id} 上移`} disabled={!i} onClick={() => move(i, -1)}>↑</button><button aria-label={`${s.candidate_id} 下移`} disabled={i === selected.length - 1} onClick={() => move(i, 1)}>↓</button></div>)}</div>}
      {preview.error && <p className="form-error">{preview.error.message}</p>}
      <button className="button button-primary" disabled={!canSelect || !selected.length || preview.isPending || selected.some(s => !s.title.trim() || !s.angle.trim() || data.candidates.find(c => c.id === s.candidate_id)?.queued)} onClick={() => preview.mutate()}>{preview.isPending ? "准备计划…" : `预览 ${selected.length} 个选题的入队计划`}</button>
    </>}
  </section>;
}
