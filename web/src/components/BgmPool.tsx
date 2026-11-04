import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiUrl, request } from "../api/client";
import { imageBase64 } from "./AgentAttachments";
import "./bgm-pool.css";

export const QA_BGM_CREATOR = "creator-f2304a25ec244d8484d8";
type Material = { id: string; name: string; kind: "audio" | "video"; duration_seconds: number; url: string; analysis_available: boolean; analysis_status?: string };
type ImportJob = { id: string; status: "running" | "completed" | "failed" | "interrupted"; source_url: string; material_id?: string; message?: string };
const label: Record<string, string> = { not_started: "未分析", running: "分析中", completed: "已分析", failed: "分析失败", interrupted: "分析中断" };
const base = (creatorId: string) => `/api/creators/${encodeURIComponent(creatorId)}/materials`;

export async function uploadBgm(creatorId: string, file: File): Promise<Material> {
  if (!file.size || file.size > 64 * 1024 * 1024) throw new Error("请选择不超过 64 MiB 的音频文件。");
  return request<Material>(base(creatorId), { method: "POST", body: JSON.stringify({ name: file.name, data_base64: await imageBase64(file), analyze: true }) });
}

export function BgmPool({ creatorId }: { creatorId: string }) {
  const queryClient = useQueryClient();
  const [link, setLink] = useState("");
  const [importId, setImportId] = useState(() => { try { return sessionStorage.getItem(`creatoros.bgm-import:${creatorId}`) ?? ""; } catch { return ""; } });
  const [message, setMessage] = useState("");
  const root = base(creatorId);
  const materials = useQuery({ queryKey: ["video-materials", creatorId], queryFn: () => request<{ items: Material[] }>(root), retry: false,
    refetchInterval: q => q.state.data?.items.some(item => item.analysis_status === "running") ? 5000 : false });
  const imported = useQuery({ queryKey: ["bgm-import", creatorId, importId], queryFn: () => request<ImportJob>(`${root}/imports/${encodeURIComponent(importId)}`),
    enabled: !!importId, retry: false, refetchInterval: q => q.state.data?.status === "running" ? 5000 : false });
  const importHistory = useQuery({ queryKey: ["bgm-imports", creatorId], queryFn: () => request<{ items: ImportJob[] }>(`${root}/imports`), retry: false,
    refetchInterval: q => q.state.data?.items.some(item => item.status === "running") ? 5000 : false });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["video-materials", creatorId] });
  const upload = useMutation({ retry: false, mutationFn: (file: File) => uploadBgm(creatorId, file),
    onSuccess: item => { setMessage(`已添加「${item.name}」`); void refresh(); }, onError: () => { void refresh(); } });
  const fromLink = useMutation({ retry: false, mutationFn: () => request<ImportJob>(`${root}/imports`, { method: "POST", body: JSON.stringify({ source_url: link.trim() }) }),
    onSuccess: job => { setImportId(job.id); try { sessionStorage.setItem(`creatoros.bgm-import:${creatorId}`, job.id); } catch { /* State remains usable. */ } setMessage(""); void importHistory.refetch(); if (job.status === "completed") void refresh(); } });
  const job = imported.data ?? fromLink.data;
  const statusText = job?.status === "running" ? "正在获取音频…" : job?.status === "completed" ? "音频已加入 BGM 池" : job?.status === "interrupted" ? "获取中断，可重新添加链接。" : job?.status === "failed" ? job.message || "未能获取音频。" : "";
  useEffect(() => { if (job?.status === "completed") void queryClient.invalidateQueries({ queryKey: ["video-materials", creatorId] }); }, [job?.status, job?.id, creatorId, queryClient]);
  return <section className="bgm-pool" aria-label="BGM 池">
    <header className="bgm-head"><div><h2>BGM 池</h2><p>存下常用音乐，后续可以直接使用。</p></div>
      <label className="button button-primary bgm-upload">{upload.isPending ? "正在上传…" : "上传音频"}<input type="file" accept="audio/*,video/*,.mp3,.wav,.m4a" disabled={upload.isPending} onChange={event => { const file = event.target.files?.[0]; if (file) { setMessage(""); upload.mutate(file); } event.target.value = ""; }} /></label>
    </header>
    <form className="bgm-link" onSubmit={event => { event.preventDefault(); if (link.trim()) fromLink.mutate(); }}>
      <label htmlFor="bgm-share">添加分享链接</label><div><input id="bgm-share" value={link} onChange={event => setLink(event.target.value)} placeholder="粘贴视频链接或分享文字" maxLength={10000} />
        <button className="button button-secondary" disabled={!link.trim() || fromLink.isPending || job?.status === "running"}>{fromLink.isPending ? "正在提交…" : "添加"}</button></div>
    </form>
    {message && <p role="status">{message}</p>}{statusText && <p role={job?.status === "failed" ? "alert" : "status"}>{statusText}</p>}
    {(upload.isError || fromLink.isError || imported.isError) && <p className="form-error" role="alert">{upload.error?.message ?? fromLink.error?.message ?? imported.error?.message}</p>}
    {!!importHistory.data?.items.filter(item => item.status !== "completed" && item.id !== importId).length && <details className="bgm-history"><summary>未完成的链接</summary>{importHistory.data.items.filter(item => item.status !== "completed" && item.id !== importId).map(item => <div key={item.id}><p>{item.source_url}</p><span>{item.status === "running" ? "正在获取音频…" : item.message || "获取中断"}</span>{item.status !== "running" && <button type="button" className="text-link" onClick={() => setLink(item.source_url)}>重新添加</button>}</div>)}</details>}
    {materials.isPending ? <p role="status">正在读取音乐…</p> : materials.isError ? <p role="alert" className="form-error">{materials.error.message} <button className="text-link" type="button" onClick={() => void materials.refetch()}>重新读取</button></p> : materials.data.items.length ?
      <div className="bgm-list">{materials.data.items.map(item => <BgmRow key={item.id} creatorId={creatorId} item={item} onRefresh={() => void refresh()} />)}</div> :
      <div className="bgm-empty"><h3>还没有音乐</h3><p>上传音频或粘贴分享链接，将常用音乐留在这里。</p></div>}
  </section>;
}

