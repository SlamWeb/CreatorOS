import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { Plus, X } from "lucide-react";
import { ApiError, request, studioApi } from "../api/client";
import type { ProducerSkillItem, ProductionTaskInput, SeriesView, SkillLibraryView } from "../api/types";
import { StatusPill } from "./StatusPill";
import "./production-tasks.css";

type TaskDraft = { id: string; title: string; brief: string; mode: "default" | "single" | "pair" | "multiple"; single: string; mind: string; visual: string; primary?: string; supporting?: string[]; selected?: string[]; digests?: Record<string, string> };
type Submission = { request_id: string; tasks: ProductionTaskInput[] };
type DraftState = { rows: TaskDraft[]; requestId: string; submitted?: Submission };
const row = (): TaskDraft => ({ id: crypto.randomUUID(), title: "", brief: "", mode: "multiple", single: "", mind: "", visual: "", selected: [], digests: {} });
const empty = (): DraftState => ({ rows: [row(), row()], requestId: crypto.randomUUID() });
const skillName = (skill: ProducerSkillItem) => `${skill.id === "knowledge-to-carousel" ? "知识点轮播（内置）" : skill.display_name || skill.name}${skill.version_number ? ` · v${skill.version_number}` : ""}${skill.version_status === "candidate" ? " · 候选" : ""}`;
const ambiguous = (error: Error | null) => !!error && (!(error instanceof ApiError) || error.status === 0 || error.status === 408 || error.status >= 500);
function loadDraft(key: string): DraftState {
  try {
    const saved = JSON.parse(sessionStorage.getItem(key) ?? "null") as DraftState | null;
    if (saved && typeof saved.requestId === "string" && Array.isArray(saved.rows) && saved.rows.length
      && saved.rows.length <= 100 && saved.rows.every(item => typeof item.id === "string" && typeof item.title === "string"
        && typeof item.brief === "string" && ["default", "single", "pair", "multiple"].includes(item.mode))) return saved;
  } catch { /* A missing or invalid draft must not prevent reading saved tasks. */ }
  return empty();
}

