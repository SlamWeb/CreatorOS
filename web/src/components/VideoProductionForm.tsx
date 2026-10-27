import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ApiError, apiUrl, request, studioApi } from "../api/client";
import type { ProductionTaskInput, SkillLibraryView } from "../api/types";
import { skillLabel } from "./SkillLibrary";

type Material = { id: string; name: string; kind: "audio" | "video"; duration_seconds: number; url: string };
type VideoDraft = { topicId: string; title: string; brief: string; skillIds: string[]; materialId: string; sourceUrl: string; mode: "now" | "wait"; dependencies: string[]; requestId: string; submitted?: { request_id: string; tasks: ProductionTaskInput[] } };
const ambiguous = (error: Error | null) => !!error && (!(error instanceof ApiError) || error.status === 0 || error.status === 408 || error.status >= 500);
const blank = (topicId: string): VideoDraft => ({ topicId, title: "", brief: "", skillIds: [], materialId: "", sourceUrl: "", mode: "now", dependencies: [], requestId: crypto.randomUUID() });
function restore(key: string, topicId: string): VideoDraft { try { const value = JSON.parse(sessionStorage.getItem(key) ?? "null"); if (value && typeof value.requestId === "string" && typeof value.title === "string" && Array.isArray(value.dependencies)) return { ...value, skillIds: Array.isArray(value.skillIds) ? value.skillIds : value.skillId ? [value.skillId] : [] }; } catch { /* Optional draft. */ } return blank(topicId); }
const fileBody = (file: File): Promise<string> => new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(",")[1]); reader.onerror = () => reject(new Error("文件读取失败，请重新选择。")); reader.readAsDataURL(file); });