function BgmRow({ creatorId, item, onRefresh }: { creatorId: string; item: Material; onRefresh: () => void }) {
  const state = item.analysis_status ?? (item.analysis_available ? "completed" : "not_started");
  const detail = useQuery({ queryKey: ["bgm-analysis-status", creatorId, item.id, state], queryFn: () => request<{ message?: string }>(`${base(creatorId)}/${item.id}/analysis/status`), enabled: state === "failed" || state === "interrupted", retry: false });
  const analysis = useMutation({ retry: false, mutationFn: () => request<{ status: string }>(`${base(creatorId)}/${item.id}/analysis/run`, { method: "POST", body: JSON.stringify({ force: item.analysis_available }) }), onSuccess: onRefresh });
  return <article className="bgm-row"><div className="bgm-row-title"><strong>{item.name}</strong><span>{Math.floor(item.duration_seconds / 60)}:{String(Math.floor(item.duration_seconds % 60)).padStart(2, "0")}</span><span className={`bgm-status ${state}`}>{label[state] ?? state}</span></div>
    {item.kind === "video" ? <video controls preload="metadata" src={apiUrl(item.url)} /> : <audio controls preload="metadata" src={apiUrl(item.url)} />}
    {(state === "failed" || state === "interrupted" || state === "not_started") && <button type="button" className="button button-secondary" disabled={analysis.isPending} onClick={() => analysis.mutate()}>{analysis.isPending ? "正在提交…" : state === "not_started" ? "分析" : "重新分析"}</button>}
    {!!detail.data?.message && <p className="form-error" role="alert">{detail.data.message}</p>}
    {analysis.isError && <p className="form-error" role="alert">{analysis.error.message}</p>}
  </article>;
}