export function ProductionTasksPanel({ topicId, series, startExpanded = false, disabled = false }: {
  topicId: string; series: SeriesView; skills: ProducerSkillItem[]; startExpanded?: boolean; disabled?: boolean;
}) {
  const client = useQueryClient();
  const location = useLocation();
  const [, setParams] = useSearchParams();
  const storageKey = `creatoros.production-tasks.v1:${topicId}`;
  const [draft, setDraft] = useState(() => loadDraft(storageKey));
  const [expanded, setExpanded] = useState(startExpanded || !!draft.submitted);
  const [localError, setLocalError] = useState("");
  const [storageFailed, setStorageFailed] = useState(false);
  const submitting = useRef(false);
  const seriesLibrary = useQuery({ queryKey: ["series-skills", series.id], queryFn: () => request<SkillLibraryView>(`/api/series/${encodeURIComponent(series.id)}/skills`), enabled: expanded && !!series.creator_id, retry: false });
  const choices = seriesLibrary.data?.available_versions ?? seriesLibrary.data?.items ?? [];
  useEffect(() => { if (startExpanded) setExpanded(true); }, [startExpanded]);
  useEffect(() => {
    try { sessionStorage.setItem(storageKey, JSON.stringify(draft)); setStorageFailed(false); }
    catch { setStorageFailed(true); }
  }, [storageKey, draft]);
  const queryKey = ["production-tasks", topicId];
  const tasks = useQuery({ queryKey, queryFn: () => studioApi.productionTasks(topicId), retry: false,
    refetchInterval: query => query.state.data?.items.some(task => ["waiting", "queued", "producing", "validating"].includes(task.status)) ? 3000 : false });
  const refresh = () => Promise.all([queryKey, ["topics"], ["series-all"], ["series", series.id], ["overview"],
    ["runs"], ["creator-tasks"], ["media-collections", series.creator_id], ["media-sources", series.creator_id]]
    .map(key => client.invalidateQueries({ queryKey: key })));
  const create = useMutation({ retry: false,
    mutationFn: (input: Submission) => studioApi.createProductionTasks(topicId, input),
    onSuccess: result => {
      client.setQueryData(queryKey, result); setDraft(empty()); setExpanded(false); setLocalError("");
      setParams(previous => {
        if (previous.get("topic") !== topicId || previous.get("series") && previous.get("series") !== series.id) return previous;
        const next = new URLSearchParams(previous); next.delete("production"); return next;
      }, { replace: true });
    },
    onError: error => { if (!ambiguous(error)) setDraft(current => ({ ...current, submitted: undefined })); },
    onSettled: async () => { try { await refresh(); } finally { submitting.current = false; } },
  });
  const waitingAction = useMutation({ retry: false,
    mutationFn: ({ id, action }: { id: string; action: "resume" | "cancel" }) => action === "resume"
      ? studioApi.resumeProductionTask(topicId, id) : studioApi.cancelProductionTask(topicId, id),
    onSettled: refresh,
  });
  const locked = disabled || create.isPending || !!draft.submitted;
  const defaultIds = seriesLibrary.data?.defaults.image.map(item => item.skill_id) ?? [];
  function selection(item: TaskDraft): string[] {
    if (item.selected) return item.selected;
    if (item.mode === "multiple") return [item.primary, ...(item.supporting ?? [])].filter((id): id is string => !!id);
    if (item.mode === "pair") return [item.mind, item.visual].filter(Boolean);
    if (item.mode === "single") return item.single ? [item.single] : [];
    return defaultIds;
  }
  function patch(id: string, change: Partial<TaskDraft>) {
    setLocalError(""); create.reset();
    setDraft(current => ({ rows: current.rows.map(item => item.id === id ? { ...item, ...change } : item), requestId: crypto.randomUUID() }));
  }
  function submit() {
    if (submitting.current || disabled) return;
    try {
      const input = draft.submitted ?? { request_id: draft.requestId, tasks: draft.rows.map(item => {
        if (!item.title.trim()) throw new Error("请为每个制作任务填写名称。");
        if (!item.brief.trim()) throw new Error("请为每个制作任务填写制作要求。");
        const selected = selection(item);
        if (!selected.length) throw new Error("请为每个制作任务选择 Skill。");
        if (selected.some(id => !choices.some(skill => skill.id === id))) throw new Error("所选 Skill 暂不可用，请重新选择。");
        return { title: item.title.trim(), brief: item.brief.trim(), kind: "image" as const, skill_ids: selected };

      }) };
      // Persist the exact identity before a write; a lost reply can only be checked with this same request.
      try { sessionStorage.setItem(storageKey, JSON.stringify({ ...draft, submitted: input })); }
      catch { setStorageFailed(true); }
      setDraft(current => ({ ...current, submitted: input }));
      submitting.current = true; create.mutate(input);
    } catch (error) { setLocalError(error instanceof Error ? error.message : "请检查任务内容。"); }
  }
  const collectionId = tasks.data?.collection_id;
  const collectionUrl = collectionId && series.creator_id
    ? `/?creator=${encodeURIComponent(series.creator_id)}&view=materials&collection=${encodeURIComponent(collectionId)}` : null;
  const runUrl = (id: string) => `/runs/${encodeURIComponent(id)}?return=${encodeURIComponent(location.pathname + location.search)}`;
  return <section className="production-tasks" aria-label="制作任务">
    <div className="production-tasks-heading"><h3>制作任务{tasks.data?.items.length ? `（${tasks.data.items.length}）` : ""}</h3>
      {collectionUrl && <Link className="text-link" to={collectionUrl}>查看素材集</Link>}
      <button className="button button-secondary" type="button" aria-expanded={expanded} disabled={disabled || create.isPending}
        onClick={() => setExpanded(value => !value)}>{expanded ? "收起拆分" : "拆分制作"}</button>
    </div>
    {tasks.isPending && <p role="status" className="muted">正在读取制作任务…</p>}
    {tasks.isError && <p className="form-error" role="alert">{tasks.error.message} <button type="button" className="text-link" onClick={() => void tasks.refetch()}>重新读取</button></p>}
    {!!tasks.data?.items.length && <ol className="production-task-list">{[...tasks.data.items].sort((a, b) => a.position - b.position).map(task => <li key={task.id}>
      <div className="production-task-title"><strong>{task.title}</strong><span className="muted">{task.kind === "video" ? "视频" : "图片"}</span><StatusPill status={task.status} /></div>
      {task.brief && <p>{task.brief}</p>}
      {task.last_error && <p className="form-error" role="alert">{task.last_error}</p>}
      <div className="production-task-meta"><span>{task.skill_summary}</span>{task.run_id ? <Link to={runUrl(task.run_id)}>查看任务</Link> : <span>{task.status === "waiting" ? "等待选定图片任务完成" : "尚未开始执行"}</span>}</div>
      {!task.run_id && ["waiting", "needs_attention"].includes(task.status) && <div className="media-actions">
        {task.status === "needs_attention" && <button type="button" className="button button-secondary" disabled={waitingAction.isPending} onClick={() => waitingAction.mutate({ id: task.id, action: "resume" })}>重试准备材料</button>}
        <button type="button" className="button button-quiet" disabled={waitingAction.isPending} onClick={() => waitingAction.mutate({ id: task.id, action: "cancel" })}>取消等待</button></div>}
    </li>)}</ol>}
    {!tasks.isPending && !tasks.isError && !tasks.data?.items.length && !expanded && <p className="muted">把这个选题拆成几项，分别制作后一起选片。</p>}
    {expanded && <form className="production-task-form" onSubmit={event => { event.preventDefault(); submit(); }}>
      <p className="production-task-intro">每项独立制作，图片收进同一个素材集。</p>
      {seriesLibrary.isPending && <p role="status" className="muted">正在读取栏目 Skill…</p>}
      {seriesLibrary.isError && <p className="form-error" role="alert">栏目 Skill 读取失败。<button type="button" className="text-link" onClick={() => void seriesLibrary.refetch()}>重新读取</button></p>}
      <fieldset disabled={locked}><legend className="sr-only">待提交制作任务</legend>
        {draft.rows.map((item, index) => <div className="production-task-draft" key={item.id}>
          <div className="production-task-draft-heading"><h4>任务 {index + 1}</h4><button type="button" aria-label={`移除任务 ${index + 1}`} disabled={draft.rows.length === 1}
            onClick={() => { create.reset(); setDraft(current => ({ rows: current.rows.filter(value => value.id !== item.id), requestId: crypto.randomUUID() })); }}><X size={16} /></button></div>
          <label>名称<input aria-label={`任务 ${index + 1} 名称`} maxLength={240} required value={item.title} onChange={event => patch(item.id, { title: event.target.value })} /></label>
          <label>制作要求<textarea aria-label={`任务 ${index + 1} 制作要求`} rows={3} maxLength={10000} required value={item.brief} onChange={event => patch(item.id, { brief: event.target.value })} /></label>
          <fieldset className="task-supporting-skills" aria-label={`任务 ${index + 1} 选择 Skill`}><legend>选择 Skill</legend>
            {choices.map(skill => <label key={skill.id}><input type="checkbox" value={skill.id} checked={selection(item).includes(skill.id)} onChange={event => patch(item.id, { selected: event.target.checked ? [...selection(item), skill.id] : selection(item).filter(id => id !== skill.id) })} /><span>{skillName(skill)}</span></label>)}
            {!seriesLibrary.isPending && !choices.length && <p className="muted">栏目还没有 Skill，请先在栏目设置中添加。</p>}
          </fieldset>
        </div>)}
        <button className="button button-quiet" type="button" disabled={draft.rows.length >= 100} onClick={() => {
          create.reset(); setDraft(current => ({ rows: [...current.rows, row()], requestId: crypto.randomUUID() }));
        }}><Plus size={16} aria-hidden="true" />添加任务</button>
      </fieldset>
      {storageFailed && <p className="form-error" role="alert">浏览器无法暂存草稿，请保留此页直到提交结果明确。</p>}
      {(localError || create.isError) && <p className="form-error" role="alert">{localError || create.error?.message}{ambiguous(create.error) ? " 提交结果待核对。内容已保留，不会自动重提；下方按钮只核对同一批任务。" : ""}</p>}
      {!!draft.submitted && !create.isPending && !create.isError && <p role="status" className="muted">有一批提交等待核对，请核对同一提交后继续。</p>}
      <div className="production-task-submit"><button className="button button-primary" disabled={disabled || create.isPending || tasks.isPending || tasks.isError
        || (!draft.submitted && (seriesLibrary.isPending || seriesLibrary.isError)) || !series.is_active || !series.creator_id}>
        {create.isPending ? "正在提交…" : draft.submitted ? "核对同一提交" : `提交并开始制作（${draft.rows.length}）`}</button>
        <small>{!series.creator_id ? "请先为栏目分配账号。" : !series.is_active ? "栏目当前不可制作。" : "草稿暂存在当前标签页；提交后可离开。"}</small></div>
    </form>}
    {create.isSuccess && <p role="status" className="production-task-success">已提交制作，可在这里查看进度。</p>}
    {waitingAction.isError && <p role="alert" className="form-error">{waitingAction.error.message} 请重新读取任务状态后检查。</p>}
  </section>;
}