export function VideoProductionForm({ creatorId, collectionId, topicId, topics, includedCount }: { creatorId: string; collectionId: string; topicId: string | null; topics: { id: string; title: string }[]; includedCount: number }) {
  const client = useQueryClient();
  const key = `creatoros.video-draft:${creatorId}:${collectionId}`;
  const [draft, setDraft] = useState(() => restore(key, topicId ?? ""));
  const [localError, setLocalError] = useState("");
  const [storageFailed, setStorageFailed] = useState(false);
  const [created, setCreated] = useState(false);
  const submitting = useRef(false);
  const endpoint = `/api/creators/${encodeURIComponent(creatorId)}/materials`;
  const materialKey = ["video-materials", creatorId];
  const materials = useQuery({ queryKey: materialKey, queryFn: () => request<{ items: Material[] }>(endpoint), retry: false });
  const tasks = useQuery({ queryKey: ["production-tasks", draft.topicId], queryFn: () => studioApi.productionTasks(draft.topicId), enabled: !!draft.topicId, retry: false });
  const seriesId = tasks.data?.series_id;
  const pool = useQuery({ queryKey: ["series-skills", seriesId], queryFn: () => request<SkillLibraryView>(`/api/series/${encodeURIComponent(seriesId!)}/skills`), enabled: !!seriesId, retry: false });
  const choices = pool.data?.available_versions ?? [];
  useEffect(() => { try { sessionStorage.setItem(key, JSON.stringify(draft)); setStorageFailed(false); } catch { setStorageFailed(true); } }, [key, draft]);
  const create = useMutation({ retry: false, mutationFn: (input: { request_id: string; tasks: ProductionTaskInput[] }) => studioApi.createProductionTasks(draft.topicId, input),
    onSuccess: async () => { setCreated(true); setDraft(blank(draft.topicId)); await Promise.all([client.invalidateQueries({ queryKey: ["production-tasks"] }), client.invalidateQueries({ queryKey: ["creator-tasks"] }), client.invalidateQueries({ queryKey: ["runs"] })]); },
    onError: error => { if (!ambiguous(error)) setDraft(value => ({ ...value, submitted: undefined })); },
    onSettled: () => { submitting.current = false; },
  });
  const upload = useMutation({ retry: false, mutationFn: async (input: File | string) => {
    if (typeof input !== "string" && input.size > 64 * 1024 * 1024) throw new Error("文件不能超过 64 MiB，请先裁短或压缩。");
    return request<Material>(endpoint, { method: "POST", body: JSON.stringify(typeof input === "string" ? { source_url: input } : { name: input.name, data_base64: await fileBody(input) }) });
  }, onSuccess: async result => { patch({ materialId: result.id }); await client.invalidateQueries({ queryKey: materialKey }); }, onError: () => client.invalidateQueries({ queryKey: materialKey }) });
  const locked = create.isPending || !!draft.submitted || upload.isPending;
  function patch(change: Partial<VideoDraft>) { create.reset(); setCreated(false); setLocalError(""); setDraft(value => ({ ...value, ...change, requestId: crypto.randomUUID(), submitted: undefined })); }
  function submit() {
    if (submitting.current) return;
    try {
      if (!draft.topicId || !draft.title.trim() || !draft.brief.trim() || !draft.materialId || !draft.skillIds.length) throw new Error("请填写任务、剪辑要求，并选择 Skill 和 BGM。");
      if (!draft.submitted && (!seriesId || pool.isError || pool.isPending || draft.skillIds.some(id => !choices.some(skill => skill.id === id)))) throw new Error("所选 Skill 不在当前栏目中，请重新选择。");
      if (draft.mode === "now" && !includedCount) throw new Error("素材集当前没有保留的图片，请先选片或改为等待图片任务。");
      if (draft.mode === "wait" && !draft.dependencies.length) throw new Error("请明确勾选要等待的图片任务。");
      const input = draft.submitted ?? { request_id: draft.requestId, tasks: [{ title: draft.title.trim(), brief: draft.brief.trim(), kind: "video" as const,
        skill_ids: draft.skillIds, inputs: { collection_id: collectionId, bgm_material_id: draft.materialId }, depends_on: draft.mode === "wait" ? draft.dependencies : [] }] };
      try { sessionStorage.setItem(key, JSON.stringify({ ...draft, submitted: input })); } catch { setStorageFailed(true); }
      setDraft(value => ({ ...value, submitted: input })); submitting.current = true; create.mutate(input);
    } catch (error) { setLocalError(error instanceof Error ? error.message : "请检查输入。"); }
  }
  const material = materials.data?.items.find(item => item.id === draft.materialId);
  const imageTasks = tasks.data?.items.filter(item => item.kind !== "video") ?? [];
  return <form className="video-production-form skill-library" aria-label="制作视频" onSubmit={event => { event.preventDefault(); submit(); }}><h3>制作视频</h3><p>把素材集中的选片、BGM 和剪辑要求交给一个新的制作任务。</p>
    <fieldset disabled={locked}>
      <label>所属选题<select aria-label="视频所属选题" required value={draft.topicId} onChange={event => patch({ topicId: event.target.value, dependencies: [], skillIds: [] })} disabled={!!topicId}><option value="">选择选题</option>{topicId && !topics.some(item => item.id === topicId) && <option value={topicId}>素材集关联选题</option>}{topics.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>
      <label>任务名称<input aria-label="视频任务名称" required maxLength={240} value={draft.title} onChange={event => patch({ title: event.target.value })} /></label>
      <fieldset disabled={!seriesId || pool.isPending || pool.isError}><legend>这一步使用的 Skill</legend><div className="skill-check-list">{choices.map(skill => <label key={skill.id}><input type="checkbox" checked={draft.skillIds.includes(skill.id)} onChange={event => patch({ skillIds: event.target.checked ? [...draft.skillIds.filter(id => { const selected = choices.find(item => item.id === id); return (selected?.family_id ?? selected?.id) !== (skill.family_id ?? skill.id); }), skill.id] : draft.skillIds.filter(id => id !== skill.id) })} /><span>{skillLabel(skill)}</span></label>)}</div></fieldset>
      {draft.topicId && !tasks.isPending && pool.isSuccess && !choices.length && <p>这个栏目还没有 Skill，请先到栏目设置添加。</p>}
      <label>剪辑要求<textarea aria-label="剪辑要求" required maxLength={10000} rows={4} value={draft.brief} onChange={event => patch({ brief: event.target.value })} placeholder="例如：竖屏、按音乐节拍切图，开场停留两秒，结尾淡出。" /></label>
      <h4>BGM</h4><label>已保存音频或视频<select aria-label="选择 BGM" required value={draft.materialId} disabled={materials.isPending || materials.isError} onChange={event => patch({ materialId: event.target.value })}><option value="">选择 BGM 材料</option>{materials.data?.items.map(item => <option key={item.id} value={item.id}>{item.name} · {Math.round(item.duration_seconds)} 秒</option>)}</select></label>
      <label>上传 MP3、音频或视频<input aria-label="上传 BGM 文件" type="file" accept="audio/*,video/*,.mp3" onChange={event => { const file = event.target.files?.[0]; if (file) upload.mutate(file); event.target.value = ""; }} /></label>
      <div className="video-bgm-url"><label>BGM 分享链接<input aria-label="BGM 分享链接" type="url" value={draft.sourceUrl} onChange={event => patch({ sourceUrl: event.target.value })} placeholder="https://…" /></label><button type="button" className="button button-secondary" disabled={!draft.sourceUrl.trim()} onClick={() => upload.mutate(draft.sourceUrl.trim())}>读取链接</button></div>
      <small>链接解析成功后才可使用；无法取得音频时请上传文件。单个文件最多 64 MiB。</small>
      {material && <div className="video-bgm-preview"><p>{material.name}</p>{material.kind === "video" ? <video controls preload="metadata" src={apiUrl(material.url)} /> : <audio controls preload="metadata" src={apiUrl(material.url)} />}</div>}
      <h4>何时开始</h4><label>开始方式<select aria-label="视频开始方式" value={draft.mode} onChange={event => patch({ mode: event.target.value as VideoDraft["mode"] })}><option value="now">立即使用当前选片（{includedCount} 张）</option><option value="wait">等待选定图片任务全部完成</option></select></label>
      {draft.mode === "wait" && <div className="skill-check-list video-dependencies">{imageTasks.map(item => <label key={item.id}><input type="checkbox" checked={draft.dependencies.includes(item.id)} onChange={event => patch({ dependencies: event.target.checked ? [...draft.dependencies, item.id] : draft.dependencies.filter(id => id !== item.id) })} /><span>{item.title} · {item.status}</span></label>)}{!imageTasks.length && <p>当前选题没有可等待的图片任务。先在选题中拆分制作。</p>}</div>}
    </fieldset>
    {upload.isPending && <p role="status">正在读取并冻结 BGM 材料…</p>}{upload.isError && <p role="alert" className="form-error">{upload.error.message} 请检查已保存材料或重新上传。</p>}
    {(materials.isError || pool.isError || tasks.isError) && <p role="alert" className="form-error">{materials.error?.message ?? pool.error?.message ?? tasks.error?.message} <button type="button" className="text-link" onClick={() => { void materials.refetch(); if (seriesId) void pool.refetch(); if (draft.topicId) void tasks.refetch(); }}>重新读取</button></p>}
    {storageFailed && <p role="alert" className="form-error">浏览器无法暂存草稿，请保留此页直到提交结果明确。</p>}
    {(localError || create.isError) && <p role="alert" className="form-error">{localError || create.error?.message}{ambiguous(create.error) ? " 提交结果待核对；内容已保留，不会自动重提。" : ""}</p>}
    {!!draft.submitted && !create.isPending && !create.isError && <p role="status">上次提交结果待核对，请核对同一提交。</p>}
    <div className="media-actions"><button className="button button-primary" disabled={create.isPending || upload.isPending || !draft.topicId || materials.isPending || tasks.isPending || tasks.isError || (!!seriesId && (pool.isPending || pool.isError))}>{create.isPending ? "正在提交…" : draft.submitted ? "核对同一视频提交" : draft.mode === "wait" ? "保存并等待图片完成" : "提交视频制作"}</button><small>草稿留在当前标签页；提交成功后可离开。</small></div>
    {created && <p role="status">视频任务已保存。<Link to={`/?${new URLSearchParams({ creator: creatorId, topic: draft.topicId, ...(create.data?.series_id ? { series: create.data.series_id } : {}) })}`}>查看这个选题的制作任务</Link></p>}
  </form>;
}
