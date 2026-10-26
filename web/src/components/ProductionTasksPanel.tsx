import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { Plus, X } from "lucide-react";
import { ApiError, studioApi } from "../api/client";
import type { ProducerSkillItem, ProductionTaskInput, SeriesView, SkillOverrides } from "../api/types";
import { StatusPill } from "./StatusPill";
import "./production-tasks.css";

type TaskDraft = { id: string; title: string; brief: string; mode: "default" | "single" | "pair"; single: string; mind: string; visual: string; digests?: Record<string, string> };
type Submission = { request_id: string; tasks: ProductionTaskInput[] };
type DraftState = { rows: TaskDraft[]; requestId: string; submitted?: Submission };
const row = (): TaskDraft => ({ id: crypto.randomUUID(), title: "", brief: "", mode: "default", single: "", mind: "", visual: "", digests: {} });
const empty = (): DraftState => ({ rows: [row(), row()], requestId: crypto.randomUUID() });
const skillName = (skill: ProducerSkillItem) => `${skill.id === "knowledge-to-carousel" ? "知识点轮播（内置）" : skill.display_name || skill.name}${skill.version_number ? ` · v${skill.version_number}` : ""}${skill.version_status === "candidate" ? " · 候选" : ""}`;
const ambiguous = (error: Error | null) => !!error && (!(error instanceof ApiError) || error.status === 0 || error.status === 408 || error.status >= 500);
// A candidate and a content Skill cannot be bound as a released standalone producer, but may be explicitly selected here.
const canChoose = (skill: ProducerSkillItem) => !!skill.digest && (skill.role === "mind" || skill.role === "production"
  || (skill.carousel_compatible && skill.role === "legacy_end_to_end"));

function loadDraft(key: string): DraftState {
  try {
    const saved = JSON.parse(sessionStorage.getItem(key) ?? "null") as DraftState | null;
    if (saved && typeof saved.requestId === "string" && Array.isArray(saved.rows) && saved.rows.length
      && saved.rows.length <= 100 && saved.rows.every(item => typeof item.id === "string" && typeof item.title === "string"
        && typeof item.brief === "string" && ["default", "single", "pair"].includes(item.mode))) return saved;
  } catch { /* A missing or invalid draft must not prevent reading saved tasks. */ }
  return empty();
}

function overrides(draft: TaskDraft, skills: ProducerSkillItem[]): SkillOverrides | undefined {
  if (draft.mode === "default") return undefined;
  const ids = draft.mode === "single" ? [draft.single] : [draft.mind, draft.visual];
  const chosen = ids.map(id => skills.find(skill => skill.id === id && canChoose(skill)));
  if (chosen.some(skill => !skill)) throw new Error("请选择可用的 Skill 版本。");
  const expected_digests = Object.fromEntries(chosen.map(skill => [skill!.id, draft.digests?.[skill!.id] ?? skill!.digest!]));
  return draft.mode === "single" ? { skill_id: draft.single, expected_digests }
    : { mind_skill_id: draft.mind, production_skill_id: draft.visual, expected_digests };
}

export function ProductionTasksPanel({ topicId, series, skills, startExpanded = false, disabled = false }: {
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
  const catalog = useQuery({ queryKey: ["producer-skills", "all-versions"], queryFn: studioApi.productionTaskSkills,
    enabled: expanded, retry: false });
  const choices = catalog.data?.items ?? [];
  useEffect(() => { if (startExpanded) setExpanded(true); }, [startExpanded]);
  useEffect(() => {
    try { sessionStorage.setItem(storageKey, JSON.stringify(draft)); setStorageFailed(false); }
    catch { setStorageFailed(true); }
  }, [storageKey, draft]);
  const queryKey = ["production-tasks", topicId];
  const tasks = useQuery({ queryKey, queryFn: () => studioApi.productionTasks(topicId), retry: false,
    refetchInterval: query => query.state.data?.items.some(task => ["queued", "producing", "validating"].includes(task.status)) ? 3000 : false });
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
  const locked = disabled || create.isPending || !!draft.submitted;
  const catalogRequired = !draft.submitted && draft.rows.some(item => item.mode !== "default");
  const available = choices.filter(canChoose);
  const singles = available.filter(skill => skill.role === "legacy_end_to_end");
  const minds = available.filter(skill => skill.role === "mind");
  const visuals = available.filter(skill => skill.role === "production");
  const defaultIds = series.skill_name ? [series.skill_name] : [series.mind_skill_id, series.production_skill_id].filter(Boolean);
  const defaults = defaultIds.map(id => [...choices, ...skills].find(skill => skill.id === id || skill.name === id)).filter((skill): skill is ProducerSkillItem => !!skill);
  function patch(id: string, change: Partial<TaskDraft>) {
    setLocalError(""); create.reset();
    const selectedId = change.single || change.mind || change.visual;
    const selectedSkill = choices.find(skill => skill.id === selectedId);
    setDraft(current => ({ rows: current.rows.map(item => item.id === id ? { ...item, ...change,
      ...(selectedSkill?.digest ? { digests: { ...item.digests, [selectedSkill.id]: selectedSkill.digest } } : {}) } : item), requestId: crypto.randomUUID() }));
  }
  function submit() {
    if (submitting.current || disabled) return;
    try {
      const input = draft.submitted ?? { request_id: draft.requestId, tasks: draft.rows.map(item => {
        if (!item.title.trim()) throw new Error("请为每个制作任务填写名称。");
        if (!item.brief.trim()) throw new Error("请为每个制作任务填写制作要求。");
        const skill_overrides = overrides(item, choices);
        return { title: item.title.trim(), brief: item.brief.trim(), ...(skill_overrides ? { skill_overrides } : {}) };
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
      <div className="production-task-title"><strong>{task.title}</strong><StatusPill status={task.status} /></div>
      {task.brief && <p>{task.brief}</p>}
      <div className="production-task-meta"><span>{task.skill_summary}</span><Link to={runUrl(task.run_id)}>查看任务</Link></div>
    </li>)}</ol>}
    {!tasks.isPending && !tasks.isError && !tasks.data?.items.length && !expanded && <p className="muted">把这个选题拆成几项，分别制作后一起选片。</p>}
    {expanded && <form className="production-task-form" onSubmit={event => { event.preventDefault(); submit(); }}>
      <p className="production-task-intro">每项独立制作，图片收进同一个素材集。</p>
      {catalog.isPending && <p role="status" className="muted">正在读取 Skill 版本…</p>}
      {catalog.isError && <p className="form-error" role="alert">Skill 版本读取失败，可继续沿用栏目默认。<button type="button" className="text-link" onClick={() => void catalog.refetch()}>重新读取</button></p>}
      <fieldset disabled={locked}><legend className="sr-only">待提交制作任务</legend>
        {draft.rows.map((item, index) => <div className="production-task-draft" key={item.id}>
          <div className="production-task-draft-heading"><h4>任务 {index + 1}</h4><button type="button" aria-label={`移除任务 ${index + 1}`} disabled={draft.rows.length === 1}
            onClick={() => { create.reset(); setDraft(current => ({ rows: current.rows.filter(value => value.id !== item.id), requestId: crypto.randomUUID() })); }}><X size={16} /></button></div>
          <label>名称<input aria-label={`任务 ${index + 1} 名称`} maxLength={240} required value={item.title} onChange={event => patch(item.id, { title: event.target.value })} /></label>
          <label>制作要求<textarea aria-label={`任务 ${index + 1} 制作要求`} rows={3} maxLength={10000} required value={item.brief} onChange={event => patch(item.id, { brief: event.target.value })} /></label>
          <label>Skill<select aria-label={`任务 ${index + 1} Skill 方式`} value={item.mode} onChange={event => patch(item.id, { mode: event.target.value as TaskDraft["mode"] })}>
            <option value="default">沿用栏目默认</option><option value="single" disabled={!catalog.data || catalog.isError}>指定完整 Skill</option><option value="pair" disabled={!catalog.data || catalog.isError}>指定内容与制图 Skill</option>
          </select></label>
          {item.mode === "default" && <p className="production-task-default">{defaults.length ? defaults.map(skillName).join(" × ") : "使用提交时的栏目默认 Skill"}</p>}
          {item.mode === "single" && <SkillSelect label={`任务 ${index + 1} 完整 Skill`} value={item.single} items={singles} onChange={single => patch(item.id, { single })} />}
          {item.mode === "pair" && <div className="production-task-skill-pair"><SkillSelect label={`任务 ${index + 1} 内容 Skill`} value={item.mind} items={minds} onChange={mind => patch(item.id, { mind })} />
            <SkillSelect label={`任务 ${index + 1} 制图 Skill`} value={item.visual} items={visuals} onChange={visual => patch(item.id, { visual })} /></div>}
        </div>)}
        <button className="button button-quiet" type="button" disabled={draft.rows.length >= 100} onClick={() => {
          create.reset(); setDraft(current => ({ rows: [...current.rows, row()], requestId: crypto.randomUUID() }));
        }}><Plus size={16} aria-hidden="true" />添加任务</button>
      </fieldset>
      {storageFailed && <p className="form-error" role="alert">浏览器无法暂存草稿，请保留此页直到提交结果明确。</p>}
      {(localError || create.isError) && <p className="form-error" role="alert">{localError || create.error?.message}{ambiguous(create.error) ? " 提交结果待核对。内容已保留，不会自动重提；下方按钮只核对同一批任务。" : ""}</p>}
      {!!draft.submitted && !create.isPending && !create.isError && <p role="status" className="muted">有一批提交等待核对，请核对同一提交后继续。</p>}
      <div className="production-task-submit"><button className="button button-primary" disabled={disabled || create.isPending || tasks.isPending || tasks.isError
        || (catalogRequired && (catalog.isPending || catalog.isError)) || !series.is_active || !series.creator_id}>
        {create.isPending ? "正在提交…" : draft.submitted ? "核对同一提交" : `提交并开始制作（${draft.rows.length}）`}</button>
        <small>{!series.creator_id ? "请先为栏目分配账号。" : !series.is_active ? "栏目当前不可制作。" : "草稿暂存在当前标签页；提交后可离开。"}</small></div>
    </form>}
    {create.isSuccess && <p role="status" className="production-task-success">已提交制作，可在这里查看进度。</p>}
  </section>;
}

function SkillSelect({ label, value, items, onChange }: { label: string; value: string; items: ProducerSkillItem[]; onChange: (value: string) => void }) {
  return <label>{label.replace(/^任务 \d+ /, "")}<select aria-label={label} required value={value} onChange={event => onChange(event.target.value)}>
    <option value="">选择 Skill 版本</option>{value && !items.some(skill => skill.id === value) && <option value={value} disabled>原 Skill 暂不可用，请重新选择</option>}
    {items.map(skill => <option key={skill.id} value={skill.id}>{skillName(skill)}</option>)}
  </select>{!items.length && <small>暂无可选版本，请先到 Skill 库准备。</small>}</label>;
}
